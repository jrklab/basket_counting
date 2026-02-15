"""
Configuration constants for the basketball shot counter system.
"""

# --- Network Configuration ---
UDP_IP = "0.0.0.0"  # Listen on all available interfaces
UDP_PORT = 12345

# --- Data Logging ---
LOG_FILE = "sensor_data.csv"

# --- Packet Structure ---
SAMPLES_PER_PACKET = 20
TOF_SAMPLES_PER_PACKET = 5

# --- Plot Configuration ---
PLOT_HISTORY_SIZE = int(200 * 2.5)  # Number of data points to buffer (5 seconds worth)
PLOT_DISPLAY_WINDOW = 5.0  # Display window in seconds (only show last 5s)

# --- Sensor Conversion Factors ---
ACCEL_SENSITIVITY = 2048.0  # LSB/g for ±16g range
GYRO_SENSITIVITY = 16.384   # LSB/°/s for ±2000°/s range
