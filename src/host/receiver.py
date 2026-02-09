import socket
import struct
import csv
import tkinter as tk
from tkinter import ttk
from threading import Thread
from queue import Queue
from collections import deque
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import time

# --- Configuration ---
UDP_IP = "0.0.0.0"  # Listen on all available interfaces
UDP_PORT = 12345
LOG_FILE = "sensor_data.csv"
SAMPLES_PER_PACKET = 20
TOF_SAMPLES_PER_PACKET = 5
PLOT_HISTORY_SIZE = 100  # Number of data points to show on the plot

# --- Conversion Factors ---
ACCEL_SENSITIVITY = 2048.0  # LSB/g for ±16g range
GYRO_SENSITIVITY = 16.384     # LSB/°/s for ±2000°/s range

# --- GUI ---
class SensorGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ESP32 MPU6050 Data")
        self.geometry("900x600")

        # Data buffers for plotting
        self.timestamps = deque(maxlen=PLOT_HISTORY_SIZE)
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

        self.create_plots()

    def create_plots(self):
        """Creates and embeds the matplotlib plots with scrollbars for y-axis control."""
        self.fig, (self.ax_accel, self.ax_gyro, self.ax_range) = plt.subplots(3, 1, figsize=(9, 8))
        self.fig.subplots_adjust(right=0.85)  # Make room for scrollbars

        # Acceleration plot
        self.ax_accel.set_title("Accelerometer Data")
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

        # Create frame for canvas and scrollbars
        frame = tk.Frame(self)
        frame.pack(side=tk.TOP, fill=tk.BOTH, expand=1)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=frame)
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=1)

        # Create frame for scrollbars
        scrollbar_frame = tk.Frame(frame, width=20)
        scrollbar_frame.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Create scrollbars for each plot
        self.accel_scrollbar = tk.Scale(scrollbar_frame, orient=tk.VERTICAL, 
                                        label="Accel Y-axis", from_=0, to=50, 
                                        command=self.on_accel_scroll)
        self.accel_scrollbar.set(16)
        self.accel_scrollbar.pack(fill=tk.Y, expand=1, padx=2, pady=5)
        
        self.gyro_scrollbar = tk.Scale(scrollbar_frame, orient=tk.VERTICAL,
                                       label="Gyro Y-axis", from_=0, to=500,
                                       command=self.on_gyro_scroll)
        self.gyro_scrollbar.set(250)
        self.gyro_scrollbar.pack(fill=tk.Y, expand=1, padx=2, pady=5)
        
        self.range_scrollbar = tk.Scale(scrollbar_frame, orient=tk.VERTICAL,
                                        label="Range Y-axis", from_=0, to=2000,
                                        command=self.on_range_scroll)
        self.range_scrollbar.set(1000)
        self.range_scrollbar.pack(fill=tk.Y, expand=1, padx=2, pady=5)

    def on_accel_scroll(self, value):
        """Handle accelerometer y-axis scrollbar."""
        y_max = float(value) if float(value) > 0 else 16
        self.ax_accel.set_ylim(-y_max, y_max)
        self.canvas.draw_idle()

    def on_gyro_scroll(self, value):
        """Handle gyroscope y-axis scrollbar."""
        y_max = float(value) if float(value) > 0 else 250
        self.ax_gyro.set_ylim(-y_max, y_max)
        self.canvas.draw_idle()

    def on_range_scroll(self, value):
        """Handle range y-axis scrollbar."""
        y_max = float(value) if float(value) > 0 else 1000
        self.ax_range.set_ylim(-100, y_max)
        self.canvas.draw_idle()

    def update_plots(self, accel, gyro, distance, timestamp):
        """Updates the plots with new data."""
        self.timestamps.append(timestamp / 1000.0) # convert to seconds
        
        # Append new data
        self.accel_data['x'].append(accel[0])
        self.accel_data['y'].append(accel[1])
        self.accel_data['z'].append(accel[2])
        # Calculate magnitude: sqrt(acx^2 + acy^2 + acz^2)
        magnitude = (accel[0]**2 + accel[1]**2 + accel[2]**2)**0.5
        self.accel_data['magnitude'].append(magnitude)
        
        self.gyro_data['x'].append(gyro[0])
        self.gyro_data['y'].append(gyro[1])
        self.gyro_data['z'].append(gyro[2])
        
        # Convert 0xFFFF (65535) to -1 for invalid/no target
        if distance == 0xFFFF or distance == 65535:
            display_distance = -1
        else:
            display_distance = distance
        self.range_data.append(display_distance)

        # Throttle plot redraws to reduce CPU usage and prevent slowdown
        current_time = time.time()
        if current_time - self.last_plot_update_time >= self.min_plot_update_interval:
            self.last_plot_update_time = current_time
            
            # Update plot data
            for axis in ['x', 'y', 'z', 'magnitude']:
                self.accel_lines[axis].set_data(self.timestamps, self.accel_data[axis])
            for axis in ['x', 'y', 'z']:
                self.gyro_lines[axis].set_data(self.timestamps, self.gyro_data[axis])
            
            self.range_line.set_data(self.timestamps, self.range_data)

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

        self.log_file = open(LOG_FILE, "w", newline="")
        self.csv_writer = csv.writer(self.log_file)
        self.csv_writer.writerow([
            "MPU_Timestamp (ms)", "AcX (g)", "AcY (g)", "AcZ (g)", "GyX (dps)", "GyY (dps)", "GyZ (dps)", "TOF_Timestamp (ms)", "Range (mm)"
        ])
        self.log_file.flush()  # Flush header immediately
        
        # Data queue for passing packets from receiver thread to processor thread
        self.packet_queue = Queue(maxsize=100)
        self.running = True

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
                
                # Log all MPU samples with available TOF data
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
                
                # Update GUI with last sample (thread-safe via after())
                last_accel, last_gyro, _ = mpu_sensor_data[-1]
                last_distance, _ = tof_data[-1] if tof_data else (0xFFFF, 0)
                self.gui.after(0, self.gui.update_plots, last_accel, last_gyro, last_distance, packet_timestamp)

            except:
                # Queue timeout - just continue
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