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


################################ JTAG Controller Class ####################################
class JtagController:
    """Class to encapsulate and manage the JTAG interface via FTDI MPSSE."""
    
    def __init__(self):
        self.device = None  # FTDI device handle

    @staticmethod
    def list_ftdi_devices():
        """Lists all connected FTDI devices."""

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
    
    def _tms_tlr_to_idle(self):
        """Returns MPSSE command to move from Test-Logic-Reset to Run-Test/Idle."""
        # 1 clock with TMS=0
        return b'\x4B\x00\x00'

    def _tms_idle_to_shift_ir(self):
        """Returns MPSSE command to move from Run-Test/Idle to Shift-IR."""
        # TMS sequence: 1, 1, 0, 0 (LSB first = 0x03)
        return b'\x4B\x03\x03'

    def _tms_idle_to_shift_dr(self):
        """Returns MPSSE command to move from Run-Test/Idle to Shift-DR."""
        # TMS sequence: 1, 0, 0 (LSB first = 0x01)
        return b'\x4B\x02\x01'

    # ---------------------------------------

    def open(self, device_index=0, freq_hz=1_000_000):
        if self.is_ready():
            print("JTAG is already open.")
            return
            
        print("Initializing JTAG...")
        try:
            self.device = ftd.open(device_index)
            self.device.setBitMode(0x00, 0) # Reset MPSSE
            time.sleep(0.05)
            self.device.setBitMode(0x0B, 2) # Enable MPSSE mode (0x02) with ADBUS direction 0x0B
            time.sleep(0.05)
            
            # Configure FTDI parameters for optimal MPSSE operation
            self.device.setUSBParameters(4096, 4096)
            self.device.setChars(0, False, 0, False)
            self.device.setTimeouts(1000, 1000)
            self.device.setLatencyTimer(16)
            self.device.purge(ftd.defines.PURGE_RX | ftd.defines.PURGE_TX)
            
            # Setup MPSSE commands for JTAG operation
            setup_cmds = bytearray()
            setup_cmds += b'\x8A\x97\x8D' # Disable advanced clock options
            
            # HARDWARE KEY FOR CUSTOM BOARD
            setup_cmds += b'\x80\x88\xFB'   # Set ADBUS direction and initial state (TCK, TDI, TMS high; TDO input)
            setup_cmds += b'\x82\x00\x00'   # Set ACBUS to High-Z for safety

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
                # Send a final command to reset the TAP controller before closing
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
                print("-" * 80)
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

    def read_fpga_usercode(self):
        """
        Demonstrates targeting a specific TAP.
        Reads the USERCODE (Instruction 0x08) of the Zynq PL (FPGA),
        while putting the ARM DAP in BYPASS (Instruction 0x0F).
        """
        if not self.is_ready():
            print("JTAG device not initialized.")
            return

        print("Targeting FPGA TAP -> Reading USERCODE...")
        try:
            self.device.purge(ftd.defines.PURGE_RX)
            payload = bytearray()
            
            # 1. Reset state machine and go to Idle
            payload += self._tms_reset()
            payload += self._tms_tlr_to_idle()
            
            # ==========================================
            # STEP 1: SHIFT INSTRUCTION (IR)
            # ==========================================
            payload += self._tms_idle_to_shift_ir()
            
            # The chain is TDI -> ARM DAP (4 bits) -> FPGA PL (6 bits) -> TDO.
            # To talk to FPGA, we shift 10 bits total. 
            # The first 6 bits will push through the ARM and land in the FPGA.
            # FPGA USERCODE IR = 0x08 (001000). ARM BYPASS = 0x0F (1111).
            # Sequence (LSB first): 0,0,0,1,0,0 (FPGA) + 1,1,1,1 (ARM)
            # Binary string: 1111 001000 -> LSB first means we shift '000100 1111'
            # Byte 0: 1110 1000 (0xE8) -> 8 bits
            # Byte 1: 0000 0011 (0x03) -> 2 bits
            
            # Send first 8 bits (Command 0x19: clock bytes out, -ve edge, LSB first)
            payload += b'\x19\x00\x00\xE8'
            
            # Send last 2 bits. CRITICAL: The very last bit must be clocked with TMS=1 
            # to exit Shift-IR. So we send 1 bit normally, and the 10th bit via TMS command.
            
            # 9th bit (a '1') with TMS=0 (Command 0x1B: clock bits out)
            payload += b'\x1B\x00\x01' 
            # 10th bit (a '1') with TMS=1 (Command 0x4B: clock bit to TMS)
            # 0x83 = 10000011 -> Bit 0 is Data(1), Bit 1 is TMS(1)
            payload += b'\x4B\x00\x83'
            
            # Return to Idle from Exit1-IR
            payload += self._tms_to_idle()
            
            # ==========================================
            # STEP 2: SHIFT DATA (DR)
            # ==========================================
            payload += self._tms_idle_to_shift_dr()
            
            # Because ARM is in BYPASS, its Data Register is exactly 1 bit long.
            # FPGA USERCODE register is 32 bits long. Total chain DR length = 33 bits.
            # We want to read the 32 bits of the FPGA, which are closest to TDO.
            # So we read 32 bits (4 bytes).
            payload += b'\x28\x03\x00' # Read 4 bytes (0x03 = length-1)
            
            # Exit Shift-DR and go to Idle
            # We don't care about shifting data into TDI here, just moving TMS.
            payload += b'\x4B\x00\x03' # 1 clock, TMS=1 (Exit1-DR)
            payload += self._tms_to_idle()
            
            # Flush
            payload += b'\x87'
            
            self.device.write(bytes(payload))
            time.sleep(0.01)
            
            # Read the 4 bytes back
            rx_data = self.device.read(4)
            if len(rx_data) == 4:
                usercode = struct.unpack('<I', rx_data)[0]
                print(f"FPGA USERCODE: 0x{usercode:08X}")
            else:
                print("Failed to read USERCODE.")
                
        except Exception as e:
            print(f"Error during IR/DR operations: {e}")
            

# --- CLI INTERFACE ---

menu_list = [
    "0. Exit",
    "1. List FTDI devices",
    "2. Open JTAG",
    "3. Close JTAG",
    "4. Scan JTAG",
    "5. Read FPGA USERCODE",
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
        case "5":
            jtag.read_fpga_usercode()
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