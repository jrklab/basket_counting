"""
show_score.py - Lightweight basketball score display.

Receives UDP packets from the ESP32, classifies baskets in the receiver
thread, then schedules minimal GUI updates on the main thread via after().

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
import tkinter as tk
from tkinter import font as tkfont

# Allow running from any directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import UDP_IP, UDP_PORT, SAMPLES_PER_PACKET, ACCEL_SENSITIVITY, GYRO_SENSITIVITY
from shot_classifier import ShotClassifier


# ── visual constants ────────────────────────────────────────────────────────
BG          = "#0d1117"
FG_SCORE    = "#f0e040"   # yellow – main score
FG_TOTAL    = "#888888"   # grey   – denominator / percentage
FG_MAKE     = "#2ecc71"   # green  – "MAKE!" flash
FG_MISS     = "#e74c3c"   # red    – "MISS"  flash
FG_IDLE     = "#333344"   # dimmed – no event yet
FLASH_MS    = 1500        # how long the last-event banner stays coloured


class ScoreApp(tk.Tk):
    """Minimal score-only GUI for real-time basket detection."""

    def __init__(self):
        super().__init__()
        self.title("🏀 Basketball Score")
        self.configure(bg=BG)
        self.geometry("700x500")
        self.minsize(400, 320)

        # ── state ──────────────────────────────────────────────────────────
        self.classifier = ShotClassifier()
        self.makes = 0
        self.total = 0
        self.running = True

        # Packet-rate counter (written by receiver thread, read by main thread)
        self._pkt_count = 0        # packets received since last tick
        self._pkt_rate  = 0        # packets/s displayed

        # ── UI ─────────────────────────────────────────────────────────────
        self._build_ui()

        # ── background receiver thread ──────────────────────────────────────
        Thread(target=self._receive_loop, daemon=True).start()

        # ── 1-second ticker to refresh packet-rate label ────────────────────
        self._tick_rate()

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        # Make the window scale with resizing
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        outer = tk.Frame(self, bg=BG)
        outer.grid(sticky="nsew")
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        inner = tk.Frame(outer, bg=BG)
        inner.grid(row=0, column=0)

        # ── score row  "  8 / 12  " ───────────────────────────────────────
        score_row = tk.Frame(inner, bg=BG)
        score_row.pack(pady=(30, 0))

        self.makes_label = tk.Label(
            score_row, text="0",
            font=("Arial", 110, "bold"), bg=BG, fg=FG_SCORE)
        self.makes_label.pack(side=tk.LEFT)

        tk.Label(score_row, text=" / ", font=("Arial", 60), bg=BG, fg=FG_TOTAL
                 ).pack(side=tk.LEFT, pady=(20, 0))

        self.total_label = tk.Label(
            score_row, text="0",
            font=("Arial", 60), bg=BG, fg=FG_TOTAL)
        self.total_label.pack(side=tk.LEFT, pady=(20, 0))

        # ── percentage ────────────────────────────────────────────────────
        self.pct_label = tk.Label(
            inner, text="— %",
            font=("Arial", 32), bg=BG, fg=FG_TOTAL)
        self.pct_label.pack(pady=(0, 8))

        # ── last-event banner ─────────────────────────────────────────────
        self.event_label = tk.Label(
            inner, text="waiting…",
            font=("Arial", 22, "italic"), bg=BG, fg=FG_IDLE)
        self.event_label.pack(pady=(0, 20))

        # ── clear button ──────────────────────────────────────────────────
        tk.Button(
            inner, text="Clear Score",
            command=self._clear_score,
            font=("Arial", 14, "bold"),
            bg="#c0392b", fg="white", activebackground="#e74c3c",
            relief=tk.FLAT, padx=24, pady=10, cursor="hand2"
        ).pack(pady=8)

        # ── packet-rate indicator (bottom-left, outside inner frame) ─────────
        self.rate_label = tk.Label(
            outer, text="UDP: — pkt/s",
            font=("Arial", 10), bg=BG, fg="#555566",
            anchor="w")
        self.rate_label.grid(row=1, column=0, sticky="sw", padx=10, pady=6)
        outer.rowconfigure(1, weight=0)

    # ── packet-rate ticker (main thread) ─────────────────────────────────────

    def _tick_rate(self):
        """Called every 1 s on the main thread to refresh the pkt/s label."""
        rate = self._pkt_count          # snapshot
        self._pkt_count = 0             # reset counter
        self._pkt_rate  = rate

        if rate == 0:
            colour, text = "#e74c3c", "UDP: 0 pkt/s  (!) no signal"
        elif rate < 7:
            colour, text = "#e67e22", f"UDP: {rate} pkt/s  ↓ low"
        else:
            colour, text = "#555566", f"UDP: {rate} pkt/s"

        self.rate_label.config(text=text, fg=colour)

        if self.running:
            self.after(1000, self._tick_rate)

    # ── score helpers ────────────────────────────────────────────────────────

    def _refresh_display(self):
        pct = (self.makes / self.total * 100) if self.total else 0
        self.makes_label.config(text=str(self.makes))
        self.total_label.config(text=str(self.total))
        self.pct_label.config(text=f"{pct:.0f} %")

    def _clear_score(self):
        """Reset everything without touching the socket."""
        self.classifier.reset()
        self.makes = 0
        self.total = 0
        self._refresh_display()
        self.event_label.config(text="cleared", fg=FG_IDLE)
        print("Score cleared.")

    # ── shot callback (always runs on main thread via after()) ───────────────

    def _on_new_shots(self, shots):
        for shot in shots:
            classification = shot["classification"]
            basket_type    = shot.get("basket_type") or ""

            if classification == "MAKE":
                self.makes += 1
            self.total += 1

            self._refresh_display()

            score_text = f"{self.makes} out of {self.total}"

            if classification == "MAKE":
                banner = f"🏀  MAKE!  {basket_type}".strip()
                self.event_label.config(text=banner, fg=FG_MAKE)
                audio = f"Swish. {score_text}" if basket_type == "SWISH" else f"Made. {score_text}"
                print(f"🏀 MAKE ({basket_type}) | {self.makes}/{self.total}")
            else:
                self.event_label.config(text="❌  Miss", fg=FG_MISS)
                audio = f"Miss. {score_text}"
                print(f"❌ MISS | {self.makes}/{self.total}")

            self._speak(audio)

            # Dim the banner after FLASH_MS ms
            self.after(FLASH_MS, lambda: self.event_label.config(fg=FG_IDLE))

    # ── audio ────────────────────────────────────────────────────────────────

    def _speak(self, text):
        """Fire-and-forget TTS via spd-say (non-blocking)."""
        try:
            subprocess.Popen(
                ["spd-say", text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass  # TTS unavailable – silently ignore

    # ── UDP receiver loop (background thread) ────────────────────────────────

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
                print(f"✅  Listening on {UDP_IP}:{UDP_PORT}")
                break
            except OSError:
                if attempt < 4:
                    print(f"⏳  Port {UDP_PORT} busy, retrying ({attempt+1}/5)…")
                    time.sleep(2)
                else:
                    print("❌  Could not bind socket. Exiting receiver thread.")
                    return

        pending: list = []

        while self.running:
            # ── receive one UDP packet ─────────────────────────────────────
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                # Flush whatever is pending on timeout
                if pending:
                    self._classify_and_notify(pending)
                    pending = []
                continue
            except Exception as e:
                print(f"Socket error: {e}")
                continue

            # ── parse packet ───────────────────────────────────────────────
            self._pkt_count += 1
            try:
                pkt_ts       = struct.unpack("!I", data[0:4])[0]
                num_mpu      = struct.unpack("!B", data[4:5])[0]

                mpu_samples = []
                for i in range(num_mpu):
                    off      = 5 + i * 14
                    ts_delta = struct.unpack("!H", data[off:off+2])[0]
                    raw      = struct.unpack("!hhhhhh", data[off+2:off+14])
                    ts       = pkt_ts - ts_delta
                    accel    = [v / ACCEL_SENSITIVITY for v in raw[0:3]]
                    gyro     = [v / GYRO_SENSITIVITY  for v in raw[3:6]]
                    mpu_samples.append((accel, gyro, ts))

                tof_off  = 5 + num_mpu * 14
                num_tof  = struct.unpack("!B", data[tof_off:tof_off+1])[0]
                tof_samples = []
                for i in range(8):          # packet always has 8 fixed slots
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
                        "accel":       accel,
                        "gyro":        gyro,
                        "distance":    distance,
                        "mpu_ts":      mpu_ts,
                        "tof_ts":      tof_ts,
                        "signal_rate": sr,
                    })

                # Process a full packet's worth at once to match the
                # classifier's expected batch size
                if len(pending) >= SAMPLES_PER_PACKET:
                    self._classify_and_notify(pending)
                    pending = []

            except Exception as e:
                print(f"Parse error: {e}")
                import traceback; traceback.print_exc()

        sock.close()

    def _classify_and_notify(self, batch: list):
        """Run classifier in receiver thread; push results to main thread."""
        new_shots = self.classifier.process_batch(batch)
        if new_shots:
            # after(0, …) is the only safe way to call into tkinter from a
            # non-main thread.  It adds < 1 ms of latency.
            self.after(0, self._on_new_shots, list(new_shots))

    # ── cleanup ──────────────────────────────────────────────────────────────

    def destroy(self):
        self.running = False
        super().destroy()


# ── entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = ScoreApp()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        print("\nExiting…")
    finally:
        app.running = False
