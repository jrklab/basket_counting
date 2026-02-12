from machine import I2C, Pin
import adafruit_mp_vl53l1x
import time
from hardware_config import VL53L1X_SCL_PIN, VL53L1X_SDA_PIN, VL53L1X_XSHUT_PIN

i2c = I2C(0, sda=Pin(VL53L1X_SDA_PIN), scl=Pin(VL53L1X_SCL_PIN), freq=400000)
vl53 = adafruit_mp_vl53l1x.VL53L1X(i2c)

measurement_interval_ms = 40
distance_mode = 1 # Short
timing_budget = 33 # ms
vl53.config_sequence(distance_mode, timing_budget, measurement_interval_ms)
vl53.start_ranging()

last_time = time.ticks_ms()

try:
    while True:
        # 1. Non-blocking check for data
        timeout_start = time.ticks_ms()
        ready = False
        
        # Wait up to 50ms for the sensor (more than our 20ms period)
        while time.ticks_diff(time.ticks_ms(), timeout_start) < measurement_interval_ms:
            if vl53.data_ready:
                ready = True
                break
            time.sleep_ms(1)

        if ready:
            measurement = vl53.get_measurement()
            current_time = time.ticks_ms()
            dt = time.ticks_diff(current_time, last_time)
            
            # Only report valid data when range_status is 0 or 9
            freq = 1000 / dt if dt > 0 else 0
            if measurement['range_status'] in (0, 9):
                print(f"Dist: {measurement['range']:>5} mm | Signal: {measurement['signal_rate']:>5} | Ambient: {measurement['ambient_rate']:>5} | SPAD: {measurement['spad_count']:>5} | Status: {measurement['range_status']:>2} | Freq: {freq:>4.1f} Hz")
            else:
                print(f"Invalid measurement - Range Status: {measurement['range_status']} (expected 0 or 9)")
                print(f"Dist: {measurement['range']:>5} mm | Signal: {measurement['signal_rate']:>5} | Ambient: {measurement['ambient_rate']:>5} | SPAD: {measurement['spad_count']:>5} | Status: {measurement['range_status']:>2} | Freq: {freq:>4.1f} Hz")
            
            vl53.clear_interrupt()
            last_time = current_time
        else:
            # HARDWARE RECOVERY
            print("Sensor Unresponsive. Initiating Hardware Reboot...")
            vl53.reboot(xshut_pin_number=VL53L1X_XSHUT_PIN)
            time.sleep_ms(50) # Wait for reboot to complete
            # 4. Restore your specific 50Hz settings
            vl53.config_sequence(distance_mode, timing_budget, measurement_interval_ms)
            vl53.start_ranging()
            # Skip the immediate next check to let the sensor settle
            last_time = time.ticks_ms()
            time.sleep_ms(25)

except KeyboardInterrupt:
    vl53.stop_ranging()