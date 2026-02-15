import socket
import struct
import csv
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from threading import Thread
from queue import Queue
from collections import deque
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import time
import os
from shot_classifier import ShotClassifier

# --- Configuration ---
UDP_IP = "0.0.0.0"  # Listen on all available interfaces
UDP_PORT = 12345
LOG_FILE = "sensor_data.csv"
SAMPLES_PER_PACKET = 20
TOF_SAMPLES_PER_PACKET = 5
PLOT_HISTORY_SIZE = int(200*2.5)  # Number of data points to buffer (5 seconds worth)
PLOT_DISPLAY_WINDOW = 5.0  # Display window in seconds (only show last 5s)

# --- Conversion Factors ---
ACCEL_SENSITIVITY = 2048.0  # LSB/g for ±16g range
GYRO_SENSITIVITY = 16.384     # LSB/°/s for ±2000°/s range

# --- GUI ---
class SensorGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ESP32 MPU6050 Data")
        self.geometry("1200x700")

        # Data buffers for plotting
        self.timestamps = deque(maxlen=PLOT_HISTORY_SIZE)  # For MPU data
        self.range_timestamps = deque(maxlen=PLOT_HISTORY_SIZE)  # For TOF data
        self.accel_data = {
            'x': deque(maxlen=PLOT_HISTORY_SIZE),
            'y': deque(maxlen=PLOT_HISTORY_SIZE),
            'z': deque(maxlen=PLOT_HISTORY_SIZE),
            'magnitude': deque(maxlen=PLOT_HISTORY_SIZE)
        }
        self.gyro_data = {
            'x': deque(maxlen=PLOT_HISTORY_SIZE),
            'y': deque(maxlen=PLOT_HISTORY_SIZE),
            'z': deque(maxlen=PLOT_HISTORY_SIZE)
        }
        self.range_data = deque(maxlen=PLOT_HISTORY_SIZE)
        self.signal_rate_data = deque(maxlen=PLOT_HISTORY_SIZE)  # Add signal rate buffer

        # Throttle plot updates to 10 FPS (100ms min interval)
        self.last_plot_update_time = 0
        self.min_plot_update_interval = 0.1  # seconds

        # Recording state
        self.recording = False
        self.log_file_path = LOG_FILE
        
        # Playback state
        self.playback_mode = False  # When True, UDP updates are ignored
        self.playback_data = []
        self.playback_index = 0
        self.playback_paused = False
        self.playback_pause_time = 0  # Track when pause started
        self.playback_thread = None
        self.playback_running = False
        
        # Shot classifier
        self.shot_classifier = ShotClassifier()
        self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}

        self.create_control_panel()
        self.create_plots()

    def create_control_panel(self):
        """Creates the control panel with recording and playback buttons."""
        control_frame = tk.Frame(self)
        control_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        # Left side: Recording and Playback controls
        left_frame = tk.LabelFrame(control_frame, text="Recording & Playback", padx=10, pady=5)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=1, padx=5)

        # Recording buttons
        recording_frame = tk.Frame(left_frame)
        recording_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        
        tk.Label(recording_frame, text="Live Recording:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=5)
        self.record_button = tk.Button(recording_frame, text="Start", command=self.start_recording,
                                       bg="lightgreen", width=10)
        self.record_button.pack(side=tk.LEFT, padx=3)

        self.stop_record_button = tk.Button(recording_frame, text="Stop", command=self.stop_recording,
                                            bg="lightcoral", width=10, state=tk.DISABLED)
        self.stop_record_button.pack(side=tk.LEFT, padx=3)

        # Log file path
        self.log_path_label = tk.Label(recording_frame, text=f"Path: {self.log_file_path}", wraplength=300, font=("Arial", 8))
        self.log_path_label.pack(side=tk.LEFT, padx=5)

        browse_button = tk.Button(recording_frame, text="Browse", command=self.browse_log_path, width=8)
        browse_button.pack(side=tk.LEFT, padx=3)

        # Playback buttons
        playback_frame = tk.Frame(left_frame)
        playback_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)
        
        tk.Label(playback_frame, text="Playback:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=5)
        self.load_button = tk.Button(playback_frame, text="Load File", command=self.load_playback_file,
                                     bg="lightblue", width=10)
        self.load_button.pack(side=tk.LEFT, padx=3)

        self.play_button = tk.Button(playback_frame, text="Play", command=self.play_playback,
                                     bg="lightgreen", width=8, state=tk.DISABLED)
        self.play_button.pack(side=tk.LEFT, padx=3)

        self.pause_button = tk.Button(playback_frame, text="Pause", command=self.pause_playback,
                                      bg="lightyellow", width=8, state=tk.DISABLED)
        self.pause_button.pack(side=tk.LEFT, padx=3)

        self.stop_button = tk.Button(playback_frame, text="Stop", command=self.stop_playback,
                                     bg="lightcoral", width=8, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=3)

        self.restart_button = tk.Button(playback_frame, text="Restart", command=self.restart_playback,
                                        bg="lightgray", width=8, state=tk.DISABLED)
        self.restart_button.pack(side=tk.LEFT, padx=3)

        self.playback_file_label = tk.Label(playback_frame, text="No file loaded", wraplength=200, font=("Arial", 8))
        self.playback_file_label.pack(side=tk.LEFT, padx=5)

        # Center: Status label
        status_frame = tk.Frame(control_frame)
        status_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=0, padx=10)
        tk.Label(status_frame, text="Status:", font=("Arial", 10)).pack(side=tk.TOP, padx=5, pady=3)
        self.status_label = tk.Label(status_frame, text="Live", font=("Arial", 14, "bold"), fg="red")
        self.status_label.pack(side=tk.TOP, padx=5)
        
        # Right side: Shot statistics label (large font)
        stats_frame = tk.Frame(control_frame)
        stats_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=0, padx=20, pady=5)
        tk.Label(stats_frame, text="Shots:", font=("Arial", 12, "bold")).pack(side=tk.TOP, padx=5, pady=3)
        self.stats_label = tk.Label(stats_frame, text="0/0 (0%)", font=("Arial", 28, "bold"), fg="blue")
        self.stats_label.pack(side=tk.TOP, padx=5)

    def browse_log_path(self):
        """Open file dialog to select log file path."""
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if file_path:
            self.log_file_path = file_path
            self.log_path_label.config(text=f"Path: {os.path.basename(file_path)}")

    def start_recording(self):
        """Enable recording of incoming data."""
        self.recording = True
        self.shot_classifier.reset()
        self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
        self.stats_label.config(text="0/0 (0%)")
        self.record_button.config(state=tk.DISABLED)
        self.stop_record_button.config(state=tk.NORMAL)
        print(f"Recording started to: {self.log_file_path}")

    def stop_recording(self):
        """Disable recording of incoming data."""
        self.recording = False
        self.record_button.config(state=tk.NORMAL)
        self.stop_record_button.config(state=tk.DISABLED)
        print("Recording stopped.")

    def load_playback_file(self):
        """Load a CSV file for playback."""
        file_path = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if file_path:
            try:
                self.playback_data = []
                with open(file_path, 'r') as f:
                    csv_reader = csv.reader(f)
                    header = next(csv_reader)  # Skip header
                    for row in csv_reader:
                        if len(row) >= 9:
                            try:
                                mpu_ts = int(row[0])
                                acx, acy, acz = float(row[1]), float(row[2]), float(row[3])
                                gx, gy, gz = float(row[4]), float(row[5]), float(row[6])
                                tof_ts = int(row[7])
                                distance = int(row[8])
                                # Signal rate may be in column 9 (new format) or missing (old format)
                                signal_rate = int(row[9]) if len(row) > 9 else 0
                                
                                # Handle TOF data validation
                                # 0xFFFE (65534) = dummy data, keep as-is
                                # 0xFFFF (65535) = invalid return, set to -1
                                if distance == 0xFFFF or distance == 65535:
                                    distance = -1
                                
                                self.playback_data.append({
                                    'mpu_ts': mpu_ts,
                                    'accel': [acx, acy, acz],
                                    'gyro': [gx, gy, gz],
                                    'tof_ts': tof_ts,
                                    'distance': distance,
                                    'signal_rate': signal_rate
                                })
                            except (ValueError, IndexError):
                                continue
                
                if self.playback_data:
                    self.playback_index = 0
                    self.playback_paused = False
                    self.playback_file_label.config(text=f"Loaded: {os.path.basename(file_path)}")
                    self.play_button.config(state=tk.NORMAL)
                    self.pause_button.config(state=tk.DISABLED)
                    self.stop_button.config(state=tk.DISABLED)
                    self.restart_button.config(state=tk.NORMAL)
                    print(f"Loaded {len(self.playback_data)} samples from {file_path}")
                else:
                    messagebox.showerror("Error", "No valid data found in file.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load file: {str(e)}")

    def play_playback(self):
        """Start or resume playback of loaded data."""
        if not self.playback_data:
            messagebox.showwarning("Warning", "No data loaded for playback.")
            return
        
        # Only clear plot data and reset classifier if this is initial play (not resuming from pause)
        if not self.playback_paused:
            self.timestamps.clear()
            self.range_timestamps.clear()
            for key in self.accel_data:
                self.accel_data[key].clear()
            for key in self.gyro_data:
                self.gyro_data[key].clear()
            self.range_data.clear()
            self.signal_rate_data.clear()
            # Reset shot classifier for new playback session
            self.shot_classifier.reset()
            self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
            self.stats_label.config(text="0/0 (0%)")
            self.canvas.draw_idle()
        
        self.playback_mode = True  # Disable UDP updates
        self.playback_paused = False
        
        # Only start new thread if playback isn't already running
        if not self.playback_running:
            self.playback_running = True
            self.playback_thread = Thread(target=self._playback_worker, daemon=True)
            self.playback_thread.start()
        
        self.play_button.config(state=tk.DISABLED)
        self.pause_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL)
        self.load_button.config(state=tk.DISABLED)
        self.status_label.config(text="Playback", fg="blue")
        print("Playback started/resumed.")

    def _playback_worker(self):
        """Worker thread for playback. Reads 20 samples at a time like UDP packets."""
        if self.playback_index >= len(self.playback_data):
            self.playback_index = 0
        
        while self.playback_running and self.playback_index < len(self.playback_data):
            # Check if paused
            if self.playback_paused:
                time.sleep(0.05)
                continue
            
            # Accumulate 20 samples and process as a batch
            batch_end = min(self.playback_index + SAMPLES_PER_PACKET, len(self.playback_data))
            batch = self.playback_data[self.playback_index:batch_end]
            
            # Update GUI with entire batch at once
            self.after(0, self.update_plots, batch)
            
            self.playback_index = batch_end
            # Delay 100ms between batches to match plot redraw interval
            time.sleep(0.1)
        
        # Playback finished
        if self.playback_running:
            self.after(0, self._playback_finished)

    def _playback_finished(self):
        """Called when playback finishes."""
        self.playback_mode = False  # Re-enable UDP updates
        self.playback_running = False
        self.play_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.load_button.config(state=tk.NORMAL)
        self.status_label.config(text="Live", fg="red")
        print("Playback finished.")

    def pause_playback(self):
        """Pause playback."""
        self.playback_paused = True
        self.playback_pause_time = time.time()  # Record when pause started
        self.play_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        print("Playback paused.")

    def stop_playback(self):
        """Stop playback and return to live recording mode."""
        self.playback_running = False
        self.playback_paused = False
        self.playback_mode = False  # Re-enable UDP updates
        
        # Wait briefly for playback thread to stop
        time.sleep(0.1)
        
        # Clear plot data
        self.timestamps.clear()
        self.range_timestamps.clear()
        for key in self.accel_data:
            self.accel_data[key].clear()
        for key in self.gyro_data:
            self.gyro_data[key].clear()
        self.range_data.clear()
        self.signal_rate_data.clear()
        self.canvas.draw_idle()
        
        # Update button states
        self.play_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.load_button.config(state=tk.NORMAL)
        self.status_label.config(text="Live", fg="red")
        print("Playback stopped. Returning to live recording.")


    def restart_playback(self):
        """Restart playback from the beginning."""
        # Stop current playback if running
        self.playback_running = False
        self.playback_paused = False
        
        # Wait briefly for playback thread to stop
        time.sleep(0.1)
        
        # Clear plot data
        self.timestamps.clear()
        self.range_timestamps.clear()
        for key in self.accel_data:
            self.accel_data[key].clear()
        for key in self.gyro_data:
            self.gyro_data[key].clear()
        self.range_data.clear()
        self.signal_rate_data.clear()
        # Reset shot classifier
        self.shot_classifier.reset()
        self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
        self.stats_label.config(text="0/0 (0%)")
        self.canvas.draw_idle()
        
        # Reset index and start playing from beginning
        self.playback_index = 0
        self.play_playback()


    def create_plots(self):
        """Creates and embeds the matplotlib plots."""
        self.fig, (self.ax_accel, self.ax_gyro, self.ax_range, self.ax_signal_rate) = plt.subplots(4, 1, figsize=(9, 10))

        # Acceleration plot
        self.ax_accel.set_title("Accelerometer Data")
        self.ax_accel.set_ylabel("Acceleration (g)")
        self.accel_lines = {
            'x': self.ax_accel.plot([], [], marker='.', label='AcX')[0],
            'y': self.ax_accel.plot([], [], marker='.', label='AcY')[0],
            'z': self.ax_accel.plot([], [], marker='.', label='AcZ')[0],
            'magnitude': self.ax_accel.plot([], [], marker='.', label='Magnitude')[0]
        }
        self.ax_accel.legend(loc='upper left')
        self.ax_accel.grid(True)

        # Gyroscope plot
        self.ax_gyro.set_title("Gyroscope Data")
        self.ax_gyro.set_ylabel("Angular Velocity (°/s)")
        self.gyro_lines = {
            'x': self.ax_gyro.plot([], [], marker='.', label='GyX')[0],
            'y': self.ax_gyro.plot([], [], marker='.', label='GyY')[0],
            'z': self.ax_gyro.plot([], [], marker='.', label='GyZ')[0]
        }
        self.ax_gyro.legend(loc='upper left')
        self.ax_gyro.grid(True)

        # Range plot
        self.ax_range.set_title("Distance (VL53L1X, -1 = no target)")
        self.ax_range.set_ylabel("Distance (mm)")
        self.range_line = self.ax_range.plot([], [], marker='.', label='Range')[0]
        self.ax_range.legend(loc='upper left')
        self.ax_range.grid(True)

        # Signal rate plot
        self.ax_signal_rate.set_title("Signal Rate (VL53L1X)")
        self.ax_signal_rate.set_xlabel("Time (s)")
        self.ax_signal_rate.set_ylabel("Signal Rate")
        self.signal_rate_line = self.ax_signal_rate.plot([], [], marker='.', label='Signal Rate')[0]
        self.ax_signal_rate.legend(loc='upper left')
        self.ax_signal_rate.grid(True)

        # Create frame for canvas
        frame = tk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=1)

    def update_plots(self, samples):
        """Updates plots with a batch of samples (same for live and playback modes).
        
        Args:
            samples: List of dicts with 'accel', 'gyro', 'distance', 'mpu_ts', 'tof_ts', 'signal_rate'
        """
        # Process batch through shot classifier
        new_shots = self.shot_classifier.process_batch(samples)
        
        # Update statistics
        if new_shots:
            self.shot_stats = self.shot_classifier.get_statistics()
            makes = self.shot_stats['makes']
            total = self.shot_stats['total']
            pct = self.shot_stats['percentage']
            self.stats_label.config(text=f"{makes}/{total} ({pct:.0f}%)")
            for shot in new_shots:
                impact_time = shot['impact_time'] if shot['impact_time'] is not None else shot['basket_time']
                if shot['classification'] == 'MAKE':
                    basket_type = shot.get('basket_type', 'Unknown')
                    print(f"🏀 Shot: {shot['classification']} ({basket_type}) @ {impact_time:.3f}s (confidence: {shot['confidence']:.2f})")
                else:
                    print(f"🏀 Shot: {shot['classification']} @ {impact_time:.3f}s (confidence: {shot['confidence']:.2f})")
        
        # Add all samples to buffers
        for sample in samples:
            timestamp = sample['mpu_ts']
            accel = sample['accel']
            gyro = sample['gyro']
            distance = sample['distance']
            tof_timestamp = sample['tof_ts']
            signal_rate = sample['signal_rate']
            
            timestamp_sec = timestamp / 1000.0
            self.timestamps.append(timestamp_sec)
            
            # Append new data
            self.accel_data['x'].append(accel[0])
            self.accel_data['y'].append(accel[1])
            self.accel_data['z'].append(accel[2])
            magnitude = (accel[0]**2 + accel[1]**2 + accel[2]**2)**0.5
            self.accel_data['magnitude'].append(magnitude)
            
            self.gyro_data['x'].append(gyro[0])
            self.gyro_data['y'].append(gyro[1])
            self.gyro_data['z'].append(gyro[2])
            
            # Only plot valid TOF data
            if distance != 0xFFFE and distance != 65534:
                if tof_timestamp is not None:
                    self.range_timestamps.append(tof_timestamp / 1000.0)
                else:
                    self.range_timestamps.append(timestamp_sec)
                if distance == 0xFFFF or distance == 65535:
                    self.range_data.append(-1)
                else:
                    self.range_data.append(distance)
                
                if signal_rate is not None:
                    self.signal_rate_data.append(signal_rate)
                else:
                    self.signal_rate_data.append(0)
        
        # Trim old data: keep only the last 5 seconds
        if len(self.timestamps) > 0:
            current_time = self.timestamps[-1]
            min_time = current_time - PLOT_DISPLAY_WINDOW
            
            while len(self.timestamps) > 0 and self.timestamps[0] < min_time:
                self.timestamps.popleft()
                self.accel_data['x'].popleft()
                self.accel_data['y'].popleft()
                self.accel_data['z'].popleft()
                self.accel_data['magnitude'].popleft()
                self.gyro_data['x'].popleft()
                self.gyro_data['y'].popleft()
                self.gyro_data['z'].popleft()
        
        if len(self.range_timestamps) > 0:
            current_range_time = self.range_timestamps[-1]
            min_range_time = current_range_time - PLOT_DISPLAY_WINDOW
            while len(self.range_timestamps) > 0 and self.range_timestamps[0] < min_range_time:
                self.range_timestamps.popleft()
                self.range_data.popleft()
                self.signal_rate_data.popleft()
        
        # Update plot once for the entire batch
        for axis in ['x', 'y', 'z', 'magnitude']:
            self.accel_lines[axis].set_data(self.timestamps, self.accel_data[axis])
        for axis in ['x', 'y', 'z']:
            self.gyro_lines[axis].set_data(self.timestamps, self.gyro_data[axis])
        
        self.range_line.set_data(self.range_timestamps, self.range_data)
        self.signal_rate_line.set_data(self.range_timestamps, self.signal_rate_data)
        
        # Determine the time range based on MPU data (which is trimmed to 5 seconds)
        # This ensures all plots display the same time window
        if len(self.timestamps) > 0:
            time_min = self.timestamps[0]
            time_max = self.timestamps[-1]
            # Add 5% padding to both sides
            time_range = time_max - time_min if time_max > time_min else 1
            time_min -= time_range * 0.05
            time_max += time_range * 0.05
        else:
            time_min, time_max = 0, 1
        
        # Rescale axes with synchronized x-limits
        self.ax_accel.relim()
        self.ax_accel.autoscale_view()
        self.ax_accel.set_xlim(time_min, time_max)
        
        self.ax_gyro.relim()
        self.ax_gyro.autoscale_view()
        self.ax_gyro.set_xlim(time_min, time_max)
        
        self.ax_range.relim()
        self.ax_range.autoscale_view()
        self.ax_range.set_xlim(time_min, time_max)
        
        self.ax_signal_rate.relim()
        self.ax_signal_rate.autoscale_view()
        self.ax_signal_rate.set_xlim(time_min, time_max)
        
        # Clear previous shot event lines from all axes
        for line in self.ax_accel.get_lines()[4:]:  # Skip first 4 (data lines)
            line.remove()
        for line in self.ax_gyro.get_lines()[3:]:  # Skip first 3 (data lines)
            line.remove()
        for line in self.ax_range.get_lines()[1:]:  # Skip first (data line)
            line.remove()
        for line in self.ax_signal_rate.get_lines()[1:]:  # Skip first (data line)
            line.remove()
        
        # Plot shot events on all 4 plots
        all_shots = self.shot_classifier.get_all_shots()
        for shot in all_shots:
            if shot['classification'] == 'MAKE':
                # Red vertical line at basket_time on all plots
                basket_time = shot['basket_time']
                self.ax_accel.axvline(x=basket_time, color='red', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_gyro.axvline(x=basket_time, color='red', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_range.axvline(x=basket_time, color='red', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_signal_rate.axvline(x=basket_time, color='red', linestyle='--', linewidth=2, alpha=0.7)
            elif shot['classification'] == 'MISS':
                # Blue vertical line at impact_time on all plots
                impact_time = shot['impact_time']
                self.ax_accel.axvline(x=impact_time, color='blue', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_gyro.axvline(x=impact_time, color='blue', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_range.axvline(x=impact_time, color='blue', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_signal_rate.axvline(x=impact_time, color='blue', linestyle='--', linewidth=2, alpha=0.7)
        
        self.canvas.draw_idle()


# --- Data Receiver and Logger ---
class DataReceiver:
    def __init__(self, gui):
        self.gui = gui
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Allow reusing the socket address and port
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            # SO_REUSEPORT not available on all systems
            pass
        
        # Set socket timeout to avoid blocking forever
        self.sock.settimeout(1.0)
        
        # Retry binding with delays to handle TIME_WAIT state
        max_retries = 5
        for attempt in range(max_retries):
            try:
                self.sock.bind((UDP_IP, UDP_PORT))
                print(f"Listening on {UDP_IP}:{UDP_PORT}")
                break
            except OSError as e:
                if attempt < max_retries - 1:
                    print(f"Port {UDP_PORT} in use, retrying in 2 seconds... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(2)
                else:
                    print(f"Failed to bind to port {UDP_PORT} after {max_retries} attempts")
                    raise

        self.log_file = None
        self.csv_writer = None
        self._init_log_file()
        
        # Data queue for passing packets from receiver thread to processor thread
        self.packet_queue = Queue(maxsize=100)
        self.running = True

    def _init_log_file(self):
        """Initialize or reinitialize the log file."""
        log_path = self.gui.log_file_path
        if self.log_file:
            self.log_file.close()
        
        self.log_file = open(log_path, "w", newline="")
        self.csv_writer = csv.writer(self.log_file)
        self.csv_writer.writerow([
            "MPU_Timestamp (ms)", "AcX (g)", "AcY (g)", "AcZ (g)", "GyX (dps)", "GyY (dps)", "GyZ (dps)", "TOF_Timestamp (ms)", "Range (mm)", "Signal_Rate"
        ])
        self.log_file.flush()  # Flush header immediately

    def receive_data(self):
        """Receives UDP packets and queues them for processing.
        
        This thread runs as fast as possible to receive packets.
        Blocking operations (CSV I/O, GUI updates) are done in a separate thread.
        """
        packets_received = 0
        packets_dropped = 0
        last_print_time = time.time()
        packets_since_last_print = 0
        
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                packets_received += 1
                packets_since_last_print += 1
                
                try:
                    # Try to queue the packet without blocking
                    # If queue is full, drop the packet to prevent blocking
                    self.packet_queue.put_nowait(data)
                except:
                    packets_dropped += 1
                    if packets_dropped % 10 == 0:
                        print(f"⚠️  Dropped {packets_dropped} packets (queue full). Receiver may be too slow.")
                
                # Print frequency every second
                current_time = time.time()
                if current_time - last_print_time >= 1.0:
                    frequency = packets_since_last_print / (current_time - last_print_time)
                    print(f"📊 Packet RX frequency: {frequency:.1f} Hz ({packets_since_last_print} packets/sec) | Total: {packets_received} | Dropped: {packets_dropped}")
                    packets_since_last_print = 0
                    last_print_time = current_time
                        
            except socket.timeout:
                # Timeout is expected when no data is available
                continue
            except Exception as e:
                print(f"Error receiving data: {e}")

    def process_data(self):
        """Processes queued packets: parses, logs to CSV, and updates GUI.
        
        This runs in a separate thread to avoid blocking the receiver thread.
        """
        while self.running:
            try:
                # Get packet with timeout to check running flag periodically
                data = self.packet_queue.get(timeout=0.1)
                
                # Parse the packet
                packet_timestamp = struct.unpack('!I', data[0:4])[0]
                num_mpu_samples = struct.unpack('!B', data[4:5])[0]
                
                # Parse MPU6050 data with timestamp deltas
                mpu_sensor_data = []
                for i in range(num_mpu_samples):
                    offset = 5 + i * 14
                    timestamp_delta = struct.unpack('!H', data[offset:offset+2])[0]
                    sample = struct.unpack('!hhhhhh', data[offset+2:offset+14])
                    
                    # Reconstruct sample timestamp
                    sample_timestamp = packet_timestamp - timestamp_delta
                    
                    # Convert to physical units
                    accel = [s / ACCEL_SENSITIVITY for s in sample[0:3]]
                    gyro = [s / GYRO_SENSITIVITY for s in sample[3:6]]
                    
                    mpu_sensor_data.append((accel, gyro, sample_timestamp))
                
                # Parse VL53L1X data with timestamp deltas
                tof_offset = 5 + num_mpu_samples * 14
                num_tof_samples = struct.unpack('!B', data[tof_offset:tof_offset+1])[0]
                tof_data = []
                for i in range(8):  # Always 8 slots in fixed packet
                    offset = tof_offset + 1 + i * 6
                    timestamp_delta = struct.unpack('!H', data[offset:offset+2])[0]
                    distance = struct.unpack('!H', data[offset+2:offset+4])[0]
                    signal_rate = struct.unpack('!H', data[offset+4:offset+6])[0]
                    
                    # Only process if this is a valid sample (within num_tof_samples)
                    if i < num_tof_samples:
                        sample_timestamp = packet_timestamp - timestamp_delta
                        tof_data.append((distance, sample_timestamp, signal_rate))
                
                # Log all MPU samples with available TOF data (only if recording)
                if self.gui.recording:
                    for i, (accel, gyro, mpu_ts) in enumerate(mpu_sensor_data):
                        if i < len(tof_data):
                            distance, tof_ts, signal_rate = tof_data[i]
                        else:
                            distance = 0xFFFE  # No TOF data available
                            signal_rate = 0
                            tof_ts = mpu_ts    # Use MPU timestamp as reference
                        self.csv_writer.writerow([mpu_ts] + accel + gyro + [tof_ts, distance, signal_rate])
                    
                    # Flush CSV file periodically (every 10 packets) instead of every sample
                    # This reduces I/O overhead significantly
                    # (10 packets × 20 samples/packet = 200 samples before flush)
                    if packet_timestamp % 10 == 0:
                        self.log_file.flush()
                
                print(f"Received packet: {num_mpu_samples} MPU samples, {num_tof_samples} TOF samples")
                print(f">>> TOF Range values (mm): {[d for d, _, _ in tof_data]}")
                
                # Update GUI with all MPU samples paired with TOF data where available (thread-safe via after())
                # Skip updates if playback is active
                if not self.gui.playback_mode:
                    batch = []
                    for i, (accel, gyro, mpu_ts) in enumerate(mpu_sensor_data):
                        if i < len(tof_data):
                            distance, tof_ts, signal_rate = tof_data[i]
                        else:
                            distance = 0xFFFE
                            tof_ts = None
                            signal_rate = None
                        batch.append({
                            'accel': accel,
                            'gyro': gyro,
                            'distance': distance,
                            'mpu_ts': mpu_ts,
                            'tof_ts': tof_ts,
                            'signal_rate': signal_rate
                        })
                    self.gui.after(0, self.gui.update_plots, batch)

            except Exception as e:
                # Queue timeout is expected, but print other errors
                if "Empty" not in str(type(e).__name__):
                    print(f"❌ Error processing packet: {type(e).__name__}: {e}")
                    import traceback
                    traceback.print_exc()
                continue

    def start(self):
        """Start receiver and processor threads."""
        receiver_thread = Thread(target=self.receive_data, daemon=True)
        receiver_thread.start()
        
        processor_thread = Thread(target=self.process_data, daemon=True)
        processor_thread.start()

    def close(self):
        self.running = False
        self.sock.close()
        if self.log_file:
            self.log_file.close()

# --- Main ---
if __name__ == "__main__":
    gui = SensorGui()
    receiver = DataReceiver(gui)
    receiver.start()
    try:
        gui.mainloop()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt detected. Closing...")
    finally:
        receiver.close()
        print("Receiver closed.")