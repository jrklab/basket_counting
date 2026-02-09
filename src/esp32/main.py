import machine
import network
import socket
import time
import struct
import random
import gc
from mpu6050 import MPU6050
import sys
sys.path.append('.')
from adafruit_mp_vl53l1x import VL53L1X

# phone hotspot
# WIFI_SSID = "Hottnt"
# WIFI_PASSWORD = "fortestonly"
# SERVER_IP = "10.98.51.74"
# home WiFi
WIFI_SSID = "AttIsBeter"
WIFI_PASSWORD = "PP123Acalance"
SERVER_IP = "192.168.1.176"
SERVER_PORT = 12345

# Sensor configuration
MPU6050_READ_FREQUENCY_HZ = 200  # MPU6050 read frequency (5ms period)
VL53L1X_READ_FREQUENCY_HZ = 40   # VL53L1X read frequency (25ms period, depending on timing budget)
UDP_SEND_FREQUENCY_HZ = 10       # UDP packet send frequency (100ms period)

SAMPLES_PER_PACKET_MPU = MPU6050_READ_FREQUENCY_HZ // UDP_SEND_FREQUENCY_HZ  # 20 samples
SAMPLES_PER_PACKET_TOF = VL53L1X_READ_FREQUENCY_HZ // UDP_SEND_FREQUENCY_HZ  # 4 samples

# Enable/disable fake data for testing
USE_FAKE_DATA_VL53L1X = False
USE_FAKE_DATA_MPU6050 = False

MPU6050_SCL_PIN = 4
MPU6050_SDA_PIN = 5
VL53L1X_SCL_PIN = 6
VL53L1X_SDA_PIN = 7
VL53L1X_XSHUT_PIN = 8

VL53L1X_DISTANCE_MODE_SHORT = 1
VL53L1X_TIMING_BUDGET_MS = 33
VL53L1X_MEASUREMENT_INTERVAL_MS = 40 # timeout + some margin for processing
# MPU6050 connection (I2C0)
i2c0 = machine.I2C(0, scl=machine.Pin(MPU6050_SCL_PIN), sda=machine.Pin(MPU6050_SDA_PIN), freq=400000)
devices = i2c0.scan()
print(f"I2C0 devices found: {[hex(d) for d in devices]}")

mpu = MPU6050(i2c=i2c0, addr=0x68, use_fake_data=USE_FAKE_DATA_MPU6050)

# Test MPU6050 connection before proceeding
try:
    test_read = mpu.get_values()
    print(f"MPU6050 test read successful: AcX={test_read['AcX']}")
except Exception as e:
    print(f"MPU6050 test read failed: {e}")

# Set to maximum ranges
mpu.set_accel_range(16)   # Acceleration range: ±2 ±4 ±8 ±16g
mpu.set_gyro_range(2000)  # Gyroscopes range: +/- 250 500 1000 2000 degree/sec

# VL53L1X connection (I2C1)
i2c1 = machine.I2C(1, scl=machine.Pin(VL53L1X_SCL_PIN), sda=machine.Pin(VL53L1X_SDA_PIN), freq=400000)
devices1 = i2c1.scan()
print(f"I2C1 devices found: {[hex(d) for d in devices1]}")

vl53 = VL53L1X(i2c=i2c1, address=0x29)
# Configure VL53L1X for ~40Hz operation with short range mode
# distance_mode=1 (short), timing_budget=33ms, measurement_interval=25ms
vl53.config_sequence(distance_mode=VL53L1X_DISTANCE_MODE_SHORT, timing_budget=VL53L1X_TIMING_BUDGET_MS, measurement_interval_ms=VL53L1X_MEASUREMENT_INTERVAL_MS)
vl53.start_ranging()

# Data buffers
mpu_data_buffer = []
tof_data_buffer = []

# Timing tracking for data ready checks
tof_last_read_time = time.ticks_ms()
tof_measurement_period = 1000.0/VL53L1X_READ_FREQUENCY_HZ  # ms (for 40Hz operation)

# VL53L1X timeout tracking
VL53L1X_TIMEOUT_MS = VL53L1X_MEASUREMENT_INTERVAL_MS  # 500ms timeout for VL53L1X data_ready
tof_last_data_ready_time = time.ticks_ms()

# Pre-allocate packet buffer (318 bytes) to avoid memory fragmentation
packet_buffer = bytearray(318)

def connect_wifi():
    """Connects to the WiFi network with proper state management."""
    sta_if = network.WLAN(network.STA_IF)
    
    # Properly reset WiFi state to avoid "Internal State Error"
    print("Resetting WiFi state...")
    sta_if.active(False)
    time.sleep_ms(500)  # Wait for state to clear
    
    if not sta_if.isconnected():
        print("Connecting to WiFi...")
        sta_if.active(True)
        time.sleep_ms(200)  # Give it time to activate
        
        # Attempt connection with retries
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"Connection attempt {attempt + 1}/{max_retries}...")
                sta_if.connect(WIFI_SSID, WIFI_PASSWORD)
                
                # Wait for connection with timeout
                timeout = time.time() + 10  # 10 second timeout
                while not sta_if.isconnected():
                    if time.time() > timeout:
                        print(f"Connection timeout on attempt {attempt + 1}")
                        break
                    time.sleep_ms(100)
                
                if sta_if.isconnected():
                    break
            except Exception as e:
                print(f"Connection error: {e}")
                if attempt < max_retries - 1:
                    sta_if.active(False)
                    time.sleep_ms(500)
                    sta_if.active(True)
                    time.sleep_ms(200)
    
    if sta_if.isconnected():
        print("✓ WiFi connected:", sta_if.ifconfig())
    else:
        print("✗ WiFi connection failed!")


def read_mpu6050_data(timer):
    """Timer callback to read MPU6050 at 200Hz.
    
    This function is called every 5ms (200Hz) to read accelerometer and gyroscope data.
    The data is buffered and will be packaged with TOF data for UDP transmission.
    """
    global mpu_data_buffer
    
    try:
        mpu_values = mpu.get_values()
        mpu_data_buffer.append({
            'accel_x': mpu_values["AcX"],
            'accel_y': mpu_values["AcY"],
            'accel_z': mpu_values["AcZ"],
            'gyro_x': mpu_values["GyX"],
            'gyro_y': mpu_values["GyY"],
            'gyro_z': mpu_values["GyZ"],
            'timestamp': time.ticks_ms()
        })
    except OSError as e:
        print(f"MPU6050 read error: {e}")


def read_vl53l1x_data():
    """Read VL53L1X data when available with timeout mechanism.
    
    This function should be called in the main loop to non-blocking read VL53L1X data
    when it's ready. If data_ready is not valid for VL53L1X_TIMEOUT_MS, reboots the sensor.
    The measurement period is approximately 25ms (40Hz operation).
    """
    global tof_data_buffer, tof_last_read_time, tof_last_data_ready_time
    
    if USE_FAKE_DATA_VL53L1X:
        # Generate fake distance data (100-400mm) at ~40Hz
        current_time = time.ticks_ms()
        time_since_last = time.ticks_diff(current_time, tof_last_read_time)
        
        if time_since_last >= tof_measurement_period:
            # Generate realistic fake data
            fake_distance = random.randint(100, 400)
            tof_data_buffer.append({
                'distance_mm': fake_distance,
                'timestamp': current_time
            })
            tof_last_read_time = current_time
            tof_last_data_ready_time = current_time
    else:
        # Read actual sensor data with timeout mechanism
        current_time = time.ticks_ms()
        
        if vl53.data_ready:
            # Reset timeout counter when data is ready
            tof_last_data_ready_time = current_time
            
            distance_mm = vl53.distance
            # distance property returns cm, multiply by 10 for mm
            # -1 indicates no target
            if distance_mm >= 0:
                tof_data_buffer.append({
                    'distance_mm': int(distance_mm),
                    'timestamp': current_time
                })
            else:
                # Invalid reading, still append to maintain synchronization
                tof_data_buffer.append({
                    'distance_mm': 0xFFFF,  # Sentinel for invalid
                    'timestamp': current_time
                })
            vl53.clear_interrupt()
        else:
            # Check if timeout has occurred
            time_since_last_ready = time.ticks_diff(current_time, tof_last_data_ready_time)
            if time_since_last_ready >= VL53L1X_TIMEOUT_MS:
                print(f"VL53L1X timeout after {time_since_last_ready}ms. Initiating hardware reboot...")
                # Reboot VL53L1X
                vl53.reboot(xshut_pin_number=VL53L1X_XSHUT_PIN)
                time.sleep_ms(50)  # Wait for reboot to complete
                # Restore configuration
                vl53.config_sequence(distance_mode=VL53L1X_DISTANCE_MODE_SHORT, 
                                    timing_budget=VL53L1X_TIMING_BUDGET_MS, 
                                    measurement_interval_ms=VL53L1X_MEASUREMENT_INTERVAL_MS)
                vl53.start_ranging()
                # Reset timeout counter
                tof_last_data_ready_time = current_time
                print("VL53L1X reboot complete and ranging restarted.")


def pack_and_send_udp_packet(udp_socket):
    """Pack buffered sensor data and send via UDP at 10Hz.
    
    Packet format (FIXED SIZE):
    - Packet timestamp (4 bytes, uint32)
    - Number of MPU samples (1 byte, uint8) - typically 20
    - MPU samples (20 slots): 
      - Sample timestamp delta (2 bytes, uint16, delta = packet_timestamp - sample_timestamp) + AcX, AcY, AcZ, GyX, GyY, GyZ (6 × int16 = 12 bytes) = 14 bytes per slot
      - 20 slots × 14 bytes = 280 bytes
    - Number of TOF samples (1 byte, uint8) - up to 8
    - TOF samples (8 slots):
      - Sample timestamp delta (2 bytes, uint16) + distance_mm (uint16 = 2 bytes) = 4 bytes per slot
      - 8 slots × 4 bytes = 32 bytes, unfilled slots have timestamp_delta=0 and distance=0
    
    Total packet size: 4 + 1 + 280 + 1 + 32 = 318 bytes (fixed)
    """
    if len(mpu_data_buffer) >= SAMPLES_PER_PACKET_MPU:
        # Extract all available TOF data
        mpu_data_to_send = mpu_data_buffer[:SAMPLES_PER_PACKET_MPU]
        tof_data_to_send = list(tof_data_buffer)  # Send all accumulated TOF data
        
        # Only proceed if we have at least some TOF data
        if len(tof_data_to_send) > 0:
            # Remove sent data from buffers
            del mpu_data_buffer[:SAMPLES_PER_PACKET_MPU]
            tof_data_buffer.clear()

            # Use pre-allocated buffer and fill it
            packet_timestamp = time.ticks_ms()
            
            # Pack data into pre-allocated buffer
            offset = 0
            struct.pack_into('!I', packet_buffer, offset, packet_timestamp)
            offset += 4
            
            # Add MPU sample count
            num_mpu_samples = len(mpu_data_to_send)
            struct.pack_into('!B', packet_buffer, offset, num_mpu_samples)
            offset += 1
            
            # Pack MPU6050 data with timestamp deltas
            for sample in mpu_data_to_send:
                # Calculate timestamp delta (packet_timestamp - sample_timestamp)
                timestamp_delta = packet_timestamp - sample['timestamp']
                # Clamp to uint16 range (0-65535)
                timestamp_delta = max(0, min(65535, timestamp_delta))
                struct.pack_into('!H', packet_buffer, offset, timestamp_delta)
                offset += 2
                struct.pack_into('!hhhhhh', packet_buffer, offset,
                    sample['accel_x'],
                    sample['accel_y'],
                    sample['accel_z'],
                    sample['gyro_x'],
                    sample['gyro_y'],
                    sample['gyro_z']
                )
                offset += 12
            
            # Add TOF sample count
            num_tof_samples = min(len(tof_data_to_send), 8)  # Cap at 8 samples
            struct.pack_into('!B', packet_buffer, offset, num_tof_samples)
            offset += 1
            
            # Pack VL53L1X data with timestamp deltas (fixed 8 slots)
            for i in range(8):
                if i < len(tof_data_to_send):
                    # Calculate timestamp delta (packet_timestamp - sample_timestamp)
                    timestamp_delta = packet_timestamp - tof_data_to_send[i]['timestamp']
                    # Clamp to uint16 range (0-65535)
                    timestamp_delta = max(0, min(65535, timestamp_delta))
                    struct.pack_into('!H', packet_buffer, offset, timestamp_delta)
                    offset += 2
                    struct.pack_into('!H', packet_buffer, offset, tof_data_to_send[i]['distance_mm'])
                    offset += 2
                else:
                    # Pad with timestamp_delta=0 and distance=0x0000
                    struct.pack_into('!I', packet_buffer, offset, 0)
                    offset += 4

            # Send packet
            try:
                udp_socket.sendto(packet_buffer, (SERVER_IP, SERVER_PORT))
                print(f"[{packet_timestamp:>10}] Sent packet: {num_mpu_samples} MPU + {num_tof_samples} TOF samples (size: 318 bytes)")
            except Exception as e:
                print(f"Error sending UDP packet: {e}")
            
            # Force garbage collection after send to free memory
            gc.collect()
            
            return True
    return False


def main():
    """Main function."""
    connect_wifi()

    # Setup UDP socket
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Setup timer for reading MPU6050 data at 200Hz (5ms period)
    mpu_timer = machine.Timer(0)
    mpu_timer.init(period=5, mode=machine.Timer.PERIODIC, callback=read_mpu6050_data)

    print("Starting data acquisition and transmission...")
    print(f"MPU6050: 200 Hz (5ms period)")
    print(f"VL53L1X: ~40 Hz (25ms period)")
    print(f"UDP transmission: 10 Hz (100ms period)")
    print(f"Fake VL53L1X data: {USE_FAKE_DATA_VL53L1X}")

    last_send_time = time.ticks_ms()
    send_interval = 1000 // UDP_SEND_FREQUENCY_HZ  # 100ms for 10Hz

    try:
        while True:
            # Non-blocking read of VL53L1X data
            read_vl53l1x_data()
            
            # Check if it's time to send a packet (10Hz = 100ms interval)
            current_time = time.ticks_ms()
            if time.ticks_diff(current_time, last_send_time) >= send_interval:
                if pack_and_send_udp_packet(udp_socket):
                    last_send_time = current_time
            
            # Small sleep to prevent blocking (1ms)
            time.sleep_ms(1)
            
    except KeyboardInterrupt:
        print("Stopping data acquisition...")
        mpu_timer.deinit()
        vl53.stop_ranging()
        udp_socket.close()


if __name__ == "__main__":
    main()
