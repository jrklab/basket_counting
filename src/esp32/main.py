import machine
import network
import socket
import time
import struct
from mpu6050 import MPU6050
from vl53l1x import VL53L1X

# WiFi credentials
WIFI_SSID = "Hottnt"
WIFI_PASSWORD = "fortestonly"
SERVER_IP = "10.98.51.74"
# WIFI_SSID = "AttIsBetter"
# WIFI_PASSWORD = "PP123Acalance"
# SERVER_IP = "192.168.1.176"

# Laptop server details
SERVER_PORT = 12345

# MPU6050 connection (I2C0)
i2c0 = machine.I2C(0, scl=machine.Pin(4), sda=machine.Pin(5), freq=400000)
# Scan I2C bus to see what devices are connected
devices = i2c0.scan()
print(f"I2C0 devices found: {[hex(d) for d in devices]}")

mpu = MPU6050(i2c=i2c0, addr=0x68, use_fake_data=False)

# Wait for MPU6050 to stabilize after initialization
# time.sleep(1.0)

# Test MPU6050 connection before proceeding
try:
    test_read = mpu.get_values()
    print(f"MPU6050 test read successful: AcX={test_read['AcX']}")
except Exception as e:
    print(f"MPU6050 test read failed: {e}")

# Set to maximum ranges
mpu.set_accel_range(16)  # Acceleration range: ±2 ±4 ±8 ±16g
mpu.set_gyro_range(2000)  # Gyroscopes range: +/- 250 500 1000 2000 degree/sec

# VL53L1X connection (I2C1)
i2c1 = machine.I2C(1, scl=machine.Pin(6), sda=machine.Pin(7), freq=400000)
devices1 = i2c1.scan()
print(f"I2C1 devices found: {[hex(d) for d in devices1]}")
tof = VL53L1X(i2c=i2c1, addr=0x29, use_fake_data=False)
tof.init()
tof.start_ranging(1)  # Short range mode for 50Hz operation

# Data acquisition parameters
READ_FREQUENCY_HZ = 200  # MPU6050 read frequency
TOF_READ_FREQUENCY_HZ = 50  # VL53L1X read frequency
SEND_FREQUENCY_HZ = 10
SAMPLES_PER_PACKET = READ_FREQUENCY_HZ // SEND_FREQUENCY_HZ
TOF_SAMPLES_PER_PACKET = TOF_READ_FREQUENCY_HZ // SEND_FREQUENCY_HZ

# Data buffers
mpu_data_buffer = []
tof_data_buffer = []

# Timing counters for reading at different frequencies
mpu_read_counter = 0
tof_read_counter = 0
tof_read_interval = READ_FREQUENCY_HZ // TOF_READ_FREQUENCY_HZ  # Read TOF every 4th MPU sample

def connect_wifi():
    """Connects to the WiFi network."""
    sta_if = network.WLAN(network.STA_IF)
    if not sta_if.isconnected():
        print("Connecting to WiFi...")
        sta_if.active(True)
        sta_if.connect(WIFI_SSID, WIFI_PASSWORD)
        while not sta_if.isconnected():
            pass
    print("WiFi connected:", sta_if.ifconfig())

def read_sensor_data(timer):
    """Reads sensor data and adds it to the buffers."""
    global mpu_data_buffer, tof_data_buffer, mpu_read_counter, tof_read_counter
    
    try:
        # Always read MPU6050
        mpu_values = mpu.get_values()
        mpu_data_buffer.append((
            mpu_values["AcX"],
            mpu_values["AcY"],
            mpu_values["AcZ"],
            mpu_values["GyX"],
            mpu_values["GyY"],
            mpu_values["GyZ"],
        ))
    except OSError as e:
        print(f"MPU6050 read error: {e}")
        return
    
    # Read VL53L1X at lower frequency (50Hz = every 4 reads at 200Hz)
    mpu_read_counter += 1
    if mpu_read_counter % tof_read_interval == 0:
        distance = tof.get_range_mm()
        if distance >= 0:
            tof_data_buffer.append(distance)
            print(f"TOF: {distance}mm")
        else:
            tof_data_buffer.append(0)  # Use 0 for failed readings
            print(f"TOF: invalid reading ({distance})")

def main():
    """Main function."""
    connect_wifi()

    # Setup UDP socket
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Setup timer for reading sensor data
    sensor_timer = machine.Timer(0)
    sensor_timer.init(period=(1000 // READ_FREQUENCY_HZ), mode=machine.Timer.PERIODIC, callback=read_sensor_data)

    print("Starting data acquisition...")

    while True:
        if len(mpu_data_buffer) >= SAMPLES_PER_PACKET and len(tof_data_buffer) >= TOF_SAMPLES_PER_PACKET:
            # Get the data to send
            mpu_data_to_send = mpu_data_buffer[:SAMPLES_PER_PACKET]
            tof_data_to_send = tof_data_buffer[:TOF_SAMPLES_PER_PACKET]
            del mpu_data_buffer[:SAMPLES_PER_PACKET]
            del tof_data_buffer[:TOF_SAMPLES_PER_PACKET]

            # Get current timestamp
            timestamp = time.ticks_ms()

            try:
                # Pack the data
                # Format: timestamp (4 bytes) + 20 MPU samples (6 shorts each) + 5 TOF samples (1 unsigned short each)
                packet = bytearray()
                packet.extend(struct.pack('!L', timestamp))  # 4 bytes timestamp
                
                # Add MPU6050 data
                for sample in mpu_data_to_send:
                    packet.extend(struct.pack('!hhhhhh', *sample))
                
                # Add VL53L1X data
                for distance in tof_data_to_send:
                    packet.extend(struct.pack('!H', distance))  # Unsigned short for distance

                # Send the data
                udp_socket.sendto(packet, (SERVER_IP, SERVER_PORT))
                # print(f"Sent packet: {len(mpu_data_to_send)} MPU samples, {len(tof_data_to_send)} TOF samples")

            except Exception as e:
                print(f"Error sending data: {e}")

        # Sleep for a short time to prevent busy-waiting
        time.sleep_ms(10)

if __name__ == "__main__":
    main()
