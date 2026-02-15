"""
Basketball shot classifier using MPU6050 (acceleration) and VL53L1X (TOF) sensors.

Detects rim/board impacts and basket makes, classifies shots as MAKE or MISS.
Handles events spanning multiple batches using state machine and event buffers.
"""

from collections import deque
import time

# --- Tunable Thresholds ---
class ThresholdConfig:
    """Thresholds for shot detection. Tune these based on your hardware/environment."""
    
    # MPU Impact Detection
    IMPACT_ACCEL_THRESHOLD = 4.0  # g-force, spike above baseline to trigger impact

    # TOF Basket Detection
    TOF_DISTANCE_THRESHOLD = 350  # mm, ball in basket when distance < this
    TOF_SIGNAL_RATE_THRESHOLD = 1000  # signal rate, basketball has high SR

    MAX_TIME_AFTER_IMPACT = 0.5  # seconds, max time to detect basket after impact
    BLACKOUT_WINDOW = 1.0  # seconds, if no basket by this time, classify as MISS

class ShotEvent:
    """Represents a detected event (impact or basket)."""
    def __init__(self, timestamp, event_type, magnitude=None, distance=None, signal_rate=None):
        self.timestamp = timestamp  # seconds
        self.event_type = event_type  # 'impact' or 'basket'
        self.magnitude = magnitude  # accel magnitude for impact events
        self.distance = distance  # distance for basket events
        self.signal_rate = signal_rate  # signal rate for basket events
    
    def __repr__(self):
        return f"ShotEvent({self.event_type} @ {self.timestamp:.3f}s)"


class ShotClassifier:
    """Detects and classifies basketball shots from sensor batches using a state machine."""
    
    # State constants
    STATE_IDLE = 'IDLE'
    STATE_IMPACT_DETECTED = 'IMPACT_DETECTED'
    STATE_BASKET_DETECTED = 'BASKET_DETECTED'
    STATE_BLACKOUT = 'BLACKOUT'
    
    def __init__(self, config=None):
        self.config = config or ThresholdConfig()
        
        # Data queues (timestamp-ordered)
        self.mpu_queue = deque()  # (timestamp, magnitude) tuples
        self.tof_queue = deque()  # (timestamp, distance, signal_rate) tuples
        
        # Shot tracking
        self.completed_shots = []  # fully classified shots
        
        # State machine
        self.state = self.STATE_IDLE  # current state
        self.state_start_time = None  # when we entered current state
        self.impact_time = None  # timestamp of impact (for MAKE/MISS correlation)
    
    def reset(self):
        """Reset classifier state for a new session (playback/recording)."""
        self.mpu_queue.clear()
        self.tof_queue.clear()
        self.completed_shots.clear()
        self.state = self.STATE_IDLE
        self.state_start_time = None
        self.impact_time = None
    
    def process_batch(self, batch, current_time=None):
        """
        Process a batch of samples using state machine.
        
        Args:
            batch: List of dicts with 'accel', 'gyro', 'distance', 'mpu_ts', 'tof_ts', 'signal_rate'
            current_time: Current time in seconds (for testing). If None, uses wall time.
        
        Returns:
            List of newly completed shots: [{impact_time, basket_time, classification, confidence}]
        """
        if current_time is None:
            current_time = time.time()
        
        # Populate queues with batch data
        for sample in batch:
            # Add MPU data
            accel = sample['accel']
            mpu_ts = sample['mpu_ts'] / 1000.0
            magnitude = (accel[0]**2 + accel[1]**2 + accel[2]**2)**0.5
            self.mpu_queue.append((mpu_ts, magnitude))
            
            # Add TOF data (only if valid)
            distance = sample['distance']
            signal_rate = sample['signal_rate']
            tof_ts = sample['tof_ts'] / 1000.0
            
            if not (distance == 0xFFFE or distance == 65534 or distance == 0xFFFF or distance == 65535 or distance == -1):
                self.tof_queue.append((tof_ts, distance, signal_rate))
        
        # Process queues sample-by-sample using state machine
        completed = []
        while self.mpu_queue or self.tof_queue:
            # Pick the sample with smaller timestamp
            mpu_sample = self.mpu_queue[0] if self.mpu_queue else None
            tof_sample = self.tof_queue[0] if self.tof_queue else None
            
            if mpu_sample is None and tof_sample is None:
                break
            
            # Determine which to process
            if mpu_sample is not None and tof_sample is not None:
                if mpu_sample[0] < tof_sample[0]:
                    sample_type = 'mpu'
                    ts, magnitude = self.mpu_queue.popleft()
                else:
                    sample_type = 'tof'
                    ts, distance, signal_rate = self.tof_queue.popleft()
            elif mpu_sample is not None:
                sample_type = 'mpu'
                ts, magnitude = self.mpu_queue.popleft()
            else:
                sample_type = 'tof'
                ts, distance, signal_rate = self.tof_queue.popleft()
            
            # Process sample through state machine
            shot = self._process_sample(sample_type, ts, magnitude if sample_type == 'mpu' else None,
                                       distance if sample_type == 'tof' else None,
                                       signal_rate if sample_type == 'tof' else None)
            if shot:
                completed.append(shot)
        
        self.completed_shots.extend(completed)
        return completed
    
    def _process_sample(self, sample_type, timestamp, magnitude=None, distance=None, signal_rate=None):
        """Process a single sample through the state machine."""
        
        # Check if we need to exit blackout state
        if self.state == self.STATE_BLACKOUT:
            if timestamp >= self.state_start_time + self.config.BLACKOUT_WINDOW:
                self.state = self.STATE_IDLE
                self.state_start_time = None
        
        # Process based on sample type and current state
        shot_completed = None
        
        if self.state == self.STATE_IDLE:
            if sample_type == 'mpu' and magnitude > self.config.IMPACT_ACCEL_THRESHOLD:
                # Transition to impact_detected
                self.state = self.STATE_IMPACT_DETECTED
                self.state_start_time = timestamp
                self.impact_time = timestamp
            elif sample_type == 'tof' and self._is_basket_event(distance, signal_rate):
                # Transition to basket_detected
                self.state = self.STATE_BASKET_DETECTED
                self.state_start_time = timestamp
                # Immediately generate MAKE event
                shot_completed = {
                    'impact_time': None,
                    'basket_time': timestamp,
                    'classification': 'MAKE',
                    'basket_type': 'SWISH',
                    'confidence': 0.85
                }
                self.state = self.STATE_BLACKOUT
                self.state_start_time = timestamp
        
        elif self.state == self.STATE_IMPACT_DETECTED:
            # Check for basket within MAX_TIME_AFTER_IMPACT
            time_since_impact = timestamp - self.impact_time
            
            # Check timeout first: no basket found within MAX_TIME_AFTER_IMPACT
            if time_since_impact > self.config.MAX_TIME_AFTER_IMPACT:
                shot_completed = {
                    'impact_time': self.impact_time,
                    'basket_time': None,
                    'classification': 'MISS',
                    'basket_type': None,
                    'confidence': 0.85
                }
                self.state = self.STATE_BLACKOUT
                self.state_start_time = timestamp
            elif sample_type == 'tof' and self._is_basket_event(distance, signal_rate):
                # Found basket within window - MAKE
                shot_completed = {
                    'impact_time': self.impact_time,
                    'basket_time': timestamp,
                    'classification': 'MAKE',
                    'basket_type': 'BANK',
                    'confidence': 0.95
                }
                self.state = self.STATE_BLACKOUT
                self.state_start_time = timestamp
        
        elif self.state == self.STATE_BASKET_DETECTED:
            # Should not reach here (handled in IDLE), but just in case
            pass
        
        elif self.state == self.STATE_BLACKOUT:
            # Just wait for blackout to expire
            pass
        
        return shot_completed
    
    def _is_basket_event(self, distance, signal_rate):
        """Check if TOF reading indicates basket."""
        return (distance < self.config.TOF_DISTANCE_THRESHOLD and 
                signal_rate > self.config.TOF_SIGNAL_RATE_THRESHOLD)
    
    def get_statistics(self):
        """Return shot statistics."""
        makes = sum(1 for s in self.completed_shots if s['classification'] == 'MAKE')
        misses = sum(1 for s in self.completed_shots if s['classification'] == 'MISS')
        total = makes + misses
        
        percentage = (makes / total * 100) if total > 0 else 0
        
        return {
            'makes': makes,
            'misses': misses,
            'total': total,
            'percentage': percentage
        }
    
    def get_all_shots(self):
        """Return all completed shot classifications."""
        return self.completed_shots.copy()
