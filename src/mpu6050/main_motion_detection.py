"""
Simple deep sleep test with MPU6050 motion detection.

Flow:
1. Configure MPU6050 motion detection (interrupt on acceleration > threshold)
2. ESP32 enters deep sleep
3. Motion detected -> ESP32 wakes up
4. Repeat
"""

import machine
import esp32
import time
from machine import I2C, Pin
from mpu6050 import MPU6050
from hardware_config import (
    MPU6050_SCL_PIN, MPU6050_SDA_PIN, MPU6050_INT_PIN,
    RGB_LED_PIN, RGB_LED_NUM_PIXELS, RGBLed
)

# Configuration
MOTION_THRESHOLD = 31  # 32mg per LSB
ACTIVE_TIMEOUT_MS = 30000  # 30 seconds - if no motion, go back to sleep
DEBUG_ACCEL = True  # Print accelerometer values when motion detected

def main():
    # Initialize RGB LED
    led = RGBLed(RGB_LED_PIN, RGB_LED_NUM_PIXELS)
    
    # Check if woke from deep sleep (motion detected)
    if machine.reset_cause() == machine.DEEPSLEEP_RESET:
        print("\n" + "=" * 60)
        print("MOTION DETECTED! Woke from deep sleep")
        print("=" * 60)
        led.green()  # Turn green LED on
    else:
        print("=" * 60)
        print("Power-on / Reset")
        print("=" * 60)
        led.green()  # Turn green LED on during initialization
    
    # Setup MPU6050 motion detection
    print("\nSetting up MPU6050 motion detection...")
    i2c0 = I2C(0, scl=Pin(MPU6050_SCL_PIN), sda=Pin(MPU6050_SDA_PIN), freq=400000)
    mpu = MPU6050(i2c=i2c0, addr=0x68, use_fake_data=False)
    # Set to maximum ranges
    mpu.set_accel_range(16)   # Acceleration range: ±2 ±4 ±8 ±16g
    mpu.set_gyro_range(2000)  # Gyroscopes range: +/- 250 500 1000 2000 degree/sec
    mpu.set_filter_bandwidth(3)  # Digital low-pass filter: 3 = 44Hz bandwidth
    mpu.set_accel_hpf(1)  # High pass filter: 5Hz (removes DC offset and low frequency vibration)
    mpu.setup_motion_detection(threshold=MOTION_THRESHOLD)
    print(f"Motion threshold set to {MOTION_THRESHOLD * 2}mg ({MOTION_THRESHOLD * 2 / 1000}g)")
    
    # Setup deep sleep wakeup
    print("Configuring deep sleep wakeup on INT pin...")
    int_pin = Pin(MPU6050_INT_PIN, Pin.IN, Pin.PULL_UP)
    esp32.gpio_deep_sleep_hold(True)
    # machine.Pin(MPU6050_INT_PIN).init(pull=machine.Pin.PULL_UP)
    esp32.wake_on_ext0(pin=int_pin, level=esp32.WAKEUP_ALL_LOW)
    
    # Active mode with timeout
    print(f"\nEntering active mode (timeout: {ACTIVE_TIMEOUT_MS}ms)")
    print("Waiting for motion interrupt...")
    print("(Green LED = active, Red LED = sleeping)")
    print("=" * 60)
    
    last_motion_time = time.ticks_ms()
    last_print_time = time.ticks_ms()
    
    try:
        while True:
            current_time = time.ticks_ms()
            time_since_motion = time.ticks_diff(current_time, last_motion_time)
            
            # Check if motion detected
            if int_pin.value() == 0:  # LOW = interrupt triggered
                accel = mpu.get_values()
                # For ±16g range: 1g = 2048 LSB
                accel_mag = ((accel['AcX']**2 + accel['AcY']**2 + accel['AcZ']**2) ** 0.5) / 2048.0
                if DEBUG_ACCEL:
                    print(f"[{current_time}] Motion detected! Accel: X={accel['AcX']:6d}, Y={accel['AcY']:6d}, Z={accel['AcZ']:6d} | Magnitude: {accel_mag:.2f}g (idle: {time_since_motion}ms)")
                else:
                    print(f"[{current_time}] Motion detected! (idle for {time_since_motion}ms)")
                # Clear the latched interrupt
                mpu.clear_motion_interrupt()
                last_motion_time = current_time
                last_print_time = current_time
                time.sleep_ms(100)  # Debounce
            else:
                # Print status every 1 second instead of every 100ms
                if time.ticks_diff(current_time, last_print_time) >= 1000:
                    print(f"[{current_time}] Idle: {time_since_motion}ms / {ACTIVE_TIMEOUT_MS}ms")
                    last_print_time = current_time
                time.sleep_ms(100)
            
            # Check if timeout reached
            if time_since_motion >= ACTIVE_TIMEOUT_MS:
                print(f"\nNo motion for {time_since_motion}ms - entering deep sleep")
                print("=" * 60)
                led.red()  # Turn red LED on before sleeping
                time.sleep_ms(100)  # Brief delay for user to see LED change
                machine.deepsleep()  # Will not return unless woken by interrupt
            # Small delay to prevent busy-waiting
            time.sleep_ms(10)
    
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        led.off()


if __name__ == "__main__":
    main()