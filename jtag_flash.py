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
        """Lists all connected FTDI devices with rich details."""
        print("Scanning for FTDI devices...")
        try:
            # Use createDeviceInfoList to populate the internal driver list
            num_devices = ftd.createDeviceInfoList()
            if num_devices == 0:
                print("No FTDI devices detected.")
                return
            
            print(f"Found {num_devices} FTDI endpoint(s):")
            for i in range(num_devices):
                detail = ftd.getDeviceInfoDetail(i)
                # In the ftd2xx Python wrapper, 'detail' is a dictionary
                desc = detail.get('description', b'Unknown').decode('utf-8', errors='ignore')
                serial = detail.get('serial', b'Unknown').decode('utf-8', errors='ignore')
                
                print(f"  Index {i}: {desc} (Serial: {serial})")

        except Exception as e:
            # Added type(e).__name__ to avoid masking Python exceptions in the future
            print(f"Error communicating with FTDI driver: {type(e).__name__} - {e}")
            return

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
        # TMS sequence: 1, 1, 0 (3 clocks)
        return b'\x4B\x02\x03'
        
    def _tms_exit_to_idle(self):
        """Returns MPSSE command to move from Exit1-xR to Run-Test/Idle."""
        # TMS sequence: 1 (Update-xR), 0 (Run-Test/Idle)
        # LSB first = 0x01 (2 clocks)
        return b'\x4B\x01\x01'
    
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
            divisor = int((30_000_000 / freq_hz) - 1)
            divisor = max(0, min(65535, divisor)) # Clamp between 0x0000 and 0xFFFF
            setup_cmds += struct.pack('<BH', 0x86, divisor)
            
            self.device.write(bytes(setup_cmds))
            
            # --- TARGET POWER CHECK ---
            # Do a dummy read to see if the target board is powered on
            self.device.purge(ftd.defines.PURGE_RX)
            self.device.write(self._tms_reset() + self._tms_to_shift_dr())
            payload = b'\x28\x03\x00' + self._tms_to_idle() + b'\x87' # Read 32 bits
            self.device.write(payload)
            time.sleep(0.01)
            
            rx_data = self.device.read(4)
            if len(rx_data) == 4:
                test_val = struct.unpack('<I', rx_data)[0]
                if test_val == 0xFFFFFFFF or test_val == 0x00000000:
                    print(f"WARNING: FTDI opened, but JTAG chain is DEAD (Read: 0x{test_val:08X}).")
                    print("Is the Zynq board powered off?")
                    self.device.close()
                    self.device = None
                    return
            else:
                print("WARNING: Failed to read from JTAG chain. Target might be off.")
                self.device.close()
                self.device = None
                return
                
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
                        print(f"{i + 1:<5} | 0x{idcode:08X} | Warning: Non-IDCODE / Bypass bit detected")
                        continue
                    
                    tap_count += 1
                        
                    masked_id = idcode & 0x0FFFFFFF
                    device_name = KNOWN_TAPS.get(masked_id, "Unknown Device")
                    
                    print(f"{i:<5} | 0x{idcode:08X} | {device_name}")
                    
                print("-" * 80)
                print(f"Total devices found: {tap_count}\n")
            else:
                print(f"JTAG Error: Read {len(rx_data)} bytes instead of {bytes_to_read}.")
                
        except Exception as e:
            print(f"Error during scan: {e}")

    def read_fpga_usercode(self):
        """
        Reads the USERCODE (Instruction 0x08) of the Zynq PL (FPGA),
        utilizing the new dynamic Full-Duplex shift engine.
        """
        if not self.is_ready():
            print("JTAG device not initialized.")
            return

        print("Targeting FPGA TAP -> Reading USERCODE...")
        try:
            self.device.purge(ftd.defines.PURGE_RX)
            
            # Reset state machine and go to Idle
            self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
            
            # Step 1: Target FPGA (Index 0) with Instruction 0x08
            self.shift_ir(0x08, tap_index=0)
            
            # Step 2: Read 32 bits from the FPGA Data Register
            usercode = self.shift_dr(0x00000000, dr_len=32, tap_index=0)
            
            print(f"FPGA USERCODE: 0x{usercode:08X}")
                
        except Exception as e:
            print(f"Error during IR/DR operations: {e}")

    # --- DYNAMIC SHIFT ENGINE (FULL DUPLEX) ---

    def shift_ir(self, instruction, tap_index):
        """
        Shifts the instruction into the target TAP while putting the other in BYPASS.
        - tap_index 0 = FPGA (PL) [IR = 6 bit]
        - tap_index 1 = ARM (PS)  [IR = 4 bit]
        """
        # Zynq 7000 Chain: TDI -> ARM (4 bit) -> FPGA (6 bit) -> TDO
        if tap_index == 1: # Target ARM
            total_bits = 6 + 4
            shift_value = (instruction << 6) | 0x3F
        elif tap_index == 0: # Target FPGA
            total_bits = 6 + 4
            shift_value = (0x0F << 6) | instruction
        else:
            raise ValueError("Invalid TAP index.")

        self._shift_bits(shift_value, total_bits, is_ir=True)

    def shift_dr(self, data_val, dr_len, tap_index):
        """
        Shifts data into the target TAP DR and reads the response.
        Dynamically compensates for the bypass bit of the ignored TAP.
        """
        if tap_index == 1: # Target ARM
            # Chain: TDI -> ARM (dr_len bits) -> FPGA BYPASS (1 bit) -> TDO
            total_bits = dr_len + 1
            # The first bit pushed in ends up in FPGA, followed by ARM data
            shift_value = (data_val << 1) | 0x01
        elif tap_index == 0: # Target FPGA
            # Chain: TDI -> ARM BYPASS (1 bit) -> FPGA (dr_len bits) -> TDO
            total_bits = dr_len + 1
            shift_value = (0x01 << dr_len) | data_val
        else:
            raise ValueError("Invalid TAP index.")

        rx_val = self._shift_bits(shift_value, total_bits, is_ir=False)
        
        # FPGA is closest to TDO, so its data (or bypass bit) comes out first
        if tap_index == 1:
            # ARM is targeted. FPGA bypass bit is at bit 0. ARM data is shifted left by 1.
            return (rx_val >> 1) & ((1 << dr_len) - 1)
        else:
            # FPGA is targeted. FPGA data is at bits 0 to (dr_len - 1).
            return rx_val & ((1 << dr_len) - 1)

    def _shift_bits(self, data_val, num_bits, is_ir=False):
        """
        Full-Duplex MPSSE engine. Writes and reads bits simultaneously.
        Returns the numeric value read from the TDO pin.
        """
        payload = bytearray()
        payload += self._tms_idle_to_shift_ir() if is_ir else self._tms_idle_to_shift_dr()
            
        num_bytes = (num_bits - 1) // 8
        remaining_bits = (num_bits - 1) % 8
        last_bit = (data_val >> (num_bits - 1)) & 0x01
        
        # 1. Full bytes (Command 0x39: Clock Data Bytes In & Out, LSB first)
        if num_bytes > 0:
            payload += b'\x39' + struct.pack('<H', num_bytes - 1)
            byte_mask = (1 << (num_bytes * 8)) - 1
            payload += (data_val & byte_mask).to_bytes(num_bytes, byteorder='little')
            
        # 2. Remaining bits (Command 0x3B: Clock Data Bits In & Out, LSB first)
        if remaining_bits > 0:
            payload += b'\x3B' + struct.pack('<B', remaining_bits - 1)
            bit_data = (data_val >> (num_bytes * 8)) & 0xFF
            payload += struct.pack('<B', bit_data)
            
        # 3. Last bit with TMS=1 (Command 0x6B: Clock Data to TMS with Read)
        # Bit 0 = TMS (forced to 1 to exit Shift state)
        # Bit 7 = TDI (our last data bit)
        tms_byte = 0x01 | (last_bit << 7)
        payload += b'\x6B\x00' + struct.pack('<B', tms_byte)
        
        # Go back to Run-Test/Idle using the correct 2-clock exit sequence
        payload += self._tms_exit_to_idle()
        payload += b'\x87' # Flush
        
        self.device.write(bytes(payload))
        
        # --- READ PHASE ---
        expected_rx_len = num_bytes + (1 if remaining_bits > 0 else 0) + 1
        rx_data = self.device.read(expected_rx_len)
        
        rx_val = 0
        if len(rx_data) == expected_rx_len:
            idx = 0
            if num_bytes > 0:
                rx_val = int.from_bytes(rx_data[0:num_bytes], byteorder='little')
                idx += num_bytes
            if remaining_bits > 0:
                extra_bits = rx_data[idx] >> (8 - remaining_bits)
                rx_val |= (extra_bits << (num_bytes * 8))
                idx += 1
            last_rx_bit = (rx_data[idx] >> 7) & 0x01
            rx_val |= (last_rx_bit << (num_bits - 1))
            
        return rx_val

    # ==========================================
    # TEST ARM CORESIGHT DAP
    # ==========================================
    def test_arm_dap(self):
        """Interrogates and initializes the ARM Debug Port (CoreSight JTAG-DP)."""
        if not self.is_ready():
            return

        print("Targeting ARM DAP -> CoreSight Initialization Sequence...")
        self.device.purge(ftd.defines.PURGE_RX)
        
        # Ensure we start from Idle state
        self.device.write(self._tms_reset() + self._tms_tlr_to_idle())

        # ==========================================
        # STEP 1: Direct ARM IDCODE
        # ==========================================
        self.shift_ir(0x0E, tap_index=1)
        rx_idcode = self.shift_dr(0x00000000, dr_len=32, tap_index=1)
        print(f"ARM IDCODE     : 0x{rx_idcode:08X} (Expected: 0x4BA00477)")

        # ==========================================
        # STEP 2: DAP Initialization Sequence
        # ==========================================
        self.shift_ir(0x0A, tap_index=1) # Select DPACC (Debug Port Access)

        # In JTAG-DP, the response to a command arrives in the NEXT transaction.
        # We queue commands in a pipeline.

        # TX 1: Write ABORT (Addr 0) to clear any sticky errors left by the reset
        # RnW=0 (Write), A[3:2]=0, Data=0x1E
        req_abort = (0x0000001E << 3) | (0 << 1) | 0
        self.shift_dr(req_abort, dr_len=35, tap_index=1)

        # TX 2: Write CTRL/STAT (Addr 4) to request Power Up
        # RnW=0 (Write), A[3:2]=1, Data=0x50000000 (CSYSPWRUPREQ | CDBGPWRUPREQ)
        req_powerup = (0x50000000 << 3) | (1 << 1) | 0
        rx_val = self.shift_dr(req_powerup, dr_len=35, tap_index=1)
        print(f"ABORT ACK      : 0x{rx_val & 0x7:02X} (Expected: 0x02 = OK)")

        # TX 3: Read CTRL/STAT (Addr 4) to verify power status
        # RnW=1 (Read), A[3:2]=1, Data=0
        req_read_ctrl = (0x00000000 << 3) | (1 << 1) | 1
        rx_val = self.shift_dr(req_read_ctrl, dr_len=35, tap_index=1)
        print(f"PWRUP ACK      : 0x{rx_val & 0x7:02X} (Expected: 0x02 = OK)")

        # TX 4: Dummy Read to clock out the DATA result of TX 3
        rx_val = self.shift_dr(req_read_ctrl, dr_len=35, tap_index=1)
        ack = rx_val & 0x07
        ctrl_stat = (rx_val >> 3) & 0xFFFFFFFF
        
        print(f"CTRL/STAT ACK  : 0x{ack:02X} (Expected: 0x02 = OK)")
        print(f"CTRL/STAT DATA : 0x{ctrl_stat:08X} (Expected to start with 0xF... meaning Powered Up!)")

    # --- CORESIGHT DAP MEMORY ACCESS (AHB-AP) ---

    def _dap_write(self, is_ap, a32, data):
        """
        Low-level write to DP (is_ap=False) or AP (is_ap=True).
        a32 is the A[3:2] address bits (0 to 3).
        """
        ir = 0x0B if is_ap else 0x0A
        self.shift_ir(ir, tap_index=1)
        # RnW=0 (Write)
        req = (data << 3) | (a32 << 1) | 0
        rx_val = self.shift_dr(req, dr_len=35, tap_index=1)
        return rx_val & 0x07 # Return ACK

    def _dap_read(self, is_ap, a32):
        """
        Low-level read from DP (is_ap=False) or AP (is_ap=True).
        """
        ir = 0x0B if is_ap else 0x0A
        self.shift_ir(ir, tap_index=1)
        # RnW=1 (Read)
        req = (0 << 3) | (a32 << 1) | 1
        
        # Issue read request (first response is from previous transaction)
        self.shift_dr(req, dr_len=35, tap_index=1)
        # Dummy read to get the actual data
        rx_val = self.shift_dr(req, dr_len=35, tap_index=1)
        
        return (rx_val >> 3) & 0xFFFFFFFF, rx_val & 0x07

    def init_ahb_ap(self):
        """Initializes the AHB-AP for memory access."""
        # 1. Power up DP (System & Debug Power)
        self._dap_write(is_ap=False, a32=1, data=0x50000000) # DP CTRL/STAT
        
        # 2. Select AP 0 (AHB-AP) and Bank 0 via DP SELECT register (A[3:2] = 2)
        # APSEL = 0x00, APBANKSEL = 0x00
        self._dap_write(is_ap=False, a32=2, data=0x00000000)
        
        # 3. Configure AP CSW (Control/Status Word) for 32-bit transfer (Size=2)
        # and Auto-increment (AddrInc=1 for sequential writes).
        # CSW is at AP Bank 0, Offset 0x00 (a32=0)
        self._dap_write(is_ap=True, a32=0, data=0x23000002)

    def write_mem32(self, address, data):
        """Writes a 32-bit word to the physical memory address."""
        # Write Address to TAR (Transfer Address Register, Offset 0x04 -> a32=1)
        self._dap_write(is_ap=True, a32=1, data=address)
        # Write Data to DRW (Data Read/Write Register, Offset 0x0C -> a32=3)
        self._dap_write(is_ap=True, a32=3, data=data)

    def read_mem32(self, address):
        """Reads a 32-bit word from the physical memory address."""
        # Write Address to TAR
        self._dap_write(is_ap=True, a32=1, data=address)
        # Read from DRW
        data, ack = self._dap_read(is_ap=True, a32=3)
        return data

    # ==========================================
    # TEST OCM RAM
    # ==========================================
    def test_ocm_ram(self):
        """Verifies we can read/write the Zynq internal OCM memory."""
        if not self.is_ready():
            return
            
        print("\nTargeting ARM AHB-AP -> Testing OCM Memory Access...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
        
        # 1. Initialize the AHB-AP bridge
        self.init_ahb_ap()
        
        # 2. Address 0x00000000 corresponds to the start of the OCM (On-Chip Memory)
        test_addr = 0x00000000
        magic_word = 0xDEADBEEF
        
        print(f"Writing 0x{magic_word:08X} to address 0x{test_addr:08X}...")
        self.write_mem32(test_addr, magic_word)
        
        # 3. Read back to verify
        read_back = self.read_mem32(test_addr)
        print(f"Read Value : 0x{read_back:08X}")
        
        if read_back == magic_word:
            print("SUCCESS: OCM memory is accessible!")
        else:
            print("ERROR: Memory write failed.")


    # ==========================================
    # ZYNQ HARDWARE INITIALIZATION (Without FSBL)
    # ==========================================
    def init_qspi_hardware(self):
        """Unlocks SLCR registers and configures MIO pins and Clocks for QSPI."""
        if not self.is_ready():
            return
            
        print("\nInitializing Zynq Hardware (SLCR) for QSPI...")
        
        # 1. Unlock SLCR (Official Xilinx unlock key)
        SLCR_UNLOCK_ADDR = 0xF8000008
        SLCR_UNLOCK_KEY  = 0x0000DF0D
        self.write_mem32(SLCR_UNLOCK_ADDR, SLCR_UNLOCK_KEY)
        print(" -> SLCR Unlocked.")

        # 2. MIO 1-6 Pins Configuration
        # Typical value: 0x00003301 or 0x00000301 (LVCMOS, Pull-up enabled, L0_SEL active)
        # Note: This value might vary slightly based on your board's voltage.
        MIO_BASE = 0xF8000700
        mio_qspi_val = 0x00000301 
        
        for mio_pin in range(1, 7):
            mio_addr = MIO_BASE + (mio_pin * 4)
            self.write_mem32(mio_addr, mio_qspi_val)
        
        # MIO 8 for feedback clock (if used on your board)
        # self.write_mem32(MIO_BASE + (8 * 4), mio_qspi_val) 
        
        print(" -> MIO 1-6 pins configured for QSPI.")

        # 3. QSPI Clock Configuration (QSPI_CLK_CTRL)
        QSPI_CLK_CTRL = 0xF800014C
        # Enable the clock and set a conservative primary and secondary divisor
        self.write_mem32(QSPI_CLK_CTRL, 0x00000100) 
        print(" -> QSPI Clock activated.")

        # 4. Lock SLCR (Protection against accidental writes)
        SLCR_LOCK_ADDR = 0xF8000004
        SLCR_LOCK_KEY  = 0x0000767B
        self.write_mem32(SLCR_LOCK_ADDR, SLCR_LOCK_KEY)
        print(" -> SLCR Locked and protected.")
        print("SUCCESS: QSPI Controller ready for communication!")

        
# --- CLI INTERFACE ---

menu_list = [
    "0. Exit",
    "1. List FTDI devices",
    "2. Open JTAG",
    "3. Close JTAG",
    "4. Scan JTAG",
    "5. Read FPGA USERCODE",
    "6. Test ARM DAP",
    "7. Test OCM RAM",
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
        case "6":
            jtag.test_arm_dap()
        case "7":
            jtag.test_ocm_ram()
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