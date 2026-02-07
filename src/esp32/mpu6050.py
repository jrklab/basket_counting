# MPU6050 6-DoF IMU Accelerometer and Gyroscope Micropython Library
#
# Author: Adam Jezek <adamA.jezek@gmail.com>
# License: MIT
#
# This library is based on the work of:
#  - https://github.com/m-rtijn/mpu6050
#  - https://github.com/micropython-IMU/micropython-mpu9250
#  - https://github.com/aybese/MPU6050-IMU-micropython
#
import math
import time
from machine import I2C

class MPU6050():
    """
    MPU6050 driver for MicroPython.
    """
    def __init__(self, i2c=None, addr=0x68, use_fake_data=True):
        self.i2c = i2c
        self.addr = addr
        self.use_fake_data = use_fake_data
        if not use_fake_data:
            self.i2c.writeto_mem(self.addr, 107, bytearray([0]))
        if use_fake_data:
            print("Using fake data for MPU6050. This is useful for testing without hardware.")
        else:
            print("MPU6050 initialized with real sensor data.")
        self.start_time = time.ticks_ms()

    def set_accel_range(self, accel_range):
        """
        Set the accelerometer full scale range.
        :param accel_range: 2, 4, 8, or 16 (in g)
        """
        if accel_range == 2:
            value = 0x00
        elif accel_range == 4:
            value = 0x08
        elif accel_range == 8:
            value = 0x10
        elif accel_range == 16:
            value = 0x18
        else:
            raise ValueError("Accelerometer range must be 2, 4, 8, or 16")
        self.i2c.writeto_mem(self.addr, 0x1C, bytearray([value]))

    def set_gyro_range(self, gyro_range):
        """
        Set the gyroscope full scale range.
        :param gyro_range: 250, 500, 1000, or 2000 (in degree/sec)
        """
        if gyro_range == 250:
            value = 0x00
        elif gyro_range == 500:
            value = 0x08
        elif gyro_range == 1000:
            value = 0x10
        elif gyro_range == 2000:
            value = 0x18
        else:
            raise ValueError("Gyroscope range must be 250, 500, 1000, or 2000")
        self.i2c.writeto_mem(self.addr, 0x1B, bytearray([value]))

    def get_raw_values(self):
        """
        Gets the raw values from the MPU6050
        :return:
        """
        a = self.i2c.readfrom_mem(self.addr, 0x3B, 14)
        return a

    def get_ints(self):
        """
        Gets the values as integers
        :return:
        """
        b = self.get_raw_values()
        c = []
        for i in b:
            c.append(i)
        return c

    def bytes_toint(self, firstbyte, secondbyte):
        """
        Convert two bytes to a signed integer.
        :param firstbyte:
        :param secondbyte:
        :return:
        """
        if not firstbyte & 0x80:
            return firstbyte << 8 | secondbyte
        return - (((firstbyte ^ 255) << 8) | (secondbyte ^ 255) + 1)

    def get_values(self):
        """
        Get the values from the MPU-6050
        :return:
        """
        if self.use_fake_data:
            return self._get_fake_values()
        else:
            return self._get_real_values()
    
    def _get_real_values(self):
        """
        Read real values from the MPU-6050 via I2C
        :return:
        """
        raw_ints = self.get_raw_values()
        vals = {}
        vals["AcX"] = self.bytes_toint(raw_ints[0], raw_ints[1])
        vals["AcY"] = self.bytes_toint(raw_ints[2], raw_ints[3])
        vals["AcZ"] = self.bytes_toint(raw_ints[4], raw_ints[5])
        vals["Tmp"] = self.bytes_toint(raw_ints[6], raw_ints[7]) / 340.00 + 36.53
        vals["GyX"] = self.bytes_toint(raw_ints[8], raw_ints[9])
        vals["GyY"] = self.bytes_toint(raw_ints[10], raw_ints[11])
        vals["GyZ"] = self.bytes_toint(raw_ints[12], raw_ints[13])
        return vals  # returned in range of Int16
        # -32768 to 32767
    
    def _get_fake_values(self):
        """
        Generate fake sensor data for testing
        :return:
        """
        current_time = time.ticks_diff(time.ticks_ms(), self.start_time) / 1000.0
        vals = {}
        vals["AcX"] = int(16384 * math.sin(2 * math.pi * 1 * current_time))
        vals["AcY"] = int(16384 * math.cos(2 * math.pi * 1 * current_time))
        vals["AcZ"] = int(16384 * math.sin(2 * math.pi * 0.5 * current_time))
        vals["Tmp"] = 25.0
        vals["GyX"] = int(131 * math.cos(2 * math.pi * 0.5 * current_time))
        vals["GyY"] = int(131 * math.sin(2 * math.pi * 1 * current_time))
        vals["GyZ"] = int(131 * math.cos(2 * math.pi * 1 * current_time))
        return vals

    def val_test(self):
        """
        Test the values and return Gs and deg/s
        :return:
        """
        # val_test is not reading the correct values from the MPU6050. It is
        # returning a constant value of 255 for all values except for the
        # temperature. This is a known issue with the MPU6050 and this
        # library. Please use get_values() instead.
        # Accelerometer
        # AcX = raw_ac_x / 16384
        # AcY = raw_ac_y / 16384
        # AcZ = raw_ac_z / 16384
        # Gyroscope
        # GyX = raw_gy_x / 131
        # GyY = raw_gy_y / 131
        # GyZ = raw_gy_z / 131
        #
        # return {"AcX": AcX, "AcY": AcY, "AcZ": AcZ, "GyX": GyX, "GyY": GyY, "GyZ": GyZ}
        pass
