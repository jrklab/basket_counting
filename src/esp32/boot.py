import network
import webrepl
import time
import machine

# --- Configuration ---
WIFI_SSID = "AttIsBeter"
WIFI_PASSWORD = "PP123Acalance"
# ---------------------
# import webrepl_setup # password: fortest

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    if not wlan.isconnected():
        print('Connecting to network...')
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        
        # Timeout after 10 seconds if it can't connect
        start_time = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), start_time) > 10000:
                print("\nWiFi Connection Failed! Check credentials.")
                return False
            print(".", end="")
            time.sleep(0.5)
            
    print('\nConnected! Network config:', wlan.ifconfig())
    return True

# 1. Start the connection
if connect_wifi():
    # 2. Initialize WebREPL for wireless debugging
    # Make sure you have already run 'import webrepl_setup' on the device
    try:
        webrepl.start()
        print("WebREPL started. You can now connect wirelessly.")
    except Exception as e:
        print("Could not start WebREPL:", e)

# 3. Garbage collection to keep memory clean for your N16R8 PSRAM
import gc
gc.collect()