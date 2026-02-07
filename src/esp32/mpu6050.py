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
    def __init__(self, i2c=None, addr=0x68):
        # self.i2c = i2c
        # self.addr = addr
        # self.i2c.start()
        # self.i2c.writeto(self.addr, bytearray([107, 0]))
        # self.i2c.stop()
        self.start_time = time.ticks_ms()

    # def get_raw_values(self):
    #     """
    #     Gets the raw values from the MPU6050
    #     :return:
    #     """
    #     self.i2c.start()
    #     a = self.i2c.readfrom_mem(self.addr, 0x3B, 14)
    #     self.i2c.stop()
    #     return a

    # def get_ints(self):
    #     """
    #     Gets the values as integers
    #     :return:
    #     """
    #     b = self.get_raw_values()
    #     c = []
    #     for i in b:
    #         c.append(i)
    #     return c

    # def bytes_toint(self, firstbyte, secondbyte):
    #     """
    #     Convert two bytes to a signed integer.
    #     :param firstbyte:
    #     :param secondbyte:
    #     :return:
    #     """
    #     if not firstbyte & 0x80:
    #         return firstbyte << 8 | secondbyte
    #     return - (((firstbyte ^ 255) << 8) | (secondbyte ^ 255) + 1)

    def get_values(self):
        """
        Get the values from the MPU-6050
        :return:
        """
        # raw_ints = self.get_raw_values()
        # vals = {}
        # vals["AcX"] = self.bytes_toint(raw_ints[0], raw_ints[1])
        # vals["AcY"] = self.bytes_toint(raw_ints[2], raw_ints[3])
        # vals["AcZ"] = self.bytes_toint(raw_ints[4], raw_ints[5])
        # vals["Tmp"] = self.bytes_toint(raw_ints[6], raw_ints[7]) / 340.00 + 36.53
        # vals["GyX"] = self.bytes_toint(raw_ints[8], raw_ints[9])
        # vals["GyY"] = self.bytes_toint(raw_ints[10], raw_ints[11])
        # vals["GyZ"] = self.bytes_toint(raw_ints[12], raw_ints[13])
        # return vals  # returned in range of Int16
        # -32768 to 32767

        # Fake data generation
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
