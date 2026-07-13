#########################################################
#               JTAG management tool                    #
#########################################################

import ftd2xx as ftd
import time
import struct


# Dictionary of known TAP IDCODEs (with the 4-bit Revision masked out to 0)
KNOWN_TAPS = {
    # --- ARM Cores ---
    0x0BA00477: "ARM Cortex-A9 CoreSight DAP (Zynq 7000)",
    0x0BA02477: "ARM Cortex-A53 CoreSight DAP (Zynq UltraScale+)",
    0x0BA04477: "ARM Cortex-R5 CoreSight DAP",
    
    # --- Xilinx Zynq 7000 Series ---
    0x03722093: "Xilinx Zynq Z-7010",
    0x03727093: "Xilinx Zynq Z-7015 / Z-7020",
    0x0372C093: "Xilinx Zynq Z-7030",
    0x03731093: "Xilinx Zynq Z-7045",
    0x03736093: "Xilinx Zynq Z-7100",
    
    # --- Xilinx Zynq UltraScale+ (Common) ---
    0x04711093: "Xilinx Zynq UltraScale+ ZU2EG/ZU3EG",
    0x04721093: "Xilinx Zynq UltraScale+ ZU4/ZU5/ZU7"
}


class JtagController:
    """Class to encapsulate and manage the JTAG interface via FTDI MPSSE."""
    
    def __init__(self):
        self.device = None

    @staticmethod
    def list_ftdi_devices():
        print("Scan for FTDI devices in progress...")
        try:
            # Added safety try-catch for D2XX driver issues
            devices = ftd.listDevices()
        except Exception as e:
            print(f"Error communicating with FTDI driver: {e}")
            return

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
            self.device.getQueueStatus()
            return True
        except ftd.DeviceError:
            return False

    # --- JTAG STATE MACHINE ABSTRACTIONS ---

    def _tms_reset(self):
        """Returns MPSSE command to reset the TAP controller (Test-Logic-Reset)."""
        # Send 32 clocks with TMS=1 to force reset state
        return b'\x4B\x07\xFF' * 4

    def _tms_to_shift_dr(self):
        """Returns MPSSE command to move from Test-Logic-Reset to Shift-DR."""
        # TMS sequence: 0, 1, 0, 0
        return b'\x4B\x03\x02'

    def _tms_to_idle(self):
        """Returns MPSSE command to move from Shift-xR to Run-Test/Idle."""
        # TMS sequence: 1, 1, 0
        return b'\x4B\x02\x03'

    # ---------------------------------------

    def open(self, device_index=0, freq_hz=1_000_000):
        if self.is_ready():
            print("JTAG is already open.")
            return
            
        print("Initializing JTAG...")
        try:
            self.device = ftd.open(device_index)
            self.device.setBitMode(0x00, 0)
            time.sleep(0.05)
            self.device.setBitMode(0x0B, 2)
            time.sleep(0.05)
            
            self.device.setUSBParameters(4096, 4096)
            self.device.setChars(0, False, 0, False)
            self.device.setTimeouts(1000, 1000)
            self.device.setLatencyTimer(16)
            self.device.purge(ftd.defines.PURGE_RX | ftd.defines.PURGE_TX)
            
            setup_cmds = bytearray()
            setup_cmds += b'\x8A\x97\x8D' # Disable advanced clock options
            
            # HARDWARE KEY FOR CUSTOM BOARD
            setup_cmds += b'\x80\x88\xFB' 
            # Set ACBUS to High-Z for safety
            setup_cmds += b'\x82\x00\x00'

            # Set JTAG Clock (Base clock is 60MHz. TCK = 60MHz / ((1 + divisor) * 2))
            # Moving clock configuration here and calculating divisor dynamically
            divisor = int((30_000_000 / freq_hz) - 1)
            divisor = max(0, min(65535, divisor)) # Clamp between 0x0000 and 0xFFFF
            setup_cmds += struct.pack('<BH', 0x86, divisor)
            
            self.device.write(bytes(setup_cmds))
            print(f"FTDI connection opened. TCK set to ~{freq_hz/1e6:.1f} MHz.")
        except Exception as e:
            print(f"Error initializing FTDI: {e}")
            self.device = None

    def close(self):
        if self.is_ready():
            try:
                self.device.write(b'\x80\x00\x00')
                self.device.close()
                print("FTDI connection closed.")
            except Exception as e:
                print(f"Error during close: {e}")
            finally:
                self.device = None
        else:
            print("JTAG is not open.")

    def scan(self, max_devices=8):
        """
        Blind scan of the JTAG chain.
        Reads up to 'max_devices' (default 8) to find all TAPs in the chain.
        """
        if not self.is_ready():
            print("JTAG device not initialized. Please Open JTAG first.")
            return

        try:
            print("Scanning JTAG chain (Blind Scan)...")
            self.device.purge(ftd.defines.PURGE_RX)
            
            # Using the abstracted TAP methods to build the payload
            mpsse_payload = bytearray()
            mpsse_payload += self._tms_reset()
            mpsse_payload += self._tms_to_shift_dr()
            
            # Read 'max_devices' * 4 bytes (e.g., 8 devices = 32 bytes)
            bytes_to_read = max_devices * 4
            # MPSSE Command 0x28: length is (bytes - 1)
            length_val = bytes_to_read - 1
            mpsse_payload += b'\x28' + struct.pack('<H', length_val)
            
            mpsse_payload += self._tms_to_idle()
            mpsse_payload += b'\x87'              
            
            self.device.write(bytes(mpsse_payload))
            time.sleep(0.01)
            
            rx_data = self.device.read(bytes_to_read)
            
            if len(rx_data) == bytes_to_read:
                print("\n" + "-" * 80)
                print(f"{'TAP':<5} | {'RAW IDCODE':<10} | {'DEVICE DESCRIPTION'}")
                print("-" * 80)
                
                tap_count = 0
                for i in range(max_devices):
                    chunk = rx_data[i*4 : (i+1)*4]
                    idcode = struct.unpack('<I', chunk)[0]
                    
                    if idcode == 0xFFFFFFFF:
                        break
                    
                    if (idcode & 0x01) == 0:
                        # Fallback for bypassed TAPs (Using 'tap_count + 1' for visual alignment)
                        print(f"{i + 1:<5} | 0x{idcode:08X} | Warning: Non-IDCODE / Bypass bit detected")
                        continue
                    
                    # Valid TAP found, increment counter
                    tap_count += 1
                        
                    masked_id = idcode & 0x0FFFFFFF
                    device_name = KNOWN_TAPS.get(masked_id, "Unknown Device")
                    
                    # Print using the real tap_count for perfect visual alignment
                    print(f"{i:<5} | 0x{idcode:08X} | {device_name}")
                    
                print("-" * 80)
                print(f"Total devices found: {tap_count}\n")
            else:
                print(f"JTAG Error: Read {len(rx_data)} bytes instead of {bytes_to_read}.")
                
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
    jtag = JtagController()
    
    show_menu()
    while main_loop(jtag):
        pass

    print("Exiting...")
    jtag.close()