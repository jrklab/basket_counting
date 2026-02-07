import socket
import struct
import csv
import tkinter as tk
from threading import Thread
from collections import deque
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

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
        self.geometry("800x600")

        # Data buffers for plotting
        self.timestamps = deque(maxlen=PLOT_HISTORY_SIZE)
        self.accel_data = {
            'x': deque(maxlen=PLOT_HISTORY_SIZE),
            'y': deque(maxlen=PLOT_HISTORY_SIZE),
            'z': deque(maxlen=PLOT_HISTORY_SIZE)
        }
        self.gyro_data = {
            'x': deque(maxlen=PLOT_HISTORY_SIZE),
            'y': deque(maxlen=PLOT_HISTORY_SIZE),
            'z': deque(maxlen=PLOT_HISTORY_SIZE)
        }
        self.range_data = deque(maxlen=PLOT_HISTORY_SIZE)

        self.create_plots()

    def create_plots(self):
        """Creates and embeds the matplotlib plots."""
        self.fig, (self.ax_accel, self.ax_gyro, self.ax_range) = plt.subplots(3, 1, figsize=(8, 8))

        # Acceleration plot
        self.ax_accel.set_title("Accelerometer Data")
        self.ax_accel.set_ylabel("Acceleration (g)")
        self.accel_lines = {
            'x': self.ax_accel.plot([], [], label='AcX')[0],
            'y': self.ax_accel.plot([], [], label='AcY')[0],
            'z': self.ax_accel.plot([], [], label='AcZ')[0]
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
        self.ax_range.set_title("Distance (VL53L1X)")
        self.ax_range.set_xlabel("Time (s)")
        self.ax_range.set_ylabel("Distance (mm)")
        self.range_line = self.ax_range.plot([], [], label='Range')[0]
        self.ax_range.legend(loc='upper left')
        self.ax_range.grid(True)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=1)

    def update_plots(self, accel, gyro, distance, timestamp):
        """Updates the plots with new data."""
        self.timestamps.append(timestamp / 1000.0) # convert to seconds
        
        # Append new data
        self.accel_data['x'].append(accel[0])
        self.accel_data['y'].append(accel[1])
        self.accel_data['z'].append(accel[2])
        
        self.gyro_data['x'].append(gyro[0])
        self.gyro_data['y'].append(gyro[1])
        self.gyro_data['z'].append(gyro[2])
        
        self.range_data.append(distance)

        # Update plot data
        for axis in ['x', 'y', 'z']:
            self.accel_lines[axis].set_data(self.timestamps, self.accel_data[axis])
            self.gyro_lines[axis].set_data(self.timestamps, self.gyro_data[axis])
        
        self.range_line.set_data(self.timestamps, self.range_data)

        # Rescale axes
        self.ax_accel.relim()
        self.ax_accel.autoscale_view()
        self.ax_gyro.relim()
        self.ax_gyro.autoscale_view()
        self.ax_range.relim()
        self.ax_range.autoscale_view()

        self.canvas.draw()


# --- Data Receiver and Logger ---
class DataReceiver:
    def __init__(self, gui):
        self.gui = gui
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((UDP_IP, UDP_PORT))
        print(f"Listening on {UDP_IP}:{UDP_PORT}")

        self.log_file = open(LOG_FILE, "w", newline="")
        self.csv_writer = csv.writer(self.log_file)
        self.csv_writer.writerow([
            "Timestamp (ms)", "AcX (g)", "AcY (g)", "AcZ (g)", "GyX (dps)", "GyY (dps)", "GyZ (dps)", "Range (mm)"
        ])

    def receive_data(self):
        """Receives, processes, and logs data."""
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
                timestamp = struct.unpack('!L', data[0:4])[0]
                
                # Parse MPU6050 data (20 samples × 6 shorts)
                mpu_sensor_data = []
                for i in range(SAMPLES_PER_PACKET):
                    offset = 4 + i * 12
                    sample = struct.unpack('!hhhhhh', data[offset:offset+12])
                    
                    # Convert to physical units
                    accel = [s / ACCEL_SENSITIVITY for s in sample[0:3]]
                    gyro = [s / GYRO_SENSITIVITY for s in sample[3:6]]
                    
                    mpu_sensor_data.append((accel, gyro))
                
                # Parse VL53L1X data (5 samples × 1 unsigned short)
                tof_offset = 4 + SAMPLES_PER_PACKET * 12
                tof_data = []
                for i in range(TOF_SAMPLES_PER_PACKET):
                    offset = tof_offset + i * 2
                    distance = struct.unpack('!H', data[offset:offset+2])[0]
                    tof_data.append(distance)
                
                # Log data
                for mpu_sample, distance in zip(mpu_sensor_data, tof_data):
                    accel, gyro = mpu_sample
                    self.csv_writer.writerow([timestamp] + accel + gyro + [distance])
                
                print(f"Received packet: {len(mpu_sensor_data)} MPU samples, {len(tof_data)} TOF samples")
                print(f">>> TOF Range values (mm): {tof_data}")
                for i, val in enumerate(tof_data):
                    print(f"    [{i}]: {val}mm")
                print()
                
                # Update GUI with last sample
                last_accel, last_gyro = mpu_sensor_data[-1]
                last_distance = tof_data[-1]
                self.gui.update_plots(last_accel, last_gyro, last_distance, timestamp)

            except Exception as e:
                print(f"Error receiving data: {e}")

    def start(self):
        thread = Thread(target=self.receive_data, daemon=True)
        thread.start()

    def close(self):
        self.sock.close()
        self.log_file.close()

# --- Main ---
if __name__ == "__main__":
    gui = SensorGui()
    receiver = DataReceiver(gui)
    receiver.start()
    gui.mainloop()
    receiver.close()
    print("Receiver closed.")