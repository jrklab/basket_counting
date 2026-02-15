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
    
    def __init__(self, camera_id=0, fps=30, resolution=(640, 480), jpeg_quality=95):
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
        self.frame_time_ms = 1000.0 / fps  # ~33.3ms for 30fps
        self.resolution = resolution
        self.jpeg_quality = jpeg_quality
        
        self.cap = None
        self.is_available = False
        self.is_running = False
        self.frame_count = 0
        
        # Thread-safe frame queue and metadata
        self.frame_queue = Queue(maxsize=10)
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
    
    def _capture_worker(self):
        """Worker thread that captures frames at target FPS."""
        last_frame_time = time.time()
        
        while self.is_running and self.cap.isOpened():
            try:
                ret, frame = self.cap.read()
                
                if not ret or frame is None:
                    print("⚠️  Camera stopped or frame capture failed")
                    break
                
                # Capture host timestamp with nanosecond precision
                host_ts_frame = time.time_ns() // 1_000_000  # ns → ms
                
                # Store frame (thread-safe)
                with self.frame_lock:
                    self.current_frame = frame.copy()
                    self.current_frame_id = self.frame_count
                    self.current_frame_ts = host_ts_frame
                
                self.frame_count += 1
                
                # Maintain target FPS with frame time regulation
                elapsed = time.time() - last_frame_time
                sleep_time = self.frame_time_ms / 1000.0 - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
                last_frame_time = time.time()
                
            except Exception as e:
                print(f"❌ Camera capture error: {e}")
                break
        
        print(f"Camera capture stopped ({self.frame_count} frames captured)")
    
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
    
    def prepare_recording(self, base_dir="recordings"):
        """Prepare directory structure for recording frames."""
        if not self.is_available:
            return None
        
        # Create frames directory
        self.frames_dir = os.path.join(base_dir, "frames")
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
            
            # Save frame
            frame_path = os.path.join(self.frames_dir, f"frame_{frame_id:06d}.jpg")
            cv2.imwrite(frame_path, self.current_frame, 
                       [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            
            self.frame_ids_logged.append((frame_id, host_ts))
            return frame_id, host_ts
    
    def load_frame(self, frame_id):
        """
        Load a frame from disk by frame_id.
        
        Returns:
            numpy array or None if not found
        """
        if self.frames_dir is None:
            return None
        
        frame_path = os.path.join(self.frames_dir, f"frame_{frame_id:06d}.jpg")
        if os.path.exists(frame_path):
            return cv2.imread(frame_path)
        return None
    
    def get_frame_timestamp_mapping(self):
        """Get list of (frame_id, host_ts_frame) tuples recorded."""
        return self.frame_ids_logged.copy()
    
    def stop_capture(self):
        """Stop camera capture thread."""
        self.is_running = False
        if self.cap:
            self.cap.release()
        print("Camera capture stopped")
    
    def cleanup(self):
        """Clean up camera resources."""
        self.stop_capture()
