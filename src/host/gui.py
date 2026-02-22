"""
GUI for the basketball shot counter.
Displays real-time sensor data in 4 plots and manages recording/playback.
Includes optional USB camera video display.
"""

import tkinter as tk
from tkinter import filedialog, messagebox
from threading import Thread
from collections import deque
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import time
import os
import csv
import bisect
import subprocess
import datetime
import cv2
import numpy as np
from PIL import Image, ImageTk
from datetime import datetime

from config import PLOT_HISTORY_SIZE, PLOT_DISPLAY_WINDOW, LOG_FILE, SAMPLES_PER_PACKET
from shot_classifier import ShotClassifier


class SensorGui(tk.Tk):
    """Main GUI window for sensor visualization and shot classification."""
    
    def __init__(self):
        super().__init__()
        self.title("ESP32 Basketball Shot Counter")
        self.geometry("2200x800")

        # Data buffers for plotting
        self.timestamps = deque(maxlen=PLOT_HISTORY_SIZE)
        self.range_timestamps = deque(maxlen=PLOT_HISTORY_SIZE)
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
        self.signal_rate_data = deque(maxlen=PLOT_HISTORY_SIZE)

        # Throttle plot updates to 10 FPS (100ms min interval)
        self.last_plot_update_time = 0
        self.min_plot_update_interval = 0.1  # seconds
        self.pending_samples = []  # Buffer samples that arrive between plot updates

        # Recording state
        self.recording = False
        self.log_file_path = LOG_FILE
        
        # Playback state
        self.playback_mode = False
        self.playback_data = []
        self.playback_index = 0
        self.playback_paused = False
        self.playback_pause_time = 0
        self.playback_thread = None
        self.playback_running = False
        
        # Shot classifier
        self.shot_classifier = ShotClassifier()
        self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
        
        # Camera display
        self.camera_manager = None  # Will be set by main.py
        self.current_camera_image = None
        self.camera_label = None
        self.playback_frames_dir = None  # Set when loading playback file
        self.playback_frame_index = []   # List of (ts_ms, filename), sorted by ts_ms
        self.playback_frame_ts = []      # Parallel list of timestamps for bisect

        self.create_control_panel()
        self.create_main_layout()

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
        tk.Button(stats_frame, text="Clear Score", command=self.clear_score,
                  bg="lightyellow", width=12).pack(side=tk.TOP, padx=5, pady=3)

    def clear_score(self):
        """Reset shot statistics and classifier state without stopping recording or playback."""
        self.shot_classifier.reset()
        self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
        self.stats_label.config(text="0/0 (0%)")
        print("Score cleared.")

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
        # Generate a new timestamped filename for each session
        ts = datetime.now().strftime("%Y%m%d%H%M")
        base_dir = os.path.dirname(self.log_file_path) or "."
        self.log_file_path = os.path.join(base_dir, f"{ts}_sensor_data.csv")
        self.log_path_label.config(text=f"Path: {os.path.basename(self.log_file_path)}")

        # Reinitialize the CSV file and camera recording directory
        if hasattr(self, 'receiver') and self.receiver:
            self.receiver.last_frame_id = -1
            self.receiver.packets_processed = 0
            self.receiver._init_log_file()

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
        # Request data receiver to flush CSV file
        if hasattr(self, 'receiver') and self.receiver and self.receiver.log_file:
            self.receiver.log_file.flush()
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
                self.playback_frames_dir = None
                self.playback_frame_index = []
                self.playback_frame_ts = []
                
                # Check if frames directory exists (new format with camera)
                base_dir = os.path.dirname(file_path)
                csv_basename = os.path.basename(file_path)
                session_name = csv_basename.split('_')[0] if '_' in csv_basename else ""
                # Prefer timestamped frames dir (e.g. 202602211411_frames), fall back to generic "frames"
                candidates = []
                if session_name:
                    candidates.append(os.path.join(base_dir, f"{session_name}_frames"))
                candidates.append(os.path.join(base_dir, "frames"))
                for candidate in candidates:
                    if os.path.exists(candidate):
                        self.playback_frames_dir = candidate
                        break
                if self.playback_frames_dir:
                    frame_entries = []
                    for fname in os.listdir(self.playback_frames_dir):
                        # Expected format: frame_NNNNNN_<timestamp>ms.jpg
                        if fname.startswith('frame_') and fname.endswith('ms.jpg'):
                            try:
                                ts_str = fname.rsplit('_', 1)[1].replace('ms.jpg', '')
                                frame_entries.append((int(ts_str), fname))
                            except (ValueError, IndexError):
                                continue
                    frame_entries.sort()
                    self.playback_frame_index = frame_entries
                    self.playback_frame_ts = [e[0] for e in frame_entries]
                    print(f"✓ Found camera frames in: {self.playback_frames_dir} ({len(frame_entries)} frames indexed by timestamp)")
                
                with open(file_path, 'r') as f:
                    csv_reader = csv.reader(f)
                    next(csv_reader)  # Skip header
                    for row in csv_reader:
                        if len(row) >= 9:
                            try:
                                mpu_ts = int(row[0])
                                acx, acy, acz = float(row[1]), float(row[2]), float(row[3])
                                gx, gy, gz = float(row[4]), float(row[5]), float(row[6])
                                tof_ts = int(row[7])
                                distance = int(row[8])
                                signal_rate = int(row[9]) if len(row) > 9 else 0
                                host_ts_udp = int(row[10]) if len(row) > 10 else -1
                                frame_id = int(row[12]) if len(row) > 12 else -1
                                
                                # Handle TOF data validation
                                if distance == 0xFFFF or distance == 65535:
                                    distance = -1
                                
                                self.playback_data.append({
                                    'mpu_ts': mpu_ts,
                                    'accel': [acx, acy, acz],
                                    'gyro': [gx, gy, gz],
                                    'tof_ts': tof_ts,
                                    'distance': distance,
                                    'signal_rate': signal_rate,
                                    'frame_id': frame_id,
                                    'host_ts_udp': host_ts_udp
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
            self._clear_plot_data()
            self.shot_classifier.reset()
            self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
            self.stats_label.config(text="0/0 (0%)")
            self.canvas.draw_idle()
        
        self.playback_mode = True
        self.playback_paused = False
        
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
        """Worker thread for playback."""
        if self.playback_index >= len(self.playback_data):
            self.playback_index = 0
        
        while self.playback_running and self.playback_index < len(self.playback_data):
            if self.playback_paused:
                time.sleep(0.05)
                continue
            
            # Accumulate SAMPLES_PER_PACKET samples and process as a batch
            batch_end = min(self.playback_index + SAMPLES_PER_PACKET, len(self.playback_data))
            batch = self.playback_data[self.playback_index:batch_end]
            
            self.after(0, self.update_plots, batch)
            
            self.playback_index = batch_end
            time.sleep(0.1)
        
        if self.playback_running:
            self.after(0, self._playback_finished)

    def _playback_finished(self):
        """Called when playback finishes."""
        self.playback_mode = False
        self.playback_running = False
        self.canvas.draw_idle()
        self.play_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.load_button.config(state=tk.NORMAL)
        self.status_label.config(text="Live", fg="red")
        print("Playback finished.")

    def pause_playback(self):
        """Pause playback."""
        self.playback_paused = True
        self.playback_pause_time = time.time()
        self.play_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        print("Playback paused.")

    def stop_playback(self):
        """Stop playback and return to live recording mode."""
        self.playback_running = False
        self.playback_paused = False
        self.playback_mode = False
        
        time.sleep(0.1)
        
        self._clear_plot_data()
        self.canvas.draw_idle()
        
        self.play_button.config(state=tk.NORMAL)
        self.pause_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.DISABLED)
        self.load_button.config(state=tk.NORMAL)
        self.status_label.config(text="Live", fg="red")
        print("Playback stopped. Returning to live recording.")

    def restart_playback(self):
        """Restart playback from the beginning."""
        self.playback_running = False
        self.playback_paused = False
        
        time.sleep(0.1)
        
        self._clear_plot_data()
        self.shot_classifier.reset()
        self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
        self.stats_label.config(text="0/0 (0%)")
        self.canvas.draw_idle()
        
        self.playback_index = 0
        self.play_playback()

    def _clear_plot_data(self):
        """Clear all plot data buffers."""
        self.timestamps.clear()
        self.range_timestamps.clear()
        for key in self.accel_data:
            self.accel_data[key].clear()
        for key in self.gyro_data:
            self.gyro_data[key].clear()
        self.range_data.clear()
        self.signal_rate_data.clear()

    def create_main_layout(self):
        """Creates the main layout with plots on left and camera on right."""
        main_frame = tk.Frame(self)
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        main_frame.columnconfigure(0, weight=1)  # Plots column (50%)
        main_frame.columnconfigure(1, weight=1)  # Camera column (50%)
        
        # Left side: plots
        left_frame = tk.Frame(main_frame)
        left_frame.grid(row=0, column=0, sticky='nsew', padx=5, pady=5)
        
        self.create_plots(parent=left_frame)
        
        # Right side: camera display
        right_frame = tk.Frame(main_frame, bg='black')
        right_frame.grid(row=0, column=1, sticky='nsew', padx=5, pady=5)
        right_frame.rowconfigure(1, weight=1)  # Make label expand vertically
        right_frame.columnconfigure(0, weight=1)  # Make label expand horizontally
        
        tk.Label(right_frame, text="Camera Feed", font=("Arial", 10, "bold"), bg='black', fg='white').grid(row=0, column=0, sticky='ew')
        self.camera_label = tk.Label(right_frame, bg='black')
        self.camera_label.grid(row=1, column=0, sticky='nsew', padx=5, pady=5)

    def create_plots(self, parent=None):
        """Creates and embeds the matplotlib plots."""
        if parent is None:
            parent = self
            
        self.fig, (self.ax_accel, self.ax_gyro, self.ax_range, self.ax_signal_rate) = plt.subplots(4, 1, figsize=(8, 10))

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
        frame = tk.Frame(parent)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=1)

    def update_plots(self, samples):
        """
        Updates plots with a batch of samples.
        
        Args:
            samples: List of dicts with 'accel', 'gyro', 'distance', 'mpu_ts', 'tof_ts', 'signal_rate', 'frame_id'
        """
        # Buffer samples that arrive between plot updates (don't drop them!)
        self.pending_samples.extend(samples)
        
        # Check if it's time to update the plot
        current_time = time.time()
        if current_time - self.last_plot_update_time < self.min_plot_update_interval:
            return  # Not time yet, samples are buffered in self.pending_samples
        
        # Time to plot! Process all pending samples
        self.last_plot_update_time = current_time
        samples_to_process = self.pending_samples
        self.pending_samples = []  # Clear buffer
        # Process batch through shot classifier
        new_shots = self.shot_classifier.process_batch(samples_to_process)
        
        # Update statistics
        if new_shots:
            self.shot_stats = self.shot_classifier.get_statistics()
            makes = self.shot_stats['makes']
            total = self.shot_stats['total']
            pct = self.shot_stats['percentage']
            self.stats_label.config(text=f"{makes}/{total} ({pct:.0f}%)")
            for shot in new_shots:
                impact_time = shot['impact_time'] if shot['impact_time'] is not None else shot['basket_time']
                score_text = f"{makes} out of {total}"
                if shot['classification'] == 'MAKE':
                    basket_type = shot.get('basket_type', 'Unknown')
                    print(f"🏀 Shot: {shot['classification']} ({basket_type}) @ {impact_time:.3f}s (confidence: {shot['confidence']:.2f})")
                    if basket_type == 'SWISH':
                        self._speak(f'Great Swish. {score_text}')
                    else:
                        self._speak(f'Made. {score_text}')
                else:
                    print(f"🏀 Shot: {shot['classification']} @ {impact_time:.3f}s (confidence: {shot['confidence']:.2f})")
                    self._speak(f'Miss. {score_text}')
        
        # Handle camera frame display (for live mode)
        if not self.playback_mode and self.camera_manager and self.camera_manager.is_available:
            # Display live camera frame (always get the latest)
            frame, _, _ = self.camera_manager.get_current_frame()
            if frame is not None:
                self._display_camera_frame(frame)
        
        # Update date/time suptitle above plots (live and playback)
        if self.playback_mode and samples_to_process:
            # Use host UDP timestamp from the most recent sample
            for s in reversed(samples_to_process):
                host_ts = s.get('host_ts_udp', -1)
                if host_ts > 0:
                    dt = datetime.fromtimestamp(host_ts / 1000)
                    self.fig.suptitle(dt.strftime('%Y-%m-%d  %H:%M:%S'),
                                      fontsize=10, fontweight='bold', y=1.0)
                    break
        else:
            # Live mode: use current wall-clock time
            self.fig.suptitle(datetime.now().strftime('%Y-%m-%d  %H:%M:%S'),
                              fontsize=10, fontweight='bold', y=1.0)

        # Handle camera frame display (for playback mode)
        if self.playback_mode and self.playback_frames_dir and self.playback_frame_index and len(samples_to_process) > 0:
            for sample in samples_to_process:
                host_ts = sample.get('host_ts_udp', -1)
                if host_ts > 0:
                    # Pick the last frame whose timestamp does not exceed host_ts
                    idx = bisect.bisect_right(self.playback_frame_ts, host_ts) - 1
                    if idx >= 0:
                        _, fname = self.playback_frame_index[idx]
                        frame_path = os.path.join(self.playback_frames_dir, fname)
                        if os.path.exists(frame_path):
                            frame = cv2.imread(frame_path)
                            if frame is not None:
                                self._display_camera_frame(frame)
                    break
        
        # Add all samples to buffers
        for sample in samples_to_process:
            timestamp = sample['mpu_ts']
            accel = sample['accel']
            gyro = sample['gyro']
            distance = sample['distance']
            tof_timestamp = sample['tof_ts']
            signal_rate = sample['signal_rate']
            
            timestamp_sec = timestamp / 1000.0
            self.timestamps.append(timestamp_sec)
            
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
        
        # Update plot lines
        for axis in ['x', 'y', 'z', 'magnitude']:
            self.accel_lines[axis].set_data(self.timestamps, self.accel_data[axis])
        for axis in ['x', 'y', 'z']:
            self.gyro_lines[axis].set_data(self.timestamps, self.gyro_data[axis])
        
        self.range_line.set_data(self.range_timestamps, self.range_data)
        self.signal_rate_line.set_data(self.range_timestamps, self.signal_rate_data)
        
        # Determine time range based on MPU data
        if len(self.timestamps) > 0:
            time_min = self.timestamps[0]
            time_max = self.timestamps[-1]
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
        for line in self.ax_accel.get_lines()[4:]:
            line.remove()
        for line in self.ax_gyro.get_lines()[3:]:
            line.remove()
        for line in self.ax_range.get_lines()[1:]:
            line.remove()
        for line in self.ax_signal_rate.get_lines()[1:]:
            line.remove()
        
        # Plot shot events on all 4 plots
        all_shots = self.shot_classifier.get_all_shots()
        for shot in all_shots:
            if shot['classification'] == 'MAKE':
                basket_time = shot['basket_time']
                self.ax_accel.axvline(x=basket_time, color='red', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_gyro.axvline(x=basket_time, color='red', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_range.axvline(x=basket_time, color='red', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_signal_rate.axvline(x=basket_time, color='red', linestyle='--', linewidth=2, alpha=0.7)
            elif shot['classification'] == 'MISS':
                impact_time = shot['impact_time']
                self.ax_accel.axvline(x=impact_time, color='blue', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_gyro.axvline(x=impact_time, color='blue', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_range.axvline(x=impact_time, color='blue', linestyle='--', linewidth=2, alpha=0.7)
                self.ax_signal_rate.axvline(x=impact_time, color='blue', linestyle='--', linewidth=2, alpha=0.7)
        
        self.canvas.draw_idle()    
    def _speak(self, text):
        """Speak text asynchronously using spd-say (non-blocking)."""
        try:
            subprocess.Popen(['spd-say', text],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            pass  # Audio not critical; silently ignore if spd-say unavailable

    def _display_camera_frame(self, frame):
        """Display a camera frame in the camera label."""
        if frame is None or self.camera_label is None:
            return
        
        try:
            # Get actual label dimensions
            label_width = self.camera_label.winfo_width()
            label_height = self.camera_label.winfo_height()
            
            # If label not yet rendered, use reasonable defaults
            if label_width <= 1:
                label_width = 1024
            if label_height <= 1:
                label_height = 720
            
            # Calculate resize dimensions to fit label while maintaining aspect ratio
            h, w = frame.shape[:2]
            aspect = w / h
            
            # Fit to label dimensions
            if (label_width / label_height) > aspect:
                # Label is wider, fit to height
                target_h = label_height
                target_w = int(target_h * aspect)
            else:
                # Label is taller, fit to width
                target_w = label_width
                target_h = int(target_w / aspect)
            
            resized = cv2.resize(frame, (target_w, target_h))
            
            # Convert BGR to RGB for Tkinter
            rgb_frame = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            
            # Convert to PIL Image then PhotoImage
            pil_image = Image.fromarray(rgb_frame)
            photo = ImageTk.PhotoImage(pil_image)
            
            # Update label
            self.camera_label.config(image=photo)
            self.camera_label.image = photo  # Keep reference
        except Exception as e:
            print(f"⚠️  Error displaying camera frame: {e}")