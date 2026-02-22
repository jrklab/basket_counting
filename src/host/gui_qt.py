"""
gui_qt.py - PyQt5 + PyQtGraph replacement for gui.py

Key improvements over the Tkinter/matplotlib version:
  - PlotDataItem.setData()  ≈ 1-5 ms  vs  canvas.draw_idle() ≈ 30-100 ms
  - QLabel + QPixmap        ≈ 2-5 ms  vs  PIL → PhotoImage    ≈ 15-30 ms
  - No growing after()-queue lag: DataReceiver.after() posts to a thread-safe
    queue that is drained by a QTimer every 10 ms on the main thread.

DataReceiver is unchanged: it calls gui.after(0, gui.update_plots, batch)
exactly as before; the after() shim below handles the cross-thread dispatch.
"""

import bisect
import csv
import os
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from threading import Thread
import queue as _queue

import cv2
import numpy as np

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)
import pyqtgraph as pg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import LOG_FILE, PLOT_DISPLAY_WINDOW, PLOT_HISTORY_SIZE, SAMPLES_PER_PACKET
from shot_classifier import ShotClassifier

# ── pyqtgraph global config ───────────────────────────────────────────────────
pg.setConfigOptions(antialias=False, background='k', foreground='w')


class SensorGui(QMainWindow):
    """
    PyQt5/PyQtGraph main window — drop-in replacement for the Tkinter SensorGui.

    The public API (attributes & method names) matches the original so that
    DataReceiver and main.py need minimal changes.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESP32 Basketball Shot Counter")
        self.resize(2200, 850)

        # ── data buffers ──────────────────────────────────────────────────
        self.timestamps        = deque(maxlen=PLOT_HISTORY_SIZE)
        self.range_timestamps  = deque(maxlen=PLOT_HISTORY_SIZE)
        self.accel_data = {
            'x':         deque(maxlen=PLOT_HISTORY_SIZE),
            'y':         deque(maxlen=PLOT_HISTORY_SIZE),
            'z':         deque(maxlen=PLOT_HISTORY_SIZE),
            'magnitude': deque(maxlen=PLOT_HISTORY_SIZE),
        }
        self.gyro_data = {
            'x': deque(maxlen=PLOT_HISTORY_SIZE),
            'y': deque(maxlen=PLOT_HISTORY_SIZE),
            'z': deque(maxlen=PLOT_HISTORY_SIZE),
        }
        self.range_data       = deque(maxlen=PLOT_HISTORY_SIZE)
        self.signal_rate_data = deque(maxlen=PLOT_HISTORY_SIZE)

        # ── update throttle ───────────────────────────────────────────────
        self.last_plot_update_time    = 0.0
        self.min_plot_update_interval = 0.1   # 10 Hz max
        self.pending_samples          = []

        # ── recording ────────────────────────────────────────────────────
        self.recording      = False
        self.log_file_path  = LOG_FILE

        # ── playback ─────────────────────────────────────────────────────
        self.playback_mode      = False
        self.playback_data      = []
        self.playback_index     = 0
        self.playback_paused    = False
        self.playback_running   = False
        self.playback_thread    = None
        self.playback_frames_dir   = None
        self.playback_frame_index  = []
        self.playback_frame_ts     = []

        # ── shot classifier ───────────────────────────────────────────────
        self.shot_classifier = ShotClassifier()
        self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}

        # ── camera / receiver (set by main_qt.py after construction) ─────
        self.camera_manager = None
        self.receiver       = None

        # ── shot-event infinite lines (stored for removal on clear) ───────
        self._shot_lines: list[tuple] = []   # list of (PlotWidget, InfiniteLine)

        # ── thread-safe call queue (replaces Tkinter's after() queue) ─────
        self._call_queue: _queue.Queue = _queue.Queue()
        self._drain_timer = QTimer(self)
        self._drain_timer.timeout.connect(self._drain_call_queue)
        self._drain_timer.start(10)   # drain every 10 ms

        # ── build UI ──────────────────────────────────────────────────────
        self._build_ui()

    # =========================================================================
    # Tkinter compatibility shim
    # =========================================================================

    def after(self, delay_ms: int, fn, *args):
        """
        Drop-in replacement for tk.Tk.after().

        Posts (fn, args) onto a thread-safe queue.  The _drain_timer fires
        every 10 ms on the main thread and calls all queued functions.
        delay_ms is accepted for API compatibility but not enforced precisely
        (10 ms granularity is fine for plot updates).
        """
        self._call_queue.put((fn, args))

    def _drain_call_queue(self):
        """Called every 10 ms by QTimer on the main thread."""
        try:
            while True:
                fn, args = self._call_queue.get_nowait()
                fn(*args)
        except _queue.Empty:
            pass

    def mainloop(self):
        """No-op: QApplication.exec_() is called from main_qt.py."""
        pass

    # =========================================================================
    # UI construction
    # =========================================================================

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        root.addWidget(self._make_control_panel())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._make_plot_panel())
        splitter.addWidget(self._make_camera_panel())
        splitter.setSizes([1100, 1100])
        root.addWidget(splitter, stretch=1)

    # ── control panel ────────────────────────────────────────────────────────

    def _make_control_panel(self) -> QWidget:
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)

        # ── Recording & Playback group ────────────────────────────────────
        rec_group = QGroupBox("Recording & Playback")
        rec_layout = QVBoxLayout(rec_group)

        # Recording row
        rec_row = QHBoxLayout()
        rec_row.addWidget(QLabel("Live Recording:"))

        self.record_button = QPushButton("Start")
        self.record_button.setStyleSheet("background-color: lightgreen;")
        self.record_button.clicked.connect(self.start_recording)
        rec_row.addWidget(self.record_button)

        self.stop_record_button = QPushButton("Stop")
        self.stop_record_button.setStyleSheet("background-color: lightcoral;")
        self.stop_record_button.setEnabled(False)
        self.stop_record_button.clicked.connect(self.stop_recording)
        rec_row.addWidget(self.stop_record_button)

        self.log_path_label = QLabel(f"Path: {self.log_file_path}")
        self.log_path_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        rec_row.addWidget(self.log_path_label)

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_log_path)
        rec_row.addWidget(browse_btn)
        rec_row.addStretch()
        rec_layout.addLayout(rec_row)

        # Playback row
        pb_row = QHBoxLayout()
        pb_row.addWidget(QLabel("Playback:"))

        self.load_button = QPushButton("Load File")
        self.load_button.setStyleSheet("background-color: lightblue;")
        self.load_button.clicked.connect(self.load_playback_file)
        pb_row.addWidget(self.load_button)

        self.play_button = QPushButton("Play")
        self.play_button.setStyleSheet("background-color: lightgreen;")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.play_playback)
        pb_row.addWidget(self.play_button)

        self.pause_button = QPushButton("Pause")
        self.pause_button.setStyleSheet("background-color: lightyellow;")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.pause_playback)
        pb_row.addWidget(self.pause_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setStyleSheet("background-color: lightcoral;")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_playback)
        pb_row.addWidget(self.stop_button)

        self.restart_button = QPushButton("Restart")
        self.restart_button.setEnabled(False)
        self.restart_button.clicked.connect(self.restart_playback)
        pb_row.addWidget(self.restart_button)

        self.playback_file_label = QLabel("No file loaded")
        pb_row.addWidget(self.playback_file_label)
        pb_row.addStretch()
        rec_layout.addLayout(pb_row)

        layout.addWidget(rec_group, stretch=3)

        # ── Status ────────────────────────────────────────────────────────
        status_w = QWidget()
        status_layout = QVBoxLayout(status_w)
        status_layout.addWidget(QLabel("Status:"))
        self.status_label = QLabel("Live")
        self.status_label.setStyleSheet("color: red; font-weight: bold; font-size: 14pt;")
        status_layout.addWidget(self.status_label)
        layout.addWidget(status_w)

        # ── Shot statistics ────────────────────────────────────────────────
        stats_w = QWidget()
        stats_layout = QVBoxLayout(stats_w)
        stats_layout.addWidget(QLabel("Shots:"))
        self.stats_label = QLabel("0/0 (0%)")
        self.stats_label.setStyleSheet("color: blue; font-weight: bold; font-size: 28pt;")
        stats_layout.addWidget(self.stats_label)
        clear_btn = QPushButton("Clear Score")
        clear_btn.setStyleSheet("background-color: lightyellow;")
        clear_btn.clicked.connect(self.clear_score)
        stats_layout.addWidget(clear_btn)
        layout.addWidget(stats_w)

        return panel

    # ── plot panel ───────────────────────────────────────────────────────────

    def _make_plot_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(2)
        layout.setContentsMargins(0, 0, 0, 0)

        # Date/time label above plots
        self.datetime_label = QLabel("")
        self.datetime_label.setAlignment(Qt.AlignCenter)
        self.datetime_label.setStyleSheet("color: black; font-size: 11pt; font-weight: bold; padding: 2px;")
        layout.addWidget(self.datetime_label)

        # Acceleration
        self.plot_accel = pg.PlotWidget(title="Accelerometer (g)")
        self.plot_accel.setLabel('left', 'Accel (g)')
        self.plot_accel.showGrid(x=True, y=True, alpha=0.3)
        self.plot_accel.addLegend()
        self.accel_curves = {
            'x':         self.plot_accel.plot(pen=pg.mkPen('b', width=1), symbol='o', symbolSize=4, symbolBrush='b', symbolPen=None, name='AcX'),
            'y':         self.plot_accel.plot(pen=pg.mkPen('g', width=1), symbol='o', symbolSize=4, symbolBrush='g', symbolPen=None, name='AcY'),
            'z':         self.plot_accel.plot(pen=pg.mkPen('r', width=1), symbol='o', symbolSize=4, symbolBrush='r', symbolPen=None, name='AcZ'),
            'magnitude': self.plot_accel.plot(pen=pg.mkPen('w', width=1), symbol='o', symbolSize=4, symbolBrush='w', symbolPen=None, name='Mag'),
        }
        layout.addWidget(self.plot_accel)

        # Gyroscope
        self.plot_gyro = pg.PlotWidget(title="Gyroscope (dps)")
        self.plot_gyro.setLabel('left', 'Gyro (dps)')
        self.plot_gyro.showGrid(x=True, y=True, alpha=0.3)
        self.plot_gyro.addLegend()
        self.gyro_curves = {
            'x': self.plot_gyro.plot(pen=pg.mkPen('b', width=1), symbol='o', symbolSize=4, symbolBrush='b', symbolPen=None, name='GyX'),
            'y': self.plot_gyro.plot(pen=pg.mkPen('g', width=1), symbol='o', symbolSize=4, symbolBrush='g', symbolPen=None, name='GyY'),
            'z': self.plot_gyro.plot(pen=pg.mkPen('r', width=1), symbol='o', symbolSize=4, symbolBrush='r', symbolPen=None, name='GyZ'),
        }
        layout.addWidget(self.plot_gyro)

        # Range
        self.plot_range = pg.PlotWidget(title="Distance / VL53L1X (mm,  -1 = no target)")
        self.plot_range.setLabel('left', 'Distance (mm)')
        self.plot_range.showGrid(x=True, y=True, alpha=0.3)
        self.plot_range.addLegend()
        self.range_curve = self.plot_range.plot(pen=pg.mkPen('c', width=1), symbol='o', symbolSize=5, symbolBrush='c', symbolPen=None, name='Range')
        layout.addWidget(self.plot_range)

        # Signal rate
        self.plot_sr = pg.PlotWidget(title="Signal Rate / VL53L1X")
        self.plot_sr.setLabel('left', 'Signal Rate')
        self.plot_sr.setLabel('bottom', 'Time (s)')
        self.plot_sr.showGrid(x=True, y=True, alpha=0.3)
        self.plot_sr.addLegend()
        self.sr_curve = self.plot_sr.plot(pen=pg.mkPen('y', width=1), symbol='o', symbolSize=5, symbolBrush='y', symbolPen=None, name='SR')
        layout.addWidget(self.plot_sr)

        self._all_plots = [self.plot_accel, self.plot_gyro, self.plot_range, self.plot_sr]

        # Lock x-axis to a fixed rolling window; disable auto-range so that
        # InfiniteLines (shot markers) cannot push the view outward.
        for p in self._all_plots:
            p.setMouseEnabled(x=False, y=True)   # prevent accidental x-pan
            p.enableAutoRange(axis='x', enable=False)

        return widget

    # ── camera panel ─────────────────────────────────────────────────────────

    def _make_camera_panel(self) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet("background-color: black;")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(2, 2, 2, 2)

        hdr = QLabel("Camera Feed")
        hdr.setStyleSheet("color: white; font-weight: bold;")
        layout.addWidget(hdr)

        self.camera_label = QLabel()
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setStyleSheet("background-color: black;")
        self.camera_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.camera_label)
        return widget

    # =========================================================================
    # Recording
    # =========================================================================

    def clear_score(self):
        self.shot_classifier.reset()
        self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
        self.stats_label.setText("0/0 (0%)")
        self._remove_shot_lines()
        print("Score cleared.")

    def browse_log_path(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Select log file", "", "CSV files (*.csv);;All files (*.*)")
        if path:
            self.log_file_path = path
            self.log_path_label.setText(f"Path: {os.path.basename(path)}")

    def start_recording(self):
        ts = datetime.now().strftime("%Y%m%d%H%M")
        base_dir = os.path.dirname(self.log_file_path) or "."
        self.log_file_path = os.path.join(base_dir, f"{ts}_sensor_data.csv")
        self.log_path_label.setText(f"Path: {os.path.basename(self.log_file_path)}")

        if hasattr(self, 'receiver') and self.receiver:
            self.receiver.last_frame_id    = -1
            self.receiver.packets_processed = 0
            self.receiver._init_log_file()

        self.recording = True
        self.shot_classifier.reset()
        self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
        self.stats_label.setText("0/0 (0%)")
        self.record_button.setEnabled(False)
        self.stop_record_button.setEnabled(True)
        print(f"Recording started to: {self.log_file_path}")

    def stop_recording(self):
        self.recording = False
        if hasattr(self, 'receiver') and self.receiver and self.receiver.log_file:
            self.receiver.log_file.flush()
        self.record_button.setEnabled(True)
        self.stop_record_button.setEnabled(False)
        print("Recording stopped.")

    # =========================================================================
    # Playback
    # =========================================================================

    def load_playback_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load CSV", "", "CSV files (*.csv);;All files (*.*)")
        if not path:
            return
        try:
            self.playback_data         = []
            self.playback_frames_dir   = None
            self.playback_frame_index  = []
            self.playback_frame_ts     = []

            base_dir     = os.path.dirname(path)
            csv_basename = os.path.basename(path)
            session_name = csv_basename.split('_')[0] if '_' in csv_basename else ""

            candidates = []
            if session_name:
                candidates.append(os.path.join(base_dir, f"{session_name}_frames"))
            candidates.append(os.path.join(base_dir, "frames"))
            for c in candidates:
                if os.path.exists(c):
                    self.playback_frames_dir = c
                    break

            if self.playback_frames_dir:
                entries = []
                for fname in os.listdir(self.playback_frames_dir):
                    if fname.startswith('frame_') and fname.endswith('ms.jpg'):
                        try:
                            ts_str = fname.rsplit('_', 1)[1].replace('ms.jpg', '')
                            entries.append((int(ts_str), fname))
                        except (ValueError, IndexError):
                            continue
                entries.sort()
                self.playback_frame_index = entries
                self.playback_frame_ts    = [e[0] for e in entries]
                print(f"Found {len(entries)} frames in {self.playback_frames_dir}")

            with open(path, 'r') as f:
                reader = csv.reader(f)
                next(reader)
                for row in reader:
                    if len(row) >= 9:
                        try:
                            mpu_ts      = int(row[0])
                            acx, acy, acz = float(row[1]), float(row[2]), float(row[3])
                            gx, gy, gz    = float(row[4]), float(row[5]), float(row[6])
                            tof_ts      = int(row[7])
                            distance    = int(row[8])
                            signal_rate = int(row[9])  if len(row) > 9  else 0
                            host_ts_udp = int(row[10]) if len(row) > 10 else -1
                            frame_id    = int(row[12]) if len(row) > 12 else -1
                            if distance in (0xFFFF, 65535):
                                distance = -1
                            self.playback_data.append({
                                'mpu_ts': mpu_ts, 'accel': [acx, acy, acz],
                                'gyro': [gx, gy, gz], 'tof_ts': tof_ts,
                                'distance': distance, 'signal_rate': signal_rate,
                                'frame_id': frame_id, 'host_ts_udp': host_ts_udp,
                            })
                        except (ValueError, IndexError):
                            continue

            if self.playback_data:
                self.playback_index = 0
                self.playback_paused = False
                self.playback_file_label.setText(f"Loaded: {os.path.basename(path)}")
                self.play_button.setEnabled(True)
                self.restart_button.setEnabled(True)
                print(f"Loaded {len(self.playback_data)} samples from {path}")
            else:
                QMessageBox.critical(self, "Error", "No valid data found in file.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load file: {e}")

    def play_playback(self):
        if not self.playback_data:
            QMessageBox.warning(self, "Warning", "No data loaded for playback.")
            return

        if not self.playback_paused:
            self._clear_plot_data()
            self._remove_shot_lines()
            self.shot_classifier.reset()
            self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
            self.stats_label.setText("0/0 (0%)")

        self.playback_mode   = True
        self.playback_paused = False

        if not self.playback_running:
            self.playback_running = True
            self.playback_thread  = Thread(target=self._playback_worker, daemon=True)
            self.playback_thread.start()

        self.play_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.load_button.setEnabled(False)
        self.status_label.setText("Playback")
        self.status_label.setStyleSheet("color: blue; font-weight: bold; font-size: 14pt;")
        print("Playback started/resumed.")

    def _playback_worker(self):
        if self.playback_index >= len(self.playback_data):
            self.playback_index = 0
        while self.playback_running and self.playback_index < len(self.playback_data):
            if self.playback_paused:
                time.sleep(0.05)
                continue
            batch_end = min(self.playback_index + SAMPLES_PER_PACKET, len(self.playback_data))
            batch = self.playback_data[self.playback_index:batch_end]
            self.after(0, self.update_plots, batch)
            self.playback_index = batch_end
            time.sleep(0.1)
        if self.playback_running:
            self.after(0, self._playback_finished)

    def _playback_finished(self):
        self.playback_mode    = False
        self.playback_running = False
        self.play_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.load_button.setEnabled(True)
        self.status_label.setText("Live")
        self.status_label.setStyleSheet("color: red; font-weight: bold; font-size: 14pt;")
        print("Playback finished.")

    def pause_playback(self):
        self.playback_paused = True
        self.play_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        print("Playback paused.")

    def stop_playback(self):
        self.playback_running = False
        self.playback_paused  = False
        self.playback_mode    = False
        time.sleep(0.1)
        self._clear_plot_data()
        self._remove_shot_lines()
        self.play_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.load_button.setEnabled(True)
        self.status_label.setText("Live")
        self.status_label.setStyleSheet("color: red; font-weight: bold; font-size: 14pt;")
        print("Playback stopped.")

    def restart_playback(self):
        self.playback_running = False
        self.playback_paused  = False
        time.sleep(0.1)
        self._clear_plot_data()
        self._remove_shot_lines()
        self.shot_classifier.reset()
        self.shot_stats = {'makes': 0, 'misses': 0, 'total': 0, 'percentage': 0.0}
        self.stats_label.setText("0/0 (0%)")
        self.playback_index = 0
        self.play_playback()

    def _clear_plot_data(self):
        self.timestamps.clear()
        self.range_timestamps.clear()
        for k in self.accel_data:
            self.accel_data[k].clear()
        for k in self.gyro_data:
            self.gyro_data[k].clear()
        self.range_data.clear()
        self.signal_rate_data.clear()
        # Blank the curves immediately
        for c in self.accel_curves.values():
            c.setData([], [])
        for c in self.gyro_curves.values():
            c.setData([], [])
        self.range_curve.setData([], [])
        self.sr_curve.setData([], [])

    # =========================================================================
    # Shot-event vertical lines
    # =========================================================================

    def _remove_shot_lines(self):
        for plot, line in self._shot_lines:
            plot.removeItem(line)
        self._shot_lines.clear()

    def _prune_shot_lines(self, x_min: float):
        """Remove shot lines that have scrolled past the left edge of the window."""
        keep = []
        for plot, line in self._shot_lines:
            if line.value() >= x_min:
                keep.append((plot, line))
            else:
                plot.removeItem(line)
        self._shot_lines = keep

    def _add_shot_line(self, t: float, color: str):
        pen = pg.mkPen(color, width=2, style=Qt.DashLine)
        for plot in self._all_plots:
            line = pg.InfiniteLine(pos=t, angle=90, pen=pen)
            plot.addItem(line)
            self._shot_lines.append((plot, line))

    # =========================================================================
    # Main update — called from DataReceiver thread via after()
    # =========================================================================

    def update_plots(self, samples):
        """
        Buffers incoming samples and redraws at most at min_plot_update_interval.
        Identical contract to the original Tkinter version.
        """
        self.pending_samples.extend(samples)

        now = time.time()
        if now - self.last_plot_update_time < self.min_plot_update_interval:
            return
        self.last_plot_update_time = now

        s2p = self.pending_samples
        self.pending_samples = []

        # ── shot classification ────────────────────────────────────────────
        new_shots = self.shot_classifier.process_batch(s2p)
        if new_shots:
            stats = self.shot_classifier.get_statistics()
            self.shot_stats = stats
            makes, total, pct = stats['makes'], stats['total'], stats['percentage']
            self.stats_label.setText(f"{makes}/{total} ({pct:.0f}%)")
            for shot in new_shots:
                t = shot['impact_time'] if shot['impact_time'] is not None else shot['basket_time']
                score_text = f"{makes} out of {total}"
                if shot['classification'] == 'MAKE':
                    basket_type = shot.get('basket_type', '')
                    print(f"🏀 MAKE ({basket_type}) @ {t:.3f}s")
                    self._add_shot_line(shot['basket_time'], 'r')
                    msg = f"Swish. {score_text}" if basket_type == 'SWISH' else f"Made. {score_text}"
                else:
                    print(f"❌ MISS @ {t:.3f}s")
                    self._add_shot_line(shot['impact_time'], 'b')
                    msg = f"Miss. {score_text}"
                self._speak(msg)

        # ── live camera ───────────────────────────────────────────────────
        if not self.playback_mode and self.camera_manager and self.camera_manager.is_available:
            frame, _, _ = self.camera_manager.get_current_frame()
            if frame is not None:
                self._display_camera_frame(frame)

        # ── playback camera ────────────────────────────────────────────────
        if self.playback_mode and self.playback_frames_dir and self.playback_frame_index and s2p:
            for sample in s2p:
                host_ts = sample.get('host_ts_udp', -1)
                if host_ts > 0:
                    idx = bisect.bisect_right(self.playback_frame_ts, host_ts) - 1
                    if idx >= 0:
                        _, fname = self.playback_frame_index[idx]
                        fpath = os.path.join(self.playback_frames_dir, fname)
                        if os.path.exists(fpath):
                            fr = cv2.imread(fpath)
                            if fr is not None:
                                self._display_camera_frame(fr)
                    break

        # ── buffer samples ────────────────────────────────────────────────
        for s in s2p:
            ts_sec = s['mpu_ts'] / 1000.0
            accel  = s['accel']
            gyro   = s['gyro']
            dist   = s['distance']
            tof_ts = s['tof_ts']
            sr     = s['signal_rate']

            self.timestamps.append(ts_sec)
            self.accel_data['x'].append(accel[0])
            self.accel_data['y'].append(accel[1])
            self.accel_data['z'].append(accel[2])
            self.accel_data['magnitude'].append(
                (accel[0]**2 + accel[1]**2 + accel[2]**2) ** 0.5)
            self.gyro_data['x'].append(gyro[0])
            self.gyro_data['y'].append(gyro[1])
            self.gyro_data['z'].append(gyro[2])

            if dist not in (0xFFFE, 65534):
                self.range_timestamps.append(tof_ts / 1000.0 if tof_ts else ts_sec)
                self.range_data.append(-1 if dist in (0xFFFF, 65535) else dist)
                self.signal_rate_data.append(sr or 0)

        # ── trim to 2.5 s rolling window ───────────────────────────────────
        DISPLAY_WINDOW = 2.5
        if self.timestamps:
            cutoff = self.timestamps[-1] - DISPLAY_WINDOW
            while self.timestamps and self.timestamps[0] < cutoff:
                self.timestamps.popleft()
                self.accel_data['x'].popleft()
                self.accel_data['y'].popleft()
                self.accel_data['z'].popleft()
                self.accel_data['magnitude'].popleft()
                self.gyro_data['x'].popleft()
                self.gyro_data['y'].popleft()
                self.gyro_data['z'].popleft()

        if self.range_timestamps:
            cutoff_r = self.range_timestamps[-1] - DISPLAY_WINDOW
            while self.range_timestamps and self.range_timestamps[0] < cutoff_r:
                self.range_timestamps.popleft()
                self.range_data.popleft()
                self.signal_rate_data.popleft()

        # ── push to pyqtgraph (the fast part) ─────────────────────────────
        ts_arr = np.array(self.timestamps)
        self.accel_curves['x'].setData(ts_arr, np.array(self.accel_data['x']))
        self.accel_curves['y'].setData(ts_arr, np.array(self.accel_data['y']))
        self.accel_curves['z'].setData(ts_arr, np.array(self.accel_data['z']))
        self.accel_curves['magnitude'].setData(ts_arr, np.array(self.accel_data['magnitude']))

        self.gyro_curves['x'].setData(ts_arr, np.array(self.gyro_data['x']))
        self.gyro_curves['y'].setData(ts_arr, np.array(self.gyro_data['y']))
        self.gyro_curves['z'].setData(ts_arr, np.array(self.gyro_data['z']))

        rts = np.array(self.range_timestamps)
        self.range_curve.setData(rts, np.array(self.range_data))
        self.sr_curve.setData(rts, np.array(self.signal_rate_data))

        # ── update date/time label ─────────────────────────────────────────
        if self.playback_mode and s2p:
            for s in reversed(s2p):
                host_ts = s.get('host_ts_udp', -1)
                if host_ts and host_ts > 0:
                    dt_str = datetime.fromtimestamp(host_ts / 1000).strftime('%Y-%m-%d  %H:%M:%S')
                    self.datetime_label.setText(dt_str)
                    break
        else:
            self.datetime_label.setText(datetime.now().strftime('%Y-%m-%d  %H:%M:%S'))

        # ── enforce fixed 2.5 s x-window (done AFTER setData) ─────────────
        # setXRange overrides any auto-scale triggered by InfiniteLines or
        # setData, keeping the view locked to the rolling window.
        t_max = self.timestamps[-1] if self.timestamps else 0.0
        x_min = t_max - DISPLAY_WINDOW
        self._prune_shot_lines(x_min)
        for p in self._all_plots:
            p.setXRange(x_min, t_max, padding=0)

    # =========================================================================
    # Camera display
    # =========================================================================

    def _display_camera_frame(self, frame):
        if frame is None:
            return
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            # QImage shares the numpy buffer; keep rgb alive until after setPixmap
            qt_img  = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            pixmap  = QPixmap.fromImage(qt_img)
            lw, lh  = self.camera_label.width(), self.camera_label.height()
            if lw > 1 and lh > 1:
                pixmap = pixmap.scaled(lw, lh, Qt.KeepAspectRatio, Qt.FastTransformation)
            self.camera_label.setPixmap(pixmap)
        except Exception as e:
            print(f"Camera display error: {e}")

    # =========================================================================
    # Audio
    # =========================================================================

    def _speak(self, text: str):
        try:
            subprocess.Popen(['spd-say', text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # =========================================================================
    # Cleanup
    # =========================================================================

    def closeEvent(self, event):
        self._drain_timer.stop()
        self.playback_running = False
        event.accept()
