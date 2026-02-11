"""Hardware configuration for basket counting ESP32 system."""
import neopixel
import machine

# MPU6050 I2C pins and configuration
MPU6050_SCL_PIN = 10
MPU6050_SDA_PIN = 9
MPU6050_INT_PIN = 18

# VL53L1X I2C pins and configuration
VL53L1X_SCL_PIN = 6
VL53L1X_SDA_PIN = 7
VL53L1X_XSHUT_PIN = 4

# VL53L1X sensor settings
VL53L1X_DISTANCE_MODE_SHORT = 1
VL53L1X_TIMING_BUDGET_MS = 33
VL53L1X_MEASUREMENT_INTERVAL_MS = 40
VL53L1X_TIMEOUT_MS = VL53L1X_MEASUREMENT_INTERVAL_MS  # 40ms timeout for VL53L1X data_ready
# RGB LED (WS2812 NeoPixel)
RGB_LED_PIN = 48
RGB_LED_NUM_PIXELS = 1


class RGBLed:
    """Simple RGB LED controller using WS2812 NeoPixel."""
    
    def __init__(self, pin, num_pixels=1):
        self.pixel = neopixel.NeoPixel(machine.Pin(pin), num_pixels)
    
    def set_color(self, r, g, b):
        """Set RGB color."""
        self.pixel[0] = (r, g, b)
        self.pixel.write()
    
    def red(self):
        """Turn red."""
        self.set_color(50, 0, 0)
    
    def green(self):
        """Turn green."""
        self.set_color(0, 50, 0)
    
    def blue(self):
        """Turn blue."""
        self.set_color(0, 0, 50)
    
    def off(self):
        """Turn off."""
        self.set_color(0, 0, 0)
