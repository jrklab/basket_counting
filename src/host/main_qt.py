"""
main_qt.py - Entry point for the PyQt5/PyQtGraph basketball shot counter.

Usage:
    cd src/host && python main_qt.py

DataReceiver is unchanged: it calls gui.after(0, gui.update_plots, batch)
which the SensorGui.after() shim dispatches safely onto the main thread.
"""

import sys
from PyQt5.QtWidgets import QApplication

from gui_qt import SensorGui
from data_receiver import DataReceiver


def main():
    app = QApplication(sys.argv)

    gui = SensorGui()
    receiver = DataReceiver(gui)

    # Wire up references (same as original main.py)
    gui.camera_manager = receiver.camera
    gui.receiver       = receiver

    receiver.start()
    gui.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
