"""
Main entry point for the basketball shot counter application.
"""

from gui import SensorGui
from data_receiver import DataReceiver


def main():
    """Initialize and run the application."""
    gui = SensorGui()
    receiver = DataReceiver(gui)
    receiver.start()
    
    try:
        gui.mainloop()
    except KeyboardInterrupt:
        print("\nKeyboard interrupt detected. Closing...")
    finally:
        receiver.close()


if __name__ == "__main__":
    main()
