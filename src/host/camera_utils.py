"""
Camera utilities for USB camera capture, frame storage, and timing synchronization.
Provides graceful fallback if camera is unavailable.
"""

import cv2
import os
import time
from threading import Thread, Lock
from queue import Queue
import numpy as np


class CameraManager:
    """Manages USB camera capture with timestamp logging and frame storage."""
    
    def __init__(self, camera_id=0, fps=30, resolution=(640, 480), jpeg_quality=70):
        """
        Initialize camera manager.
        
        Args:
            camera_id: USB camera index (0 for default)
            fps: Target frames per second
            resolution: (width, height) tuple
            jpeg_quality: JPEG compression quality (0-100)
        """
        self.camera_id = camera_id
        self.target_fps = fps
        self.frame_time_ms = 1000.0 / fps  # ~100ms for 10fps
        self.resolution = resolution
        self.jpeg_quality = jpeg_quality
        
        self.cap = None
        self.is_available = False
        self.is_running = False
        self.frame_count = 0
        
        # Thread-safe frame queue and metadata
        self.frame_queue = Queue(maxsize=200)  # Large buffer for 20+ seconds of frames at 10fps
        self.frame_lock = Lock()
        self.current_frame = None
        self.current_frame_id = -1
        self.current_frame_ts = None
        
        # Recording metadata
        self.frame_ids_logged = []  # List of (frame_id, host_ts_frame) tuples
        
        # Frame storage path (will be set by recorder)
        self.frames_dir = None
        
        # Try to initialize camera
        self._init_camera()
    
    def _init_camera(self):
        """Initialize camera capture. Gracefully fail if unavailable."""
        try:
            self.cap = cv2.VideoCapture(self.camera_id)
            
            if not self.cap.isOpened():
                print(f"⚠️  Camera {self.camera_id} not available")
                self.is_available = False
                return
            
            # Set camera properties
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
            self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
            
            # Test capture one frame
            ret, frame = self.cap.read()
            if not ret or frame is None:
                print(f"⚠️  Camera {self.camera_id} cannot read frames")
                self.cap.release()
                self.is_available = False
                return
            
            self.is_available = True
            print(f"✓ Camera {self.camera_id} initialized ({self.resolution[0]}x{self.resolution[1]}, {self.target_fps}fps)")
            
        except Exception as e:
            print(f"⚠️  Camera initialization failed: {e}")
            self.is_available = False
    
    def start_capture(self):
        """Start camera capture thread."""
        if not self.is_available:
            print("Camera not available, skipping capture")
            return
        
        self.is_running = True
        self.frame_count = 0
        capture_thread = Thread(target=self._capture_worker, daemon=True)
        capture_thread.start()
        
        # Start frame saver thread (handles async disk I/O)
        saver_thread = Thread(target=self._frame_saver_worker, daemon=True)
        saver_thread.start()
    
    def _capture_worker(self):
        """Worker thread that captures frames at target FPS."""
        last_frame_time = time.time()
        total_dropped = 0
        
        while self.is_running and self.cap.isOpened():
            try:
                ret, frame = self.cap.read()
                
                if not ret or frame is None:
                    print("⚠️  Camera stopped or frame capture failed")
                    break
                
                now = time.time()
                
                # Detect real camera drops: if elapsed time since last frame is
                # more than 1.8x the target interval, we missed at least one frame
                elapsed_since_last = now - last_frame_time
                missed = int(elapsed_since_last / (self.frame_time_ms / 1000.0)) - 1
                if missed > 0:
                    total_dropped += missed
                    print(f"❌ Camera missed {missed} frame(s) (gap {elapsed_since_last*1000:.0f}ms, total dropped: {total_dropped})")
                
                # Capture host timestamp with nanosecond precision
                host_ts_frame = time.time_ns() // 1_000_000  # ns → ms
                
                # Store frame (thread-safe)
                with self.frame_lock:
                    self.current_frame = frame.copy()
                    self.current_frame_id = self.frame_count
                    self.current_frame_ts = host_ts_frame
                
                # Queue frame for saving (with brief timeout to avoid blocking capture)
                try:
                    # Try with 10ms timeout - if saver is too slow, we still capture next frame
                    self.frame_queue.put((self.frame_count, frame.copy(), host_ts_frame), timeout=0.01)
                except:
                    # Queue is backed up, skip this frame to keep capture smooth
                    pass
                
                self.frame_count += 1
                
                # Maintain target FPS with frame time regulation
                elapsed = now - last_frame_time
                sleep_time = self.frame_time_ms / 1000.0 - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
                last_frame_time = time.time()
                
            except Exception as e:
                print(f"❌ Camera capture error: {e}")
                break
        
        print(f"Camera capture stopped ({self.frame_count} frames captured)")
    
    def _frame_saver_worker(self):
        """Separate thread that saves queued frames to disk without blocking capture."""
        while self.is_running:
            try:
                # Get frame from queue with timeout to check running flag
                try:
                    frame_id, frame, host_ts = self.frame_queue.get(timeout=0.5)
                except:
                    continue  # Queue timeout, check running flag
                
                # Save to disk if directory is set
                # Filename embeds the capture timestamp (ms epoch) for direct CSV correlation:
                #   frame_000029_1771573017786ms.jpg  →  Host_TS_Frame column in CSV
                if self.frames_dir is not None and frame is not None:
                    try:
                        frame_path = os.path.join(self.frames_dir, f"frame_{frame_id:06d}_{host_ts}ms.jpg")
                        success = cv2.imwrite(frame_path, frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
                        if success:
                            self.frame_ids_logged.append((frame_id, host_ts))
                    except Exception as save_error:
                        pass  # Silently skip this frame if save fails
            except Exception as e:
                pass  # Silently skip errors in saver thread
    
    def get_current_frame(self):
        """
        Get the current frame being displayed.
        
        Returns:
            (frame, frame_id, host_ts_frame) or (None, -1, None) if unavailable
        """
        if not self.is_available:
            return None, -1, None
        
        with self.frame_lock:
            return self.current_frame, self.current_frame_id, self.current_frame_ts
    
    def prepare_recording(self, base_dir="recordings", session_name=""):
        """Prepare directory structure for recording frames."""
        if not self.is_available:
            return None
        
        # Reset per-session frame log
        self.frame_ids_logged = []
        
        # Create frames directory named after the session (matches CSV timestamp prefix)
        dir_name = f"{session_name}_frames" if session_name else "frames"
        self.frames_dir = os.path.join(base_dir, dir_name)
        os.makedirs(self.frames_dir, exist_ok=True)
        
        # Clear any existing frames
        for f in os.listdir(self.frames_dir):
            if f.endswith('.jpg'):
                os.remove(os.path.join(self.frames_dir, f))
        
        print(f"✓ Camera recording prepared: {self.frames_dir}")
        return self.frames_dir
    
    def save_frame_if_new(self):
        """
        Save current frame to disk if it's new.
        Call this periodically during recording.
        
        Returns:
            (frame_id, host_ts_frame) if saved, (None, None) if no new frame
        """
        if not self.is_available or self.frames_dir is None:
            return None, None
        
        with self.frame_lock:
            if self.current_frame is None:
                return None, None
            
            frame_id = self.current_frame_id
            host_ts = self.current_frame_ts
            
            # Skip if already saved
            if (frame_id, host_ts) in self.frame_ids_logged:
                return None, None
            
            # Save frame — filename includes capture timestamp for CSV correlation
            frame_path = os.path.join(self.frames_dir, f"frame_{frame_id:06d}_{host_ts}ms.jpg")
            cv2.imwrite(frame_path, self.current_frame,
                       [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])

            self.frame_ids_logged.append((frame_id, host_ts))
            return frame_id, host_ts
    
    def load_frame(self, frame_id):
        """
        Load a frame from disk by frame_id.
        Supports both legacy filenames (frame_000029.jpg) and
        timestamped filenames (frame_000029_<ts>ms.jpg).

        Returns:
            numpy array or None if not found
        """
        if self.frames_dir is None:
            return None

        import glob
        # Try timestamped filename first
        pattern = os.path.join(self.frames_dir, f"frame_{frame_id:06d}_*.jpg")
        matches = glob.glob(pattern)
        if matches:
            return cv2.imread(matches[0])
        # Fallback: legacy filename without timestamp
        legacy_path = os.path.join(self.frames_dir, f"frame_{frame_id:06d}.jpg")
        if os.path.exists(legacy_path):
            return cv2.imread(legacy_path)
        return None
    
    def get_frame_timestamp_mapping(self):
        """Get list of (frame_id, host_ts_frame) tuples recorded."""
        return self.frame_ids_logged.copy()
    
    def get_latest_saved_frame(self):
        """Get the most recently saved frame ID and timestamp (non-blocking)."""
        if self.frame_ids_logged:
            frame_id, host_ts = self.frame_ids_logged[-1]
            return frame_id, host_ts
        return None, None
    
    def stop_capture(self):
        """Stop camera capture thread."""
        self.is_running = False
        if self.cap:
            self.cap.release()
        print("Camera capture stopped")
    
    def cleanup(self):
        """Clean up camera resources."""
        self.stop_capture()
