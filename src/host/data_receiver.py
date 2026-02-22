"""
Network data receiver for UDP packets from ESP32 and CSV logging.
Handles packet parsing, data validation, and logging to CSV file.
"""

import socket
import struct
import csv
import time
from queue import Queue
from threading import Thread
import os

from config import (
    UDP_IP, UDP_PORT, LOG_FILE, SAMPLES_PER_PACKET,
    ACCEL_SENSITIVITY, GYRO_SENSITIVITY,
    CAMERA_ID, CAMERA_FPS, CAMERA_RESOLUTION, CAMERA_JPEG_QUALITY
)
from camera_utils import CameraManager


class DataReceiver:
    """Receives UDP packets from ESP32, logs to CSV, and sends to GUI."""
    
    def __init__(self, gui):
        """
        Initialize the data receiver.
        
        Args:
            gui: SensorGui instance for UI updates
        """
        self.gui = gui
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Allow reusing the socket address and port
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            # SO_REUSEPORT not available on all systems
            pass
        
        # Set socket timeout to avoid blocking forever
        self.sock.settimeout(1.0)
        
        # Retry binding with delays to handle TIME_WAIT state
        max_retries = 5
        for attempt in range(max_retries):
            try:
                self.sock.bind((UDP_IP, UDP_PORT))
                print(f"Listening on {UDP_IP}:{UDP_PORT}")
                break
            except OSError as e:
                if attempt < max_retries - 1:
                    print(f"Port {UDP_PORT} in use, retrying in 2 seconds... (attempt {attempt + 1}/{max_retries})")
                    time.sleep(2)
                else:
                    print(f"Failed to bind to port {UDP_PORT} after {max_retries} attempts")
                    raise

        self.log_file = None
        self.csv_writer = None
        
        # Initialize camera
        self.camera = CameraManager(
            camera_id=CAMERA_ID,
            fps=CAMERA_FPS,
            resolution=CAMERA_RESOLUTION,
            jpeg_quality=CAMERA_JPEG_QUALITY
        )
        self.camera_frame_mapping = {}  # Map sample index to frame_id
        self.last_frame_id = -1   # Track last frame ID to detect drops
        self.last_frame_ts = None  # Timestamp of last known frame
        # Log file is created on demand when recording starts (via _init_log_file)
        
        # Data queue for passing packets from receiver thread to processor thread
        self.packet_queue = Queue(maxsize=300)  # Increased from 100 to handle 3 second bursts
        self.running = True
        self.packets_processed = 0  # Counter for CSV flush logic

    def _init_log_file(self):
        """Initialize or reinitialize the log file. Called each time recording starts."""
        log_path = self.gui.log_file_path
        if self.log_file:
            self.log_file.close()
        # Reset per-session counters
        self.packets_processed = 0
        self.last_frame_id = -1
        self.last_frame_ts = None

        # Drain any packets that arrived before recording started so the CSV
        # only contains packets received after this session begins
        drained = 0
        while not self.packet_queue.empty():
            try:
                self.packet_queue.get_nowait()
                drained += 1
            except:
                break
        if drained:
            print(f"ℹ️  Discarded {drained} pre-recording packets from queue")
        
        self.log_file = open(log_path, "w", newline="")
        self.csv_writer = csv.writer(self.log_file)
        self.csv_writer.writerow([
            "MPU_Timestamp (ms)", "AcX (g)", "AcY (g)", "AcZ (g)", 
            "GyX (dps)", "GyY (dps)", "GyZ (dps)", 
            "TOF_Timestamp (ms)", "Range (mm)", "Signal_Rate",
            "Host_TS_UDP (ms)", "Host_TS_Frame (ms)", "Frame_ID"
        ])
        self.log_file.flush()  # Flush header immediately
        
        # Prepare camera recording directory
        if self.camera.is_available:
            recording_dir = os.path.dirname(log_path) or "."
            # Derive session name from CSV filename: e.g. "202602211411" from "202602211411_sensor_data.csv"
            csv_basename = os.path.basename(log_path)
            session_name = csv_basename.split('_')[0] if '_' in csv_basename else ""
            self.camera.prepare_recording(recording_dir, session_name=session_name)

    def receive_data(self):
        """
        Receives UDP packets and queues them for processing.
        
        This thread runs as fast as possible to receive packets.
        Blocking operations (CSV I/O, GUI updates) are done in a separate thread.
        """
        packets_received = 0
        packets_dropped = 0
        last_print_time = time.time()
        packets_since_last_print = 0
        
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                # Capture host receive timestamp immediately after recvfrom returns
                host_ts_udp = time.time_ns() // 1_000_000  # ns → ms (epoch)
                packets_received += 1
                packets_since_last_print += 1
                
                try:
                    # Try to queue the packet without blocking
                    # If queue is full, drop the packet to prevent blocking
                    self.packet_queue.put_nowait((data, host_ts_udp))
                except:
                    packets_dropped += 1
                    if packets_dropped % 10 == 0:
                        print(f"⚠️  Dropped {packets_dropped} packets (queue full). Receiver may be too slow.")
                
                # Print frequency every second
                current_time = time.time()
                if current_time - last_print_time >= 1.0:
                    frequency = packets_since_last_print / (current_time - last_print_time)
                    print(f"📊 Packet RX frequency: {frequency:.1f} Hz ({packets_since_last_print} packets/sec) | Total: {packets_received} | Dropped: {packets_dropped}")
                    packets_since_last_print = 0
                    last_print_time = current_time
                        
            except socket.timeout:
                # Timeout is expected when no data is available
                continue
            except Exception as e:
                print(f"Error receiving data: {e}")

    def process_data(self):
        """
        Processes queued packets: parses, logs to CSV, and updates GUI.
        
        This runs in a separate thread to avoid blocking the receiver thread.
        """
        while self.running:
            try:
                # Get packet with timeout to check running flag periodically
                data, host_ts_udp = self.packet_queue.get(timeout=0.1)
                # host_ts_udp was captured at receive time in receive_data()
                
                # Parse the packet
                packet_timestamp = struct.unpack('!I', data[0:4])[0]
                num_mpu_samples = struct.unpack('!B', data[4:5])[0]
                
                # Parse MPU6050 data with timestamp deltas
                mpu_sensor_data = []
                for i in range(num_mpu_samples):
                    offset = 5 + i * 14
                    timestamp_delta = struct.unpack('!H', data[offset:offset+2])[0]
                    sample = struct.unpack('!hhhhhh', data[offset+2:offset+14])
                    
                    # Reconstruct sample timestamp
                    sample_timestamp = packet_timestamp - timestamp_delta
                    
                    # Convert to physical units
                    accel = [s / ACCEL_SENSITIVITY for s in sample[0:3]]
                    gyro = [s / GYRO_SENSITIVITY for s in sample[3:6]]
                    
                    mpu_sensor_data.append((accel, gyro, sample_timestamp))
                
                # Parse VL53L1X data with timestamp deltas
                tof_offset = 5 + num_mpu_samples * 14
                num_tof_samples = struct.unpack('!B', data[tof_offset:tof_offset+1])[0]
                tof_data = []
                for i in range(8):  # Always 8 slots in fixed packet
                    offset = tof_offset + 1 + i * 6
                    timestamp_delta = struct.unpack('!H', data[offset:offset+2])[0]
                    distance = struct.unpack('!H', data[offset+2:offset+4])[0]
                    signal_rate = struct.unpack('!H', data[offset+4:offset+6])[0]
                    
                    # Only process if this is a valid sample (within num_tof_samples)
                    if i < num_tof_samples:
                        sample_timestamp = packet_timestamp - timestamp_delta
                        tof_data.append((distance, sample_timestamp, signal_rate))
                
                # Get camera frame metadata (frame saving is async in camera thread)
                frame_id = -1
                host_ts_frame = None
                if self.camera.is_available and self.gui.recording:
                    # Just get the latest frame metadata without blocking on I/O
                    fid, ts = self.camera.get_latest_saved_frame()
                    if fid is not None and fid != self.last_frame_id:
                        # New frame available — update tracking
                        self.last_frame_id = fid
                        self.last_frame_ts = ts
                    # Always use the most recently known frame (avoids -1 on every other packet)
                    if self.last_frame_id >= 0:
                        frame_id = self.last_frame_id
                        host_ts_frame = self.last_frame_ts
                
                # Log all MPU samples with available TOF data and camera metadata
                if self.gui.recording:
                    for i, (accel, gyro, mpu_ts) in enumerate(mpu_sensor_data):
                        if i < len(tof_data):
                            distance, tof_ts, signal_rate = tof_data[i]
                        else:
                            distance = 0xFFFE  # No TOF data available
                            signal_rate = 0
                            tof_ts = mpu_ts    # Use MPU timestamp as reference
                        
                        # Use frame data if available, otherwise -1
                        frame_id_logged = frame_id if frame_id >= 0 else -1
                        host_ts_frame_logged = host_ts_frame if host_ts_frame is not None else -1
                        
                        self.csv_writer.writerow([
                            mpu_ts, accel[0], accel[1], accel[2], 
                            gyro[0], gyro[1], gyro[2], 
                            tof_ts, distance, signal_rate,
                            host_ts_udp, host_ts_frame_logged, frame_id_logged
                        ])
                    
                    # Flush CSV file more frequently to ensure data is written
                    self.packets_processed += 1
                    if self.packets_processed % 5 == 0:  # Flush every 5 packets (~500ms at 10 Hz)
                        self.log_file.flush()
                
                # print(f"Received packet: {num_mpu_samples} MPU samples, {num_tof_samples} TOF samples")
                # print(f">>> TOF Range values (mm): {[d for d, _, _ in tof_data]}")
                
                # Update GUI with all MPU samples paired with TOF data (thread-safe via after())
                if not self.gui.playback_mode:
                    batch = []
                    for i, (accel, gyro, mpu_ts) in enumerate(mpu_sensor_data):
                        if i < len(tof_data):
                            distance, tof_ts, signal_rate = tof_data[i]
                        else:
                            distance = 0xFFFE
                            tof_ts = mpu_ts  # Use MPU timestamp as fallback
                            signal_rate = 0  # Use 0 instead of None
                        batch.append({
                            'accel': accel,
                            'gyro': gyro,
                            'distance': distance,
                            'mpu_ts': mpu_ts,
                            'tof_ts': tof_ts,
                            'signal_rate': signal_rate,
                            'host_ts_udp': host_ts_udp,
                            'frame_id': frame_id if frame_id >= 0 else -1
                        })
                    self.gui.after(0, self.gui.update_plots, batch)

            except Exception as e:
                # Queue timeout is expected, but print other errors
                if "Empty" not in str(type(e).__name__):
                    print(f"❌ Error processing packet: {type(e).__name__}: {e}")
                    import traceback
                    traceback.print_exc()
                continue

    def start(self):
        """Start receiver and processor threads."""
        receiver_thread = Thread(target=self.receive_data, daemon=True)
        receiver_thread.start()
        
        processor_thread = Thread(target=self.process_data, daemon=True)
        processor_thread.start()
        
        # Start camera capture
        self.camera.start_capture()

    def close(self):
        """Stop receiver and close resources."""
        self.running = False
        self.camera.cleanup()
        self.sock.close()
        if self.log_file:
            self.log_file.close()
