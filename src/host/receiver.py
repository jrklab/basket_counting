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

# --- Configuration ---
UDP_IP = "0.0.0.0"  # Listen on all available interfaces
UDP_PORT = 12345
LOG_FILE = "sensor_data.csv"
SAMPLES_PER_PACKET = 20
TOF_SAMPLES_PER_PACKET = 5
PLOT_HISTORY_SIZE = 500  # Number of data points to buffer (5 seconds worth)
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

        self.create_control_panel()
        self.create_plots()

    def create_control_panel(self):
        """Creates the control panel with recording and playback buttons."""
        control_frame = tk.Frame(self)
        control_frame.pack(side=tk.TOP, fill=tk.X, padx=5, pady=5)

        # Left side: Recording controls
        left_frame = tk.LabelFrame(control_frame, text="Live Recording", padx=10, pady=5)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=1, padx=5)

        # Recording buttons
        self.record_button = tk.Button(left_frame, text="Start Recording", command=self.start_recording,
                                       bg="lightgreen", width=15)
        self.record_button.pack(side=tk.LEFT, padx=5)

        self.stop_record_button = tk.Button(left_frame, text="Stop Recording", command=self.stop_recording,
                                            bg="lightcoral", width=15, state=tk.DISABLED)
        self.stop_record_button.pack(side=tk.LEFT, padx=5)

        # Log file path
        self.log_path_label = tk.Label(left_frame, text=f"Path: {self.log_file_path}", wraplength=300)
        self.log_path_label.pack(side=tk.LEFT, padx=5)

        browse_button = tk.Button(left_frame, text="Browse", command=self.browse_log_path, width=10)
        browse_button.pack(side=tk.LEFT, padx=5)

        # Center: Status label
        status_frame = tk.Frame(control_frame)
        status_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=0, padx=10)
        tk.Label(status_frame, text="Status:", font=("Arial", 10)).pack(side=tk.LEFT, padx=5)
        self.status_label = tk.Label(status_frame, text="Live", font=("Arial", 10, "bold"), fg="red")
        self.status_label.pack(side=tk.LEFT, padx=5)

        # Right side: Playback controls
        right_frame = tk.LabelFrame(control_frame, text="Playback", padx=10, pady=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=1, padx=5)

        self.load_button = tk.Button(right_frame, text="Load File", command=self.load_playback_file,
                                     bg="lightblue", width=12)
        self.load_button.pack(side=tk.LEFT, padx=5)

        self.play_button = tk.Button(right_frame, text="Play", command=self.play_playback,
                                     bg="lightgreen", width=8, state=tk.DISABLED)
        self.play_button.pack(side=tk.LEFT, padx=3)

        self.pause_button = tk.Button(right_frame, text="Pause", command=self.pause_playback,
                                      bg="lightyellow", width=8, state=tk.DISABLED)
        self.pause_button.pack(side=tk.LEFT, padx=3)

        self.stop_button = tk.Button(right_frame, text="Stop", command=self.stop_playback,
                                     bg="lightcoral", width=8, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=3)

        self.restart_button = tk.Button(right_frame, text="Restart", command=self.restart_playback,
                                        bg="lightgray", width=8, state=tk.DISABLED)
        self.restart_button.pack(side=tk.LEFT, padx=3)

        self.playback_file_label = tk.Label(right_frame, text="No file loaded", wraplength=200)
        self.playback_file_label.pack(side=tk.LEFT, padx=5)

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
                                
                                # Handle TOF data validation
                                # 0xFFFE (65534) = dummy data, skip it
                                if distance == 0xFFFE or distance == 65534:
                                    continue
                                # 0xFFFF (65535) = invalid return, set to -1
                                if distance == 0xFFFF or distance == 65535:
                                    distance = -1
                                
                                self.playback_data.append({
                                    'mpu_ts': mpu_ts,
                                    'accel': [acx, acy, acz],
                                    'gyro': [gx, gy, gz],
                                    'tof_ts': tof_ts,
                                    'distance': distance
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
        
        # Only clear plot data if this is initial play (not resuming from pause)
        if not self.playback_paused:
            self.timestamps.clear()
            self.range_timestamps.clear()
            for key in self.accel_data:
                self.accel_data[key].clear()
            for key in self.gyro_data:
                self.gyro_data[key].clear()
            self.range_data.clear()
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
        """Worker thread for playback."""
        if self.playback_index >= len(self.playback_data):
            self.playback_index = 0
        
        start_time = time.time()
        start_sample_time = self.playback_data[self.playback_index]['mpu_ts']
        total_pause_duration = 0  # Track cumulative pause time
        last_pause_time = 0
        
        while self.playback_running and self.playback_index < len(self.playback_data):
            # Check if paused
            if self.playback_paused:
                if last_pause_time == 0:
                    last_pause_time = time.time()
                time.sleep(0.05)
                continue
            else:
                # If we just resumed from a pause, add the pause duration to total_pause_duration
                if last_pause_time > 0:
                    pause_duration = time.time() - last_pause_time
                    total_pause_duration += pause_duration
                    last_pause_time = 0
            
            sample = self.playback_data[self.playback_index]
            elapsed_time = time.time() - start_time - total_pause_duration
            sample_elapsed_time = (sample['mpu_ts'] - start_sample_time) / 1000.0
            
            # Wait until it's time to show this sample
            wait_time = sample_elapsed_time - elapsed_time
            if wait_time > 0:
                time.sleep(wait_time)
            
            # Update GUI for each sample (same as live mode)
            self.after(0, self.update_plots, sample['accel'], sample['gyro'], 
                      sample['distance'], sample['mpu_ts'], sample['tof_ts'])
            
            self.playback_index += 1
        
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
        self.canvas.draw_idle()
        
        # Reset index and start playing from beginning
        self.playback_index = 0
        self.play_playback()


    def create_plots(self):
        """Creates and embeds the matplotlib plots."""
        self.fig, (self.ax_accel, self.ax_gyro, self.ax_range) = plt.subplots(3, 1, figsize=(9, 8))

        # Acceleration plot
        self.ax_accel.set_title("Accelerometer Data, Subtracting 1g from Z-axis")
        self.ax_accel.set_ylabel("Acceleration (g)")
        self.accel_lines = {
            'x': self.ax_accel.plot([], [], label='AcX')[0],
            'y': self.ax_accel.plot([], [], label='AcY')[0],
            'z': self.ax_accel.plot([], [], label='AcZ')[0],
            'magnitude': self.ax_accel.plot([], [], label='Magnitude')[0]
        }
        self.ax_accel.legend(loc='upper left')
        self.ax_accel.grid(True)

        # Gyroscope plot
        self.ax_gyro.set_title("Gyroscope Data")
        self.ax_gyro.set_ylabel("Angular Velocity (°/s)")
        self.gyro_lines = {
            'x': self.ax_gyro.plot([], [], label='GyX')[0],
            'y': self.ax_gyro.plot([], [], label='GyY')[0],
            'z': self.ax_gyro.plot([], [], label='GyZ')[0]
        }
        self.ax_gyro.legend(loc='upper left')
        self.ax_gyro.grid(True)

        # Range plot
        self.ax_range.set_title("Distance (VL53L1X, -1 = no target)")
        self.ax_range.set_xlabel("Time (s)")
        self.ax_range.set_ylabel("Distance (mm)")
        self.range_line = self.ax_range.plot([], [], label='Range')[0]
        self.ax_range.legend(loc='upper left')
        self.ax_range.grid(True)

        # Create frame for canvas
        frame = tk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=1)

    def update_plots(self, accel, gyro, distance, timestamp, tof_timestamp=None):
        """Updates the plots with new data."""
        timestamp_sec = timestamp / 1000.0  # convert to seconds
        self.timestamps.append(timestamp_sec)
        
        # Append new data
        self.accel_data['x'].append(accel[0])
        self.accel_data['y'].append(accel[1])
        # Remove 1g offset from Z-axis (gravity)
        self.accel_data['z'].append(accel[2] - 1.0)
        # Calculate magnitude: sqrt(acx^2 + acy^2 + (acz-1)^2)
        magnitude = (accel[0]**2 + accel[1]**2 + (accel[2] - 1.0)**2)**0.5
        self.accel_data['magnitude'].append(magnitude)
        
        self.gyro_data['x'].append(gyro[0])
        self.gyro_data['y'].append(gyro[1])
        self.gyro_data['z'].append(gyro[2])
        
        # Only plot valid TOF data, skip dummy (0xFFFE) and invalid (0xFFFF) data
        if distance != 0xFFFE and distance != 65534:
            # Valid TOF data - append it
            if tof_timestamp is not None:
                self.range_timestamps.append(tof_timestamp / 1000.0)
            else:
                self.range_timestamps.append(timestamp_sec)
            if distance == 0xFFFF or distance == 65535:
                self.range_data.append(-1)
            else:
                self.range_data.append(distance)

        # Trim old data: keep only the last 5 seconds
        if len(self.timestamps) > 0:
            current_time = self.timestamps[-1]
            min_time = current_time - PLOT_DISPLAY_WINDOW
            
            # Remove MPU data older than 5 seconds
            while len(self.timestamps) > 0 and self.timestamps[0] < min_time:
                self.timestamps.popleft()
                self.accel_data['x'].popleft()
                self.accel_data['y'].popleft()
                self.accel_data['z'].popleft()
                self.accel_data['magnitude'].popleft()
                self.gyro_data['x'].popleft()
                self.gyro_data['y'].popleft()
                self.gyro_data['z'].popleft()
        
        # Remove TOF data older than 5 seconds
        if len(self.range_timestamps) > 0:
            current_range_time = self.range_timestamps[-1]
            min_range_time = current_range_time - PLOT_DISPLAY_WINDOW
            while len(self.range_timestamps) > 0 and self.range_timestamps[0] < min_range_time:
                self.range_timestamps.popleft()
                self.range_data.popleft()

        # Throttle plot redraws to reduce CPU usage and prevent slowdown
        current_time = time.time()
        if current_time - self.last_plot_update_time >= self.min_plot_update_interval:
            self.last_plot_update_time = current_time
            
            # Update plot data
            for axis in ['x', 'y', 'z', 'magnitude']:
                self.accel_lines[axis].set_data(self.timestamps, self.accel_data[axis])
            for axis in ['x', 'y', 'z']:
                self.gyro_lines[axis].set_data(self.timestamps, self.gyro_data[axis])
            
            self.range_line.set_data(self.range_timestamps, self.range_data)

            # Rescale axes
            self.ax_accel.relim()
            self.ax_accel.autoscale_view()
            self.ax_gyro.relim()
            self.ax_gyro.autoscale_view()
            self.ax_range.relim()
            self.ax_range.autoscale_view()

            # Use draw_idle() instead of draw() to avoid blocking
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
            "MPU_Timestamp (ms)", "AcX (g)", "AcY (g)", "AcZ (g)", "GyX (dps)", "GyY (dps)", "GyZ (dps)", "TOF_Timestamp (ms)", "Range (mm)"
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
                    offset = tof_offset + 1 + i * 4
                    timestamp_delta = struct.unpack('!H', data[offset:offset+2])[0]
                    distance = struct.unpack('!H', data[offset+2:offset+4])[0]
                    
                    # Only process if this is a valid sample (within num_tof_samples)
                    if i < num_tof_samples:
                        sample_timestamp = packet_timestamp - timestamp_delta
                        tof_data.append((distance, sample_timestamp))
                
                # Log all MPU samples with available TOF data (only if recording)
                if self.gui.recording:
                    for i, (accel, gyro, mpu_ts) in enumerate(mpu_sensor_data):
                        if i < len(tof_data):
                            distance, tof_ts = tof_data[i]
                        else:
                            distance = 0xFFFE  # No TOF data available
                            tof_ts = mpu_ts    # Use MPU timestamp as reference
                        self.csv_writer.writerow([mpu_ts] + accel + gyro + [tof_ts, distance])
                    
                    # Flush CSV file periodically (every 10 packets) instead of every sample
                    # This reduces I/O overhead significantly
                    # (10 packets × 20 samples/packet = 200 samples before flush)
                    if packet_timestamp % 10 == 0:
                        self.log_file.flush()
                
                print(f"Received packet: {num_mpu_samples} MPU samples, {num_tof_samples} TOF samples")
                print(f">>> TOF Range values (mm): {[d for d, _ in tof_data]}")
                
                # Update GUI with all MPU samples paired with TOF data where available (thread-safe via after())
                # Skip updates if playback is active
                if not self.gui.playback_mode:
                    for i, (accel, gyro, mpu_ts) in enumerate(mpu_sensor_data):
                        if i < len(tof_data):
                            distance, tof_ts = tof_data[i]
                        else:
                            distance = 0xFFFE
                            tof_ts = None
                        self.gui.after(0, self.gui.update_plots, accel, gyro, distance, mpu_ts, tof_ts)

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