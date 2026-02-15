# Basketball Shot Classifier - State Machine Design

## Overview

The `ShotClassifier` uses a **finite state machine (FSM)** to detect and classify basketball shots from real-time sensor data. It processes MPU6050 (accelerometer) and VL53L1X (TOF distance) samples in timestamp order, correlating impact events with basket detection to classify shots as MAKE or MISS.

## Architecture

### Data Flow

```
UDP Packets (or CSV file)
    ↓
[Batch of 20 samples]
    ↓
ShotClassifier.process_batch()
    ├─ Populate mpu_queue (timestamp, magnitude)
    ├─ Populate tof_queue (timestamp, distance, signal_rate)
    ├─ Process samples by increasing timestamp
    └─ Return completed shot dicts
    ↓
[MAKE/MISS events] → GUI visualization & statistics
```

### Key Components

1. **Two input queues** (FIFO, timestamp-ordered):
   - `mpu_queue`: (timestamp, magnitude) tuples from accelerometer
   - `tof_queue`: (timestamp, distance, signal_rate) tuples from TOF sensor
   - Invalid TOF data (0xFFFF, 0xFFFE) is filtered out at ingestion

2. **State machine** with 4 states:
   - `STATE_IDLE`: Waiting for event
   - `STATE_IMPACT_DETECTED`: Impact detected, awaiting basket
   - `STATE_BASKET_DETECTED`: Basket detected without impact
   - `STATE_BLACKOUT`: Cooldown period, preventing rapid re-triggers

3. **Shot tracking**:
   - `completed_shots`: List of fully classified shots (MAKE/MISS)

## State Definitions

### IDLE State
**Entry**: System start, or after BLACKOUT window expires
**Function**: Waits for either impact or standalone basket detection
**Transitions**:
- → `IMPACT_DETECTED` if MPU magnitude > `IMPACT_ACCEL_THRESHOLD` (5.0g)
- → `BASKET_DETECTED` if TOF detects basket (distance < 350mm AND signal_rate > 1000)

**Action**: None (passive waiting)

### IMPACT_DETECTED State
**Entry**: Accelerometer spike detected in IDLE
**Function**: Looks for basket within correlation window
**Key Variables**:
- `impact_time`: Timestamp of acceleration spike
- `state_start_time`: When impact was detected

**Transitions**:
- → `BLACKOUT` with **MAKE** event if:
  - TOF detects basket AND time since impact ≤ 0.5s
  - Basket type: "Swish" (SR > 1200) or "Bank" (SR ≤ 1200)
  - Confidence: 0.95
  
- → `BLACKOUT` with **MISS** event if:
  - No basket detected AND time since impact > 0.5s
  - Confidence: 0.85

**Timeout**: 0.5 seconds (`MAX_TIME_AFTER_IMPACT`)

### BASKET_DETECTED State
**Entry**: Basket detected while in IDLE (standalone detection)
**Function**: Immediately emit MAKE event
**Transitions**:
- → `BLACKOUT` with **MAKE** event
  - Basket type: "Swish" or "Bank" (based on signal_rate)
  - Confidence: 0.85 (lower than impact+basket, as no impact confirmation)

### BLACKOUT State
**Entry**: After shot completion (MAKE or MISS)
**Function**: Prevents rapid re-triggering of same event
**Key Variables**:
- `state_start_time`: When blackout started
- Duration: 1.0 second (`BLACKOUT_WINDOW`)

**Transitions**:
- → `IDLE` when current_timestamp ≥ state_start_time + 1.0s

**Action**: Ignore all samples during blackout

## State Transition Diagram

```
                    ┌──────────────────────────────────────┐
                    │           IDLE                       │
                    │   (waiting for event)                │
                    └──────────────┬───────────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    │                             │
        MPU > 3.0g  │                             │  Basket detected
                    ↓                             ↓
            ┌──────────────────┐      ┌─────────────────────┐
            │ IMPACT_DETECTED  │      │ BASKET_DETECTED     │
            │ (wait 500ms)     │      │ emit MAKE (SWISH)   │
            └────────┬─────────┘      └────────┬────────────┘
                     │                         │
        ┌────────────┴─────────────────────────┘
        │
        │ TOF detected      No TOF detected
        │ within 500ms      after 500ms
        │
        ↓                   ↓
   ┌──────────┐         ┌─────────┐
   │  MAKE    │         │  MISS   │
   │ (BANK)   │         │         │
   │ conf:0.95│         │conf:0.85│
   └────┬─────┘         └────┬────┘
        │                   │
        └────────┬──────────┘
                 │
                 ↓
        ┌──────────────────┐
        │    BLACKOUT      │
        │  (1.0s cooldown) │
        └────────┬─────────┘
                 │
        timeout  │
                 ↓
              IDLE
```

## Sample Processing Logic

### Per-Sample Processing (`_process_sample`)

```
for each sample in timestamp order:
    if current_state == BLACKOUT:
        if timestamp >= state_start_time + 1.0s:
            transition to IDLE
            clear state_start_time
    
    if current_state == IDLE:
        if sample is MPU and magnitude > 5.0g:
            transition to IMPACT_DETECTED
            set impact_time = timestamp
            set state_start_time = timestamp
        
        elif sample is TOF and is_basket_event():
            emit MAKE event
            determine basket_type (Swish/Bank)
            transition to BLACKOUT
            set state_start_time = timestamp
    
    elif current_state == IMPACT_DETECTED:
        time_since_impact = timestamp - impact_time
        
        if time_since_impact > 0.5s:
            emit MISS event
            transition to BLACKOUT
            set state_start_time = timestamp
        
        elif sample is TOF and is_basket_event():
            emit MAKE event
            determine basket_type
            transition to BLACKOUT
            set state_start_time = timestamp
    
    elif current_state == BASKET_DETECTED:
        # Should not reach here, but wait if it does
        do nothing
    
    elif current_state == BLACKOUT:
        # Ignore all samples during blackout
        do nothing
```

## Shot Event Structure

### MAKE Event (Standalone Basket - SWISH)
```python
{
    'impact_time': None,                    # No impact detected
    'basket_time': float (seconds),         # Time of basket detection
    'classification': 'MAKE',
    'basket_type': 'SWISH',                 # Direct basket without impact
    'confidence': 0.85
}
```

### MAKE Event (Impact + Basket - BANK)
```python
{
    'impact_time': float (seconds),         # Time of detected impact
    'basket_time': float (seconds),         # Time of basket detection
    'classification': 'MAKE',
    'basket_type': 'BANK',                  # Basket after rim/board contact
    'confidence': 0.95
}
```

### MISS Event
```python
{
    'impact_time': float (seconds),         # Time of detected impact
    'basket_time': None,                    # No basket detected
    'classification': 'MISS',
    'basket_type': None,
    'confidence': 0.85                      # Timeout-based classification
}
```

## Basket Type Classification

**SWISH**: Assigned when basket detected **without prior impact**
- Indicates ball went directly into basket
- No rim/board contact detected before basket
- Lower confidence (0.85) due to missing impact confirmation

**BANK**: Assigned when basket detected **after impact**
- Indicates rim/board contact followed by successful make
- Impact detected before basket detection
- Higher confidence (0.95) due to dual-sensor confirmation

## Timing Parameters

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `IMPACT_ACCEL_THRESHOLD` | 5.0 g | Minimum acceleration to trigger impact |
| `MAX_TIME_AFTER_IMPACT` | 0.5 s | Maximum time basket can follow impact |
| `BLACKOUT_WINDOW` | 1.0 s | Cooldown between shots |
| `TOF_DISTANCE_THRESHOLD` | 350 mm | Distance threshold for basket detection |
| `TOF_SIGNAL_RATE_THRESHOLD` | 1000 | Minimum signal rate for basket |

## Example Scenarios

### Scenario 1: Clean Shot (SWISH - Impact + Basket)
```
t=10.0s: Impact detected (accel=6.2g)
         → IMPACT_DETECTED state, impact_time=10.0s

t=10.1s: Ball reaches basket (distance=200mm, SR=1500)
         → is_basket_event() = True
         → time since impact = 0.1s < 0.5s ✓
         → Emit MAKE event (BANK, confidence=0.95)
         → BLACKOUT state, state_start_time=10.1s

t=11.1s: Blackout expires (10.1 + 1.0 = 11.1)
         → IDLE state
```

### Scenario 2: Rimshot (MISS)
```
t=15.0s: Impact detected (accel=5.8g)
         → IMPACT_DETECTED state, impact_time=15.0s

t=15.2s: No basket detected yet, sample arrives
         → time since impact = 0.2s < 0.5s ✓ (still waiting)

t=15.6s: Sample arrives
         → time since impact = 0.6s > 0.5s ✗ (timeout)
         → Emit MISS event (confidence=0.85)
         → BLACKOUT state, state_start_time=15.6s

t=16.6s: Blackout expires
         → IDLE state
```

### Scenario 3: Missed Packet
```
t=20.0s: Impact detected (accel=5.5g)
         → IMPACT_DETECTED state, impact_time=20.0s

[Packet 2 lost - no samples for 200ms]

t=20.3s: First sample from next packet
         → time since impact = 0.3s < 0.5s ✓ (still waiting)
         → Process normally

t=20.6s: Another sample (from recovered data)
         → time since impact = 0.6s > 0.5s ✗
         → Emit MISS event
```
*Note: The state machine correctly handles missed packets because it uses sample timestamps, not wall time.*

### Scenario 4: Standalone Basket (No Impact - SWISH)
```
t=25.0s: Ball near basket (no prior impact)
         → IDLE state
         → Sample is TOF: distance=150mm, SR=1100
         → is_basket_event() = True
         → Emit MAKE event (SWISH, confidence=0.85)
         → BLACKOUT state, state_start_time=25.0s

t=26.0s: Blackout expires
         → IDLE state
```

## Key Design Decisions

1. **Timestamp-ordered processing**: By processing samples in strict timestamp order (merging MPU and TOF queues), the state machine naturally handles:
   - Packet loss/reordering
   - Variable sensor sampling rates
   - Precise temporal correlation

2. **Blackout window**: Prevents false multi-shot detections from:
   - Bouncing rim hits creating multiple impacts
   - Ball rolling in basket creating extended TOF detections

3. **Basket type semantics**:
   - **SWISH**: Basket without prior impact (direct shot)
   - **BANK**: Basket after rim/board contact (bounced shot)
   - This distinguishes shot trajectory types, not signal strength

4. **Confidence scores**:
   - MAKE with impact (BANK): 0.95 (highest confidence - dual sensor confirmation)
   - MAKE without impact (SWISH): 0.85 (moderate confidence - single sensor)
   - MISS: 0.85 (timeout-based, inherently less certain)

## Configuration

All thresholds are defined in `ThresholdConfig` class for easy tuning:

```python
class ThresholdConfig:
    IMPACT_ACCEL_THRESHOLD = 5.0          # Tune for rim/board hardness
    TOF_DISTANCE_THRESHOLD = 350          # Adjust for basket geometry
    TOF_SIGNAL_RATE_THRESHOLD = 1000      # Tune for environment noise
    MAX_TIME_AFTER_IMPACT = 0.5           # Adjust for shooter speed
    BLACKOUT_WINDOW = 1.0                 # Tune for shot cadence
```

## Testing Recommendations

1. **Impact threshold**: Test with rim touches vs. full rim hits
2. **TOF thresholds**: Test with different lighting/surface conditions
3. **Timing window**: Test with fast vs. slow shooters
4. **Basket type**: Verify Swish/Bank classification against ground truth

## Future Enhancements

1. **Adaptive thresholding**: Learn from user feedback to auto-tune parameters
2. **Multi-ball detection**: Track multiple basketballs simultaneously
3. **Confidence refinement**: Machine learning model instead of heuristics
4. **Event logging**: Detailed event history with visualization overlay
5. **Performance metrics**: Shot arc analysis, release point detection
