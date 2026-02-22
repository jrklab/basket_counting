"""
show_score.py - Lightweight basketball score display (PyQt5 edition).

Receives UDP packets from the ESP32, classifies baskets in the receiver
thread, then dispatches results to the main thread via a Qt signal.

No matplotlib, no camera, no CSV logging — keeps the main thread nearly
idle so audio fires within milliseconds of a detected basket.
"""

import socket
import struct
import subprocess
import sys
import os
import time
from threading import Thread

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QLabel, QMainWindow, QPushButton,
    QVBoxLayout, QHBoxLayout, QWidget,
)

# Allow running from any directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import UDP_IP, UDP_PORT, SAMPLES_PER_PACKET, ACCEL_SENSITIVITY, GYRO_SENSITIVITY
from shot_classifier import ShotClassifier


# ── visual constants ─────────────────────────────────────────────────────────
BG_CSS   = "background-color: #0d1117;"
FG_SCORE = "#f0e040"   # yellow  – makes
FG_TOTAL = "#888888"   # grey    – denominator / percentage
FG_MAKE  = "#2ecc71"   # green   – MAKE flash
FG_MISS  = "#e74c3c"   # red     – MISS flash
FG_IDLE  = "#444455"   # dimmed  – no event yet
FLASH_MS = 1500        # ms the last-event banner stays coloured


def _lbl_style(color, size, bold=False, italic=False) -> str:
    w = "bold" if bold else "normal"
    s = "italic" if italic else "normal"
    return (f"color:{color}; font-size:{size}pt; font-weight:{w};"
            f" font-style:{s}; background:transparent;")


class ScoreApp(QMainWindow):
    """Minimal score-only PyQt5 window for real-time basket detection."""

    # Signal emitted from receiver thread — Qt queues it onto the main thread.
    _shots_signal = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Basketball Score")
        self.setMinimumSize(400, 340)
        self.resize(700, 500)
        self.setStyleSheet(BG_CSS)

        # ── state ──────────────────────────────────────────────────────────
        self.classifier = ShotClassifier()
        self.makes      = 0
        self.total      = 0
        self.running    = True
        self._pkt_count = 0    # incremented by receiver thread
        self._last_seq  = -1   # sequence-ID tracking

        # ── UI ─────────────────────────────────────────────────────────────
        self._build_ui()

        # ── connect signal (cross-thread safe) ────────────────────────────
        self._shots_signal.connect(self._on_new_shots)

        # ── background receiver thread ────────────────────────────────────
        Thread(target=self._receive_loop, daemon=True).start()

        # ── 1-second packet-rate ticker ───────────────────────────────────
        self._rate_timer = QTimer(self)
        self._rate_timer.timeout.connect(self._tick_rate)
        self._rate_timer.start(1000)

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        root.setStyleSheet(BG_CSS)
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(20, 20, 20, 10)
        outer.setSpacing(0)
        outer.addStretch(1)

        # score row  "8 / 12"
        score_row = QHBoxLayout()
        score_row.setAlignment(Qt.AlignCenter)
        self.makes_label = QLabel("0")
        self.makes_label.setStyleSheet(_lbl_style(FG_SCORE, 110, bold=True))
        self.makes_label.setAlignment(Qt.AlignCenter)
        sep = QLabel(" / ")
        sep.setStyleSheet(_lbl_style(FG_TOTAL, 60))
        sep.setAlignment(Qt.AlignCenter)
        self.total_label = QLabel("0")
        self.total_label.setStyleSheet(_lbl_style(FG_TOTAL, 60))
        self.total_label.setAlignment(Qt.AlignCenter)
        score_row.addWidget(self.makes_label)
        score_row.addWidget(sep)
        score_row.addWidget(self.total_label)
        outer.addLayout(score_row)

        # percentage
        self.pct_label = QLabel("— %")
        self.pct_label.setStyleSheet(_lbl_style(FG_TOTAL, 32))
        self.pct_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.pct_label)

        # last-event banner
        self.event_label = QLabel("waiting...")
        self.event_label.setStyleSheet(_lbl_style(FG_IDLE, 22, italic=True))
        self.event_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.event_label)

        outer.addSpacing(16)

        # clear button
        btn = QPushButton("Clear Score")
        btn.setStyleSheet(
            "background-color:#c0392b; color:white; font-size:14pt;"
            " font-weight:bold; padding:10px 24px; border:none; border-radius:6px;")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self._clear_score)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        outer.addStretch(1)

        # packet-rate label (bottom-left)
        self.rate_label = QLabel("UDP: — pkt/s")
        self.rate_label.setStyleSheet("color:#555566; font-size:10pt; background:transparent;")
        self.rate_label.setAlignment(Qt.AlignLeft)
        outer.addWidget(self.rate_label)

    # ── packet-rate ticker ───────────────────────────────────────────────────

    def _tick_rate(self):
        rate            = self._pkt_count
        self._pkt_count = 0
        if rate == 0:
            colour, text = "#e74c3c", "UDP: 0 pkt/s  (!) no signal"
        elif rate < 7:
            colour, text = "#e67e22", f"UDP: {rate} pkt/s  (low)"
        else:
            colour, text = "#555566", f"UDP: {rate} pkt/s"
        self.rate_label.setStyleSheet(
            f"color:{colour}; font-size:10pt; background:transparent;")
        self.rate_label.setText(text)

    # ── score helpers ─────────────────────────────────────────────────────────

    def _refresh_display(self):
        pct = (self.makes / self.total * 100) if self.total else 0
        self.makes_label.setText(str(self.makes))
        self.total_label.setText(str(self.total))
        self.pct_label.setText(f"{pct:.0f} %")

    def _clear_score(self):
        self.classifier.reset()
        self.makes = 0
        self.total = 0
        self._refresh_display()
        self.event_label.setStyleSheet(_lbl_style(FG_IDLE, 22, italic=True))
        self.event_label.setText("cleared")
        print("Score cleared.")

    # ── shot callback (main thread via Qt signal) ─────────────────────────────

    def _on_new_shots(self, shots: list):
        for shot in shots:
            classification = shot["classification"]
            basket_type    = shot.get("basket_type") or ""

            if classification == "MAKE":
                self.makes += 1
            self.total += 1

            self._refresh_display()
            score_text = f"{self.makes} out of {self.total}"

            if classification == "MAKE":
                banner = f"MAKE!  {basket_type}".strip()
                colour = FG_MAKE
                audio  = f"Swish. {score_text}" if basket_type == "SWISH" else f"Made. {score_text}"
                print(f"MAKE ({basket_type}) | {self.makes}/{self.total}")
            else:
                banner = "Miss"
                colour = FG_MISS
                audio  = f"Miss. {score_text}"
                print(f"MISS | {self.makes}/{self.total}")

            self.event_label.setText(banner)
            self.event_label.setStyleSheet(_lbl_style(colour, 22, italic=True))
            self._speak(audio)

            # Dim banner after FLASH_MS
            QTimer.singleShot(FLASH_MS, lambda: self.event_label.setStyleSheet(
                _lbl_style(FG_IDLE, 22, italic=True)))

    # ── audio ─────────────────────────────────────────────────────────────────

    def _speak(self, text: str):
        try:
            subprocess.Popen(["spd-say", text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # ── UDP receiver loop (background thread) ─────────────────────────────────

    def _receive_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.settimeout(1.0)

        for attempt in range(5):
            try:
                sock.bind((UDP_IP, UDP_PORT))
                print(f"Listening on {UDP_IP}:{UDP_PORT}")
                break
            except OSError:
                if attempt < 4:
                    print(f"Port {UDP_PORT} busy, retrying ({attempt+1}/5)...")
                    time.sleep(2)
                else:
                    print("Could not bind socket. Exiting receiver thread.")
                    return

        pending: list = []

        while self.running:
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                if pending:
                    self._classify_and_notify(pending)
                    pending = []
                continue
            except Exception as e:
                print(f"Socket error: {e}")
                continue

            self._pkt_count += 1

            try:
                # ── header (new 336-byte format) ──────────────────────────
                pkt_ts  = struct.unpack("!I", data[0:4])[0]
                seq_id  = struct.unpack("!H", data[4:6])[0]
                num_mpu = struct.unpack("!B", data[6:7])[0]

                # ── sequence ID check ─────────────────────────────────────
                if self._last_seq >= 0:
                    gap = (seq_id - self._last_seq) & 0xFFFF
                    if gap == 0 or (seq_id < self._last_seq and not
                                    (self._last_seq > 60000 and seq_id < 5000)):
                        continue   # stale / duplicate
                    if gap > 1:
                        print(f"Packet loss: {gap-1} pkt(s) lost "
                              f"(seq {self._last_seq} -> {seq_id})")
                self._last_seq = seq_id

                # ── MPU samples (offset 7) ────────────────────────────────
                mpu_samples = []
                for i in range(num_mpu):
                    off      = 7 + i * 14
                    ts_delta = struct.unpack("!H", data[off:off+2])[0]
                    raw      = struct.unpack("!hhhhhh", data[off+2:off+14])
                    accel    = [v / ACCEL_SENSITIVITY for v in raw[0:3]]
                    gyro     = [v / GYRO_SENSITIVITY  for v in raw[3:6]]
                    mpu_samples.append((accel, gyro, pkt_ts - ts_delta))

                # ── TOF samples ───────────────────────────────────────────
                tof_off  = 7 + num_mpu * 14
                num_tof  = struct.unpack("!B", data[tof_off:tof_off+1])[0]
                tof_samples = []
                for i in range(8):
                    off      = tof_off + 1 + i * 6
                    ts_delta = struct.unpack("!H", data[off:off+2])[0]
                    distance = struct.unpack("!H", data[off+2:off+4])[0]
                    sr       = struct.unpack("!H", data[off+4:off+6])[0]
                    if i < num_tof:
                        tof_samples.append((distance, pkt_ts - ts_delta, sr))

                for i, (accel, gyro, mpu_ts) in enumerate(mpu_samples):
                    if i < len(tof_samples):
                        distance, tof_ts, sr = tof_samples[i]
                    else:
                        distance, tof_ts, sr = 0xFFFE, mpu_ts, 0
                    pending.append({
                        "accel": accel, "gyro": gyro,
                        "distance": distance, "mpu_ts": mpu_ts,
                        "tof_ts": tof_ts, "signal_rate": sr,
                    })

                if len(pending) >= SAMPLES_PER_PACKET:
                    self._classify_and_notify(pending)
                    pending = []

            except Exception as e:
                print(f"Parse error: {e}")
                import traceback; traceback.print_exc()

        sock.close()

    def _classify_and_notify(self, batch: list):
        """Run classifier in receiver thread; emit Qt signal to main thread."""
        new_shots = self.classifier.process_batch(batch)
        if new_shots:
            self._shots_signal.emit(list(new_shots))

    # ── cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.running = False
        self._rate_timer.stop()
        event.accept()


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = ScoreApp()
    win.show()
    sys.exit(app.exec_())
