#########################################################
#               JTAG management tool                    #
#########################################################

import ftd2xx as ftd
import time
import struct


class JtagController:
    """Class to encapsulate and manage the JTAG interface via FTDI MPSSE."""
    
    def __init__(self):
        self.device = None

    @staticmethod
    def list_ftdi_devices():
        print("Scan for FTDI devices in progress...")
        devices = ftd.listDevices()
        
        if devices is None:
            print("No FTDI devices detected.")
            return

        print(f"Found {len(devices)} FTDI endpoints:")
        for i, dev in enumerate(devices):
            dev_name = dev.decode('utf-8', errors='ignore')
            print(f"Index {i}: {dev_name}")

    def is_ready(self):
        """Robust method to check if the hardware is open and responding."""
        if self.device is None:
            return False
        try:
            # The heartbeat: if it fails, the hardware is disconnected
            self.device.getQueueStatus()
            return True
        except ftd.DeviceError:
            return False

    def open(self, device_index=0):
        if self.is_ready():
            print("JTAG is already open.")
            return

        print("Initializing JTAG...")
        try:
            self.device = ftd.open(device_index)
            self.device.setBitMode(0x00, 0)
            time.sleep(0.05)
            self.device.setBitMode(0x0B, 2) # MPSSE mode
            time.sleep(0.05)
            
            self.device.setUSBParameters(4096, 4096)
            self.device.setChars(0, False, 0, False)
            self.device.setTimeouts(1000, 1000)
            self.device.setLatencyTimer(16)
            self.device.purge(ftd.defines.PURGE_RX | ftd.defines.PURGE_TX)
            
            setup_cmds = bytearray()
            setup_cmds += b'\x8A\x97\x8D' # Disable advanced clock options
            
            # HARDWARE KEY FOR CUSTOM BOARD
            # 0x88 = ADBUS7 High (Enable), TMS=1, TCK=0, TDI=0
            setup_cmds += b'\x80\x88\xFB' 
            
            # Set ACBUS to High-Z for safety
            setup_cmds += b'\x82\x00\x00'
            
            self.device.write(bytes(setup_cmds))
            print("FTDI connection opened.")

        except Exception as e:
            print(f"Error initializing FTDI: {e}")
            self.device = None

    def close(self):
        if self.is_ready():
            try:
                self.device.write(b'\x80\x00\x00')  # Set pins to High-Z
                self.device.close()
                print("FTDI connection closed.")
            except Exception as e:
                print(f"Error during close: {e}")
            finally:
                self.device = None
        else:
            print("JTAG is not open.")

    def scan(self):
        if not self.is_ready():
            print("JTAG device not initialized. Please Open JTAG first (Option 2).")
            return

        try:
            print("Scanning IDCODEs...")
            self.device.write(b'\x86\x1D\x00') # Set JTAG Clock
            self.device.purge(ftd.defines.PURGE_RX)
            
            mpsse_payload = bytearray()
            mpsse_payload += b'\x4B\x07\xFF' * 4  # JTAG Reset (Test-Logic-Reset)
            mpsse_payload += b'\x4B\x03\x02'      # Go to Shift-DR
            mpsse_payload += b'\x28\x07\x00'      # Read 64 bits (8 Bytes LSB-First)
            mpsse_payload += b'\x4B\x02\x03'      # Go to Run-Test/Idle
            mpsse_payload += b'\x87'              # Flush buffer
            
            self.device.write(bytes(mpsse_payload))
            time.sleep(0.01)
            
            rx_data = self.device.read(8)
            if len(rx_data) == 8:
                idcode_pl, idcode_arm = struct.unpack('<II', rx_data)
                print(f"TAP 1 (FPGA Z-7010) : 0x{idcode_pl:08X}")
                print(f"TAP 2 (ARM Cortex)  : 0x{idcode_arm:08X}")
            else:
                print(f"JTAG Error: Read {len(rx_data)} bytes instead of 8.")
        except Exception as e:
            print(f"Error during scan: {e}")


# --- CLI INTERFACE ---

menu_list = [
    "0. Exit",
    "1. List FTDI devices",
    "2. Open JTAG",
    "3. Close JTAG",
    "4. Scan JTAG",
    "?. Help"
]

def show_menu():
    print("\n" + "-" * 40)
    for item in menu_list:
        print(item)
    print("-" * 40 + "\n")

def main_loop(jtag):
    choice = input("> ")

    match choice:
        case "0":
            return False
        case "1":
            jtag.list_ftdi_devices()
        case "2":
            jtag.open(0)
        case "3":
            jtag.close()
        case "4":
            jtag.scan()
        case "?":
            show_menu()
        case _:
            pass

    return True

if __name__ == '__main__':
    # Instantiate our JTAG controller
    jtag = JtagController()
    
    show_menu()
    while main_loop(jtag):
        pass

    print("Exiting...")
    jtag.close() # Safety close on exit