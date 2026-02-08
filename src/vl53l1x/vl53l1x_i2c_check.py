from machine import I2C, Pin
import time

i2c = I2C(0, sda=Pin(5), scl=Pin(4), freq=100000)

# 0x010F is the Model ID register for VL53L1X
# It should return 0xEA (234)
print("Scanning I2C bus...")
devices = i2c.scan()
print(f"Devices found: {[hex(d) for d in devices]}")
# MPU6050 (Works with 8-bit)
who_am_i_mpu = i2c.readfrom_mem(0x68, 0x75, 1) # addrsize defaults to 8
print(f"MPU6050 WHO_AM_I: {hex(who_am_i_mpu[0])}") # Should be 0x68
# VL53L1X (Fails with 8-bit, needs 16-bit)
# 0x010F is the Model ID register
try:
    model_id = i2c.readfrom_mem(0x29, 0x010F, 1, addrsize=16) 
    print(f"VL53L1X Model ID: {hex(model_id[0])}") # Should be 0xEA
    # read module type (0x0110)
    module_type = i2c.readfrom_mem(0x29, 0x0110, 1, addrsize=16)
    print(f"VL53L1X Module Type: {hex(module_type[0])}")
    # read mask revision (0x0111)
    mask_revision = i2c.readfrom_mem(0x29, 0x0111, 1, addrsize=16)
    print(f"VL53L1X Mask Revision: {hex(mask_revision[0])}")
    # check data readiness (0x0031)
    while True:
        data_ready = i2c.readfrom_mem(0x29, 0x0031, 1, addrsize=16)
        print(f"VL53L1X Data Ready: {hex(data_ready[0])}")
        time.sleep(0.1)
        # read out distance (0x0096-0x0097)
        # if (data_ready[0] & 0x01) != 0:
        distance = i2c.readfrom_mem(0x29, 0x0096, 2, addrsize=16)
        distance_mm = (distance[0] << 8) | distance[1]
        print(f"Distance: {distance_mm} mm")
        # clear interrupt (0x0086)
        i2c.writeto_mem(0x29, 0x0086, bytearray([0x01]), addrsize=16)
except:
    print("VL53L1X failed to read!")
try:
    # Essential: addrsize=16
    data = i2c.readfrom_mem(0x29, 0x010F, 1, addrsize=16)
    print(f"Model ID Raw: {data}")
    print(f"Model ID Hex: {data.hex()}")
except Exception as e:
    print(f"Error: {e}")