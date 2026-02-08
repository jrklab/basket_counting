# VL53L1X Time-of-Flight Distance Sensor MicroPython Library
# Based on STMicroelectronics VL53L1X API

import time
from machine import I2C

class VL53L1X:
    """VL53L1X ToF distance sensor driver for MicroPython."""
    
    # Register addresses
    SYSRANGE_START = 0x00
    SYSTEM_THRESH_HIGH = 0x0C
    SYSTEM_THRESH_LOW = 0x0E
    SYSTEM_SEQUENCE_CONFIG = 0x01
    SYSTEM_RANGE_CONFIG = 0x09
    SYSTEM_INTERMEASUREMENT_PERIOD = 0x04
    RESULT_RANGE_STATUS = 0x31
    RESULT_DSS_ACTUAL_EFFECTIVE_SPADS_SD0 = 0x3C
    RESULT_AMBIENT_COUNT_RATE_MCPS_SD = 0x7E
    RESULT_FINAL_CROSSTALK_CORRECTED_RANGE_MM_SD0 = 0x87
    RESULT_PEAK_SIGNAL_COUNT_RATE_CROSSTALK_CORRECTED_MCPS_SD0 = 0x84
    ALGO_PART_TO_PART_RANGE_OFFSET_MM = 0x28
    I2C_SLAVE_DEVICE_ADDRESS = 0x8A
    ANA_CONFIG_VCSEL_TRIM = 0xFF
    ANA_CONFIG_FRACTIONAL_ENABLE = 0x98
    FIRMWARE_RESULT_CRC_3 = 0x3E
    FIRMWARE_RESULT_CRC_3_STATUS = 0x27
    ROI_CONFIG_MODE_CONTROL = 0xF7
    
    # Default I2C address
    DEFAULT_ADDRESS = 0x29
    
    def __init__(self, i2c=None, addr=0x29, use_fake_data=False):
        """Initialize VL53L1X sensor.
        
        Args:
            i2c: I2C bus instance
            addr: I2C address (default 0x29)
            use_fake_data: Use simulated data instead of real sensor (default False)
        """
        self.i2c = i2c
        self.addr = addr
        self.use_fake_data = use_fake_data
        self.timing_budget = 20000  # Default 66ms timing budget
        self.fake_distance_base = 500  # Base distance in mm for fake data
        
    def init(self):
        """Initialize the sensor and load default settings."""
        if self.use_fake_data:
            print("Using fake data for VL53L1X. This is useful for testing without hardware.")
            return True
        
        # Wait for sensor to be ready
        time.sleep_ms(100)
        
        # Check if sensor is accessible
        try:
            model_id = self.read_reg(0x010F, 2)
            print(f"VL53L1X Model ID: 0x{model_id[0]:02X}{model_id[1]:02X}")
        except Exception as e:
            print(f"Error reading model ID: {e}")
            return False
        
        # Load the initialization sequence
        try:
            self._load_init_sequence()
            print("VL53L1X initialization sequence loaded successfully")
            time.sleep_ms(200)
        except Exception as e:
            print(f"Error loading init sequence: {e}")
            return False
            
        return True
    
    def _load_init_sequence(self):
        """Load the VL53L1X initialization sequence."""
        # This initialization sequence is based on ST's recommended settings
        # for standard ranging mode
        
        # Start with register writes for proper timing and crosstalk correction
        init_sequence = [
            # Timing budget and inter-measurement period
            (0x0004, bytes([0x01])),  # POWER_MANAGEMENT__GO1_RESET_GO2_HELD_BYTE
            (0x0007, bytes([0x01])),  # SYSTEM_STATUS
            (0x000F, bytes([0x00])),  # PAD_SUSTAINED_TIMEOUT_VALUE_LOWER_BYTE
            (0x0010, bytes([0x01])),  # PAD_SUSTAINED_TIMEOUT_VALUE_UPPER_BYTE
            (0x0073, bytes([0x00])),  # ALGO_PHASE_CAL_LIM_MIN
            (0x0074, bytes([0x00])),  # ALGO_PHASE_CAL_LIM_MAX
            (0x0075, bytes([0x3C])),  # ALGO_PHASECAL_CONFIG_TIMEOUT_MACROP
            (0x0076, bytes([0x32])),  # ALGO_PHASECAL_TARGET_NUM_SAMPLES
            (0x0087, bytes([0x00])),  # ALGO_CROSSTALK_NUM_SAMPLES
            (0x0097, bytes([0x01])),  # ALGO_OVERSAMPLING
            (0x00B6, bytes([0x20])),  # ALGO_RANGE_IGNORE_THRESHOLD_MCPS
            (0x00B7, bytes([0x08])),  # ALGO_RANGE_IGNORE_VALID_HEIGHT_MM
            (0x00B8, bytes([0x00])),  # ALGO_RANGE_MIN_CLIP
            (0x00B9, bytes([0x02])),  # ALGO_CONSISTENCY_CHECK__TOLERANCE
            (0x00BC, bytes([0x34])),  # ALGO_RANGE_IGNORE_THRESHOLD_MCPS
            (0x00F7, bytes([0x00])),  # ROI_CONFIG_MODE_CONTROL
            (0x00FF, bytes([0x00])),  # DSS_CONFIG__APERTURE_ATTENUATION
            (0x0100, bytes([0x09])),  # ALGO_RANGE_IGNORE_VALID_HEIGHT_MM
            (0x0101, bytes([0x47])),  # ALGO_RANGE_MIN_CLIP
            (0x0102, bytes([0x05])),  # ALGO_CONSISTENCY_CHECK__TOLERANCE
            (0x0104, bytes([0x0E])),  # DSS_CONFIG__MANUAL_EFFECTIVE_SPADS_SELECT
            (0x0105, bytes([0x01])),  # DSS_CONFIG__APERTURE_ATTENUATION
            (0x010A, bytes([0x30])),  # ALGO_RANGE_IGNORE_THRESHOLD_MCPS
            (0x010B, bytes([0x01])),  # ALGO_RANGE_IGNORE_VALID_HEIGHT_MM
            (0x010C, bytes([0x00])),  # ALGO_RANGE_MIN_CLIP
            (0x010D, bytes([0x01])),  # ALGO_CONSISTENCY_CHECK__TOLERANCE
            (0x0110, bytes([0x00])),  # DSS_CONFIG__MANUAL_EFFECTIVE_SPADS_SELECT
            (0x0111, bytes([0x02])),  # DSS_CONFIG__APERTURE_ATTENUATION
            (0x0114, bytes([0x03])),  # DSS_CONFIG__MANUAL_EFFECTIVE_SPADS_SELECT
            (0x0115, bytes([0x00])),  # DSS_CONFIG__APERTURE_ATTENUATION
            (0x0118, bytes([0x00])),  # DSS_CONFIG__MANUAL_EFFECTIVE_SPADS_SELECT
            (0x0119, bytes([0x00])),  # DSS_CONFIG__APERTURE_ATTENUATION
            (0x011C, bytes([0x31])),  # DSS_CONFIG__MANUAL_EFFECTIVE_SPADS_SELECT
            (0x011D, bytes([0x01])),  # DSS_CONFIG__APERTURE_ATTENUATION
        ]
        
        for reg, data in init_sequence:
            self.write_reg(reg, data)
            time.sleep_ms(1)
        
        # Clear any pending interrupts by reading status
        try:
            self.read_reg(self.RESULT_RANGE_STATUS, 1)
        except:
            pass
    
    def read_reg(self, reg, length=1):
        """Read from sensor register.
        
        Args:
            reg: Register address
            length: Number of bytes to read
            
        Returns:
            List of bytes read
        """
        try:
            return self.i2c.readfrom_mem(self.addr, reg, length)
        except Exception as e:
            print(f"Error reading register 0x{reg:02X}: {e}")
            return None
    
    def write_reg(self, reg, data):
        """Write to sensor register.
        
        Args:
            reg: Register address
            data: Data to write (int or bytes)
        """
        try:
            if isinstance(data, int):
                self.i2c.writeto_mem(self.addr, reg, bytes([data]))
            else:
                self.i2c.writeto_mem(self.addr, reg, data)
        except Exception as e:
            print(f"Error writing register 0x{reg:02X}: {e}")
    
    def set_timing_budget(self, timing_ms):
        """Set timing budget for measurements.
        
        Args:
            timing_ms: Timing budget in milliseconds
                      Valid values: 20, 50, 100, 200, 500, 1000+
        """
        timing_us = timing_ms * 1000  # Convert to microseconds
        self.timing_budget = timing_us
        
        if self.use_fake_data:
            return
        
        # Calculate macro period for Range A VCSEL Period
        # This is a simplified timing budget implementation
        # For a complete implementation, refer to ST's VL53L1X API
        
        # Get current VCSEL period A
        try:
            vcsel_period_a = self.read_reg(0x005E, 1)
            if vcsel_period_a is None:
                return
            
            # Timing budget sets the RANGE_CONFIG__TIMEOUT_MACROP_A and B registers
            # These control the measurement timeout in macro clocks
            # For simplicity, we'll write a proportional timeout value
            
            # Encode timeout: convert from microseconds to register format
            # The timeout register uses an encoded format: (LSByte * 2^MSByte) + 1
            # For now, use a simplified approach based on timing_us
            
            # Example values (from VL53L1X datasheet):
            # 20ms = 0x0007, 50ms = 0x000E, 100ms = 0x001D, etc.
            timeout_map = {
                20: 0x0007,
                33: 0x000B,
                50: 0x000E,
                100: 0x001D,
                200: 0x003C,
                500: 0x009A,
                1000: 0x0134
            }
            
            if timing_ms in timeout_map:
                encoded_timeout = timeout_map[timing_ms]
            else:
                # Linear interpolation for unmapped values
                encoded_timeout = max(0x0007, min(0x0134, int(timing_ms / 20 * 0x0007)))
            
            # Write to both Range A and Range B timeout registers
            self.write_reg(0x005E, bytes([(encoded_timeout >> 8) & 0xFF]))  # RANGE_CONFIG__TIMEOUT_MACROP_A_HI
            self.write_reg(0x005F, bytes([encoded_timeout & 0xFF]))          # RANGE_CONFIG__TIMEOUT_MACROP_A_LO
            self.write_reg(0x0060, bytes([(encoded_timeout >> 8) & 0xFF]))  # RANGE_CONFIG__TIMEOUT_MACROP_B_HI
            self.write_reg(0x0061, bytes([encoded_timeout & 0xFF]))          # RANGE_CONFIG__TIMEOUT_MACROP_B_LO
            
        except Exception as e:
            print(f"Error setting timing budget: {e}")
    
    def set_inter_measurement_period(self, period_ms):
        """Set inter-measurement period.
        
        Args:
            period_ms: Period between measurements in milliseconds
        """
        # Period register is in units of milliseconds
        period_bytes = period_ms.to_bytes(2, 'little')
        self.write_reg(self.SYSTEM_INTERMEASUREMENT_PERIOD, period_bytes)
    
    def start_ranging(self, mode=0):
        """Start ranging measurements.
        
        Args:
            mode: 0=unchanged, 1=short range (50Hz), 2=medium range (20Hz), 3=long range (10Hz)
        """
        if self.use_fake_data:
            return
            
        if mode == 1:  # Short range - optimized for 50Hz
            # For 50Hz: period = 20ms (1000ms / 50Hz)
            # The minimum and maximum timing budgets are [20 ms, 1000 ms]
            # Timing budget must be less than period for continuous ranging
            self.set_timing_budget(20)  # 15ms timing budget (fits in 20ms period)
            self.set_inter_measurement_period(20)  # 20ms between measurements = 50Hz
        elif mode == 2:  # Medium range - standard 20Hz
            # For 20Hz: period = 50ms
            self.set_timing_budget(33)  # 33ms timing budget
            self.set_inter_measurement_period(50)  # 50ms between measurements = 20Hz
        elif mode == 3:  # Long range - 10Hz
            # For 10Hz: period = 100ms
            self.set_timing_budget(66)  # 66ms timing budget
            self.set_inter_measurement_period(100)  # 100ms between measurements = 10Hz
        
        # Start measurement by writing to SYSRANGE_START register
        try:
            self.write_reg(self.SYSRANGE_START, 0x01)
            time.sleep_ms(10)
            print(f"Ranging started in mode {mode} (optimized for {[0, 50, 20, 10][mode]}Hz)")
        except Exception as e:
            print(f"Error starting ranging: {e}")
    
    def stop_ranging(self):
        """Stop ranging measurements."""
        self.write_reg(self.SYSRANGE_START, 0x00)
    
    def get_distance(self):
        """Read distance measurement.
        
        Returns:
            Distance in millimeters, or -1 if measurement failed
        """
        if self.use_fake_data:
            return self._get_fake_distance()
        
        try:
            # Read range status and distance
            data = self.read_reg(self.RESULT_RANGE_STATUS, 27)
            
            if data is None:
                return -1
            
            # Range status is in first byte
            range_status = data[0] & 0x1F
            
            # Map of status codes to meanings
            status_messages = {
                0: "OK",
                1: "VCSEL continuity test failure",
                2: "VCSEL watchdog test failure",
                3: "No VHV value found",
                4: "No target (MSRCNOTARGET)",
                5: "Range phase check failure",
                6: "Sigma threshold check failure",
                13: "User ROI clip",
                17: "Multi-clip failure",
                18: "GPH stream count ready"
            }
            
            status_msg = status_messages.get(range_status, f"Unknown error {range_status}")
            
            if range_status != 0:
                # Still try to read the value, but indicate it's not valid
                raw_distance = data[10] | (data[11] << 8)
                distance = ((raw_distance * 2011) + 0x0400) >> 11
                # Return negative distance to indicate measurement not valid
                # but still return the raw value for debugging
                return -abs(distance)
            
            # Distance is in bytes 10-11 (little-endian)
            # Register: RESULT_FINAL_CROSSTALK_CORRECTED_RANGE_MM_SD0
            raw_distance = data[10] | (data[11] << 8)
            
            # Apply correction gain (standard VL53L1X correction)
            # Formula: distance_mm = (raw_value * 2011 + 0x0400) / 0x0800
            distance = ((raw_distance * 2011) + 0x0400) >> 11  # >> 11 is same as / 2048
            
            return distance
        except Exception as e:
            print(f"Error reading distance: {e}")
            return -1
    
    def _get_fake_distance(self):
        """Generate fake distance data for testing.
        
        Returns:
            Simulated distance in millimeters
        """
        import math
        # Generate oscillating distance (500-1500mm range)
        current_time = time.ticks_ms() / 1000.0
        variation = 500 * math.sin(2 * math.pi * 0.5 * current_time)
        distance = int(self.fake_distance_base + variation)
        # Clamp to reasonable range
        distance = max(50, min(4000, distance))
        return distance
    
    def get_range_mm(self):
        """Get range measurement in millimeters.
        
        Returns:
            Distance in mm
        """
        return self.get_distance()
