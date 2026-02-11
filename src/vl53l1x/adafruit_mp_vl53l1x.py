import struct
import time
from machine import Pin
from micropython import const

# Register Constants (Matching your uploaded file)
_VL53L1X_VHV_CONFIG__TIMEOUT_MACROP_LOOP_BOUND = const(0x0008)
_GPIO_HV_MUX__CTRL = const(0x0030)
_GPIO__TIO_HV_STATUS = const(0x0031)
_PHASECAL_CONFIG__TIMEOUT_MACROP = const(0x004B)
_RANGE_CONFIG__TIMEOUT_MACROP_A_HI = const(0x005E)
_RANGE_CONFIG__VCSEL_PERIOD_A = const(0x0060)
_RANGE_CONFIG__TIMEOUT_MACROP_B_HI = const(0x0061)
_RANGE_CONFIG__VCSEL_PERIOD_B = const(0x0063)
_RANGE_CONFIG__VALID_PHASE_HIGH = const(0x0069)
_SD_CONFIG__WOI_SD0 = const(0x0078)
_SD_CONFIG__INITIAL_PHASE_SD0 = const(0x007A)
_SYSTEM__INTERRUPT_CLEAR = const(0x0086)
_SYSTEM__MODE_START = const(0x0087)
_VL53L1X_RESULT__RANGE_STATUS = const(0x0089)
_VL53L1X_RESULT__FINAL_CROSSTALK_CORRECTED_RANGE_MM_SD0 = const(0x0096)
_VL53L1X_IDENTIFICATION__MODEL_ID = const(0x010F)

# Timing Budget Tables (Extracted from your source)
TB_SHORT_DIST = {
    15: (b"\x00\x1d", b"\x00\x27"), 20: (b"\x00\x51", b"\x00\x6e"),
    33: (b"\x00\xd6", b"\x00\x6e"), 50: (b"\x01\xae", b"\x01\xe8"),
    100: (b"\x02\xe1", b"\x03\x88"), 200: (b"\x03\xe1", b"\x04\x96"),
    500: (b"\x05\x91", b"\x05\xc1"),
}

TB_LONG_DIST = {
    20: (b"\x00\x1e", b"\x00\x22"), 33: (b"\x00\x60", b"\x00\x6e"),
    50: (b"\x00\xad", b"\x00\xc6"), 100: (b"\x01\xcc", b"\x01\xea"),
    200: (b"\x02\xd9", b"\x02\xf8"), 500: (b"\x04\x8f", b"\x04\xa4"),
}

class VL53L1X:
    def __init__(self, i2c, address=0x29):
        self._i2c = i2c
        self._address = address
        
        # Verify Sensor Identity
        info = self._read_register(_VL53L1X_IDENTIFICATION__MODEL_ID, 3)
        if info[0] != 0xEA or info[1] != 0xCC or info[2] != 0x10:
            raise RuntimeError("Wrong sensor ID or type! Check power/wiring.")
            
        self._sensor_init()
        self._timing_budget = None
        self.distance_mode = 1 # Default to short
        self.timing_budget = 50

    def _sensor_init(self):
        # Full initialization sequence from your file
        init_seq = bytes([
            0x00, 0x00, 0x00, 0x01, 0x02, 0x00, 0x02, 0x08, 0x00, 0x08,
            0x10, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x0F,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x20, 0x0B, 0x00, 0x00, 0x02,
            0x0A, 0x21, 0x00, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0xC8,
            0x00, 0x00, 0x38, 0xFF, 0x01, 0x00, 0x08, 0x00, 0x00, 0x01,
            0xCC, 0x0F, 0x01, 0xF1, 0x0D, 0x01, 0x68, 0x00, 0x80, 0x08,
            0xB8, 0x00, 0x00, 0x00, 0x00, 0x0F, 0x89, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x01, 0x0F, 0x0D, 0x0E, 0x0E, 0x00,
            0x00, 0x02, 0xC7, 0xFF, 0x9B, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00
        ])
        self._write_register(0x002D, init_seq)
        self.start_ranging()
        while not self.data_ready:
            time.sleep(0.01)
        self.clear_interrupt()
        self.stop_ranging()
        self._write_register(_VL53L1X_VHV_CONFIG__TIMEOUT_MACROP_LOOP_BOUND, b"\x09")
        self._write_register(0x0B, b"\x00")

    @property
    def distance(self):
        """Distance in centimeters. Returns -1 if out of range or no target."""
        # Check range status
        status = self._read_register(_VL53L1X_RESULT__RANGE_STATUS)[0]
        
        # Status 0x09 is valid, but we should also check for other 
        # usable statuses if we want to avoid hanging.
        if status not in (0x09, 0x00): 
            return -1 # Indicate no valid target
            
        raw = self._read_register(_VL53L1X_RESULT__FINAL_CROSSTALK_CORRECTED_RANGE_MM_SD0, 2)
        dist = struct.unpack(">H", raw)[0]
        
        # If distance is unusually high (8190/8191), it's a "no target" signal
        if dist > 4000:
            return -1
            
        return dist

    def start_ranging(self):
        """Starts ranging operation and ensures interrupts are clean."""
        self.clear_interrupt() # Clear any stale interrupts before starting
        self._write_register(_SYSTEM__MODE_START, b"\x40")
        time.sleep_ms(10)      # Brief pause for the state machine to transition

    def stop_ranging(self):
        self._write_register(_SYSTEM__MODE_START, b"\x00")

    def clear_interrupt(self):
        self._write_register(_SYSTEM__INTERRUPT_CLEAR, b"\x01")

    # @property
    # def data_ready(self):
    #     int_pol = self._read_register(_GPIO_HV_MUX__CTRL)[0] & 0x10
    #     polarity = 0 if ((int_pol >> 4) & 0x01) else 1
    #     return self._read_register(_GPIO__TIO_HV_STATUS)[0] & 0x01 == polarity
    
    @property
    def data_ready(self):
        """Checks if data is ready without blocking."""
        int_pol = self._read_register(_GPIO_HV_MUX__CTRL)[0] & 0x10
        polarity = 0 if ((int_pol >> 4) & 0x01) else 1
        
        # Check the status register bit 0
        res = self._read_register(_GPIO__TIO_HV_STATUS)[0] & 0x01
        return res == polarity

    @property
    def timing_budget(self):
        return self._timing_budget

    @timing_budget.setter
    def timing_budget(self, val):
        reg_vals = TB_SHORT_DIST if self.distance_mode == 1 else TB_LONG_DIST
        if val not in reg_vals:
            raise ValueError("Invalid timing budget. Use 15, 20, 33, 50, 100, 200, 500.")
        self._write_register(_RANGE_CONFIG__TIMEOUT_MACROP_A_HI, reg_vals[val][0])
        self._write_register(_RANGE_CONFIG__TIMEOUT_MACROP_B_HI, reg_vals[val][1])
        self._timing_budget = val

    @property
    def distance_mode(self):
        mode = self._read_register(_PHASECAL_CONFIG__TIMEOUT_MACROP)[0]
        if mode == 0x14: return 1
        if mode == 0x0A: return 2
        return None

    @distance_mode.setter
    def distance_mode(self, mode):
        if mode == 1:
            self._write_register(_PHASECAL_CONFIG__TIMEOUT_MACROP, b"\x14")
            self._write_register(_RANGE_CONFIG__VCSEL_PERIOD_A, b"\x07")
            self._write_register(_RANGE_CONFIG__VCSEL_PERIOD_B, b"\x05")
            self._write_register(_RANGE_CONFIG__VALID_PHASE_HIGH, b"\x38")
            self._write_register(_SD_CONFIG__WOI_SD0, b"\x07\x05")
            self._write_register(_SD_CONFIG__INITIAL_PHASE_SD0, b"\x06\x06")
        elif mode == 2:
            self._write_register(_PHASECAL_CONFIG__TIMEOUT_MACROP, b"\x0a")
            self._write_register(_RANGE_CONFIG__VCSEL_PERIOD_A, b"\x0f")
            self._write_register(_RANGE_CONFIG__VCSEL_PERIOD_B, b"\x0d")
            self._write_register(_RANGE_CONFIG__VALID_PHASE_HIGH, b"\xb8")
            self._write_register(_SD_CONFIG__WOI_SD0, b"\x0f\x0d")
            self._write_register(_SD_CONFIG__INITIAL_PHASE_SD0, b"\x0e\x0e")
        else:
            raise ValueError("Mode must be 1 (short) or 2 (long)")
        if self._timing_budget:
            self.timing_budget = self._timing_budget

    def set_inter_measurement_period(self, period_ms):
        """Sets the period between measurements in ms."""
        # Reg 0x6C is 32-bit (4 bytes) as per your source comments
        val = struct.pack(">I", period_ms)
        self._write_register(0x006C, val)

    def config_sequence(self, distance_mode, timing_budget, measurement_interval_ms):
        """ simplify the code
        """
        self.distance_mode = distance_mode
        self.timing_budget = timing_budget
        self.set_inter_measurement_period(measurement_interval_ms)

    def _write_register(self, address, data):
        # Standard MicroPython 16-bit register write
        self._i2c.writeto_mem(self._address, address, data, addrsize=16)

    def _read_register(self, address, length=1):
        # Standard MicroPython 16-bit register read
        return self._i2c.readfrom_mem(self._address, address, length, addrsize=16)
    
    def reboot(self, xshut_pin_number):
        """Performs a full hardware reboot of the sensor using XSHUT."""
        xshut = Pin(xshut_pin_number, Pin.OUT)
        
        # 1. Pull XSHUT low to power down the sensor logic
        xshut.value(0)
        time.sleep_ms(50) # Reduced from 150ms for faster reboot
        
        # 2. Release XSHUT
        xshut.value(1)
        time.sleep_ms(100) # Reduced from 150ms - wait for the sensor internal bootloader
        
        # 3. Re-run the essential initialization sequence
        self._sensor_init()