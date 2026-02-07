import machine
import network
import socket
import time
import struct
from mpu6050 import MPU6050

# WiFi credentials
WIFI_SSID = "Hottnt"
WIFI_PASSWORD = "fortestonly"

# Laptop server details
SERVER_IP = "10.98.51.74"
SERVER_PORT = 12345

# MPU6050 connection
# i2c = machine.I2C(scl=machine.Pin(22), sda=machine.Pin(21))
mpu = MPU6050()

# Data acquisition parameters
READ_FREQUENCY_HZ = 200
SEND_FREQUENCY_HZ = 10
SAMPLES_PER_PACKET = READ_FREQUENCY_HZ // SEND_FREQUENCY_HZ

# Data buffer
data_buffer = []

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
    """Reads sensor data and adds it to the buffer."""
    global data_buffer
    values = mpu.get_values()
    # We are interested in AcX, AcY, AcZ, GyX, GyY, GyZ
    data_point = (
        values["AcX"],
        values["AcY"],
        values["AcZ"],
        values["GyX"],
        values["GyY"],
        values["GyZ"],
    )
    data_buffer.append(data_point)

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
        if len(data_buffer) >= SAMPLES_PER_PACKET:
            # Get the data to send
            data_to_send = data_buffer[:SAMPLES_PER_PACKET]
            del data_buffer[:SAMPLES_PER_PACKET]

            # Get current timestamp
            timestamp = time.ticks_ms()

            # Pack the data
            # 1 long long (timestamp) + 20 * 6 shorts (sensor data)
            # The format string for struct.pack will be '<Q' for timestamp (unsigned long long)
            # and 'h' for each sensor value (short).
            # So, for 20 samples, it will be '<Q' + 'hhhhhh' * 20
            
            # NOTE: MicroPython's struct might not support the same format strings as CPython.
            # We will use 'l' for long, which is 4 bytes. We will send timestamp in seconds.
            
            try:
                # Pack the timestamp and the sensor data
                # 'l' for timestamp, and 'h' for each of the 6 sensor values
                # The total number of values to pack is 1 (timestamp) + 20*6 = 121
                # The format string would be '<l' + 'h'*120
                
                # Let's create the packet
                packet = bytearray()
                packet.extend(struct.pack('!L', time.ticks_ms())) # 4 bytes for timestamp
                for sample in data_to_send:
                    packet.extend(struct.pack('!hhhhhh', *sample))

                # Send the data
                udp_socket.sendto(packet, (SERVER_IP, SERVER_PORT))
                print(f"Sent packet with {len(data_to_send)} samples.")

            except Exception as e:
                print(f"Error sending data: {e}")

        # Sleep for a short time to prevent busy-waiting
        time.sleep_ms(10)

if __name__ == "__main__":
    main()
