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
        Demonstrates targeting a specific TAP using the dynamic shift engine.
        Reads the USERCODE (Instruction 0x08) of the Zynq PL (FPGA),
        while putting the ARM DAP in BYPASS (Instruction 0x0F).
        """
        if not self.is_ready():
            print("JTAG device not initialized.")
            return

        print("\nTargeting FPGA TAP -> Reading USERCODE...")
        try:
            self.device.purge(ftd.defines.PURGE_RX)
            
            # 1. Reset state machine and go to Idle
            self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
            
            # ==========================================
            # STEP 1: SHIFT INSTRUCTION (IR)
            # ==========================================
            # Target FPGA (Index 0) with Instruction 0x08
            self.shift_ir(0x08, tap_index=0)
            
            # ==========================================
            # STEP 2: SHIFT DATA (DR)
            # ==========================================
            # Since ARM is in BYPASS (1 bit) and FPGA USERCODE is 32 bits,
            # we read the first 32 bits closest to TDO (which belong to the FPGA).
            payload = bytearray()
            payload += self._tms_idle_to_shift_dr()
            payload += b'\x28\x03\x00' # Read 4 bytes (0x03 = length-1)
            payload += b'\x4B\x00\x03' # Exit1-DR (1 clock with TMS=1)
            payload += self._tms_to_idle()
            payload += b'\x87'         # Flush
            
            self.device.write(bytes(payload))
            time.sleep(0.01)
            
            rx_data = self.device.read(4)
            if len(rx_data) == 4:
                usercode = struct.unpack('<I', rx_data)[0]
                print(f"FPGA USERCODE: 0x{usercode:08X}")
            else:
                print("Failed to read USERCODE.")
                
        except Exception as e:
            print(f"Error during IR/DR operations: {e}")

    # --- DYNAMIC SHIFT ENGINE ---

    def shift_ir(self, instruction, tap_index):
        """
        Sends an instruction to a specific TAP, putting others in BYPASS (all 1s).
        - tap_index 0 = FPGA (PL) [IR = 6 bit]
        - tap_index 1 = ARM (PS)  [IR = 4 bit]
        """
        # Zynq 7000 Chain: TDI -> ARM (4 bit) -> FPGA (6 bit) -> TDO
        
        if tap_index == 1: # Target ARM
            # Send 6 bits of BYPASS for FPGA, then the ARM IR
            total_bits = 6 + 4
            shift_value = (instruction << 6) | 0x3F
        elif tap_index == 0: # Target FPGA
            # Send ARM BYPASS, then the FPGA IR
            total_bits = 6 + 4
            shift_value = (0x0F << 6) | instruction
        else:
            raise ValueError("Invalid TAP index.")

        self._shift_bits(shift_value, total_bits, is_ir=True)

    def _shift_bits(self, data_val, num_bits, is_ir=False):
        """
        Low-level MPSSE engine. Calculates exact bytes and remaining bits
        to shift into the TAP state machine.
        """
        payload = bytearray()
        
        # Navigate to Shift-IR or Shift-DR
        payload += self._tms_idle_to_shift_ir() if is_ir else self._tms_idle_to_shift_dr()
            
        # Calculate full bytes and remaining bits
        num_bytes = (num_bits - 1) // 8
        remaining_bits = (num_bits - 1) % 8
        
        # Extract the very last bit (must be sent with TMS=1 to exit shift state)
        last_bit = (data_val >> (num_bits - 1)) & 0x01
        
        # 1. Send full bytes (if any)
        if num_bytes > 0:
            # Command 0x19: Clock Data Bytes Out on -ve edge, LSB first
            payload += b'\x19' + struct.pack('<H', num_bytes - 1)
            byte_mask = (1 << (num_bytes * 8)) - 1
            payload += (data_val & byte_mask).to_bytes(num_bytes, byteorder='little')
            
        # 2. Send remaining bits (excluding the final bit)
        if remaining_bits > 0:
            # Command 0x1B: Clock Data Bits Out on -ve edge, LSB first
            payload += b'\x1B' + struct.pack('<B', remaining_bits - 1)
            bit_data = (data_val >> (num_bytes * 8)) & 0xFF
            payload += struct.pack('<B', bit_data)
            
        # 3. Send the final bit with TMS=1 (Exit1-xR)
        # Command 0x4B: Clock Data to TMS pin (Bit 0 = TDI, Bit 1 = TMS)
        tms_byte = 0x82 | last_bit
        payload += b'\x4B\x00' + struct.pack('<B', tms_byte)
        
        # Return to Run-Test/Idle
        payload += self._tms_to_idle()
        
        self.device.write(bytes(payload))


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