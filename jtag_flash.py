"""
JTAG Management Tool for Xilinx Zynq-7000 Series.
Provides low-level JTAG interactions, CoreSight DAP debugging,
On-Chip Memory (OCM) access, and QSPI Flash manipulation via FTDI MPSSE.
"""

import ftd2xx as ftd
import time
import struct


# Dictionary of known TAP IDCODEs
KNOWN_TAPS = {
    0x0BA00477: "ARM Cortex-A9 CoreSight DAP (Zynq 7000)",
    0x0BA02477: "ARM Cortex-A53 CoreSight DAP (Zynq UltraScale+)",
    0x0BA04477: "ARM Cortex-R5 CoreSight DAP",
    0x03722093: "Xilinx Zynq Z-7010",
    0x03727093: "Xilinx Zynq Z-7015 / Z-7020",
    0x0372C093: "Xilinx Zynq Z-7030",
    0x03731093: "Xilinx Zynq Z-7045",
    0x03736093: "Xilinx Zynq Z-7100",
    0x04711093: "Xilinx Zynq UltraScale+ ZU2EG/ZU3EG",
    0x04721093: "Xilinx Zynq UltraScale+ ZU4/ZU5/ZU7"
}

class ZynqRegs:
    """Memory Map and Constants for Xilinx Zynq-7000 Series."""
    
    # SLCR (System Level Control Registers)
    SLCR_UNLOCK_ADDR = 0xF8000008
    SLCR_LOCK_ADDR   = 0xF8000004
    SLCR_UNLOCK_KEY  = 0x0000DF0D
    SLCR_LOCK_KEY    = 0x0000767B
    A9_CPU_RST_CTRL  = 0xF8000244

    # QSPI Controller
    QSPI_BASE        = 0xE000D000
    QSPI_CONFIG      = QSPI_BASE + 0x00
    QSPI_STATUS      = QSPI_BASE + 0x04
    QSPI_ENABLE      = QSPI_BASE + 0x08
    QSPI_TXD_FIFO    = QSPI_BASE + 0x1C
    QSPI_RXD_FIFO    = QSPI_BASE + 0x20
    QSPI_LQSPI_CFG   = QSPI_BASE + 0xA0
    
    # Internal Memory
    OCM_BASE_ADDR    = 0x00000000


class JtagController:
    """
    Class to encapsulate and manage the JTAG interface via FTDI MPSSE.
    """
    
    def __init__(self):
        self.device = None  # FTDI device handle for JTAG communication

    # ==========================================
    # FTDI DEVICE MANAGEMENT
    # ==========================================

    @staticmethod
    def list_ftdi_devices():
        """
        Scans and lists all connected FTDI devices.
        Prints the device index, description, and serial number.
        """
        print("Scanning for FTDI devices...")
        try:
            num_devices = ftd.createDeviceInfoList()
            if num_devices == 0:
                print("No FTDI devices detected.")
                return
            print(f"Found {num_devices} FTDI endpoint(s):")
            for i in range(num_devices):
                detail = ftd.getDeviceInfoDetail(i)
                desc = detail.get('description', b'Unknown').decode('utf-8', errors='ignore')
                serial = detail.get('serial', b'Unknown').decode('utf-8', errors='ignore')
                print(f"  Index {i}: {desc} (Serial: {serial})")
        except Exception as e:
            print(f"Error communicating with FTDI driver: {type(e).__name__} - {e}")
            return

    def is_ready(self) -> bool:
        """
        Checks if the FTDI device is currently open and communicating.

        Returns:
            bool: True if the device is ready, False otherwise.
        """
        if self.device is None: 
            return False
        try:
            self.device.getQueueStatus()
            return True
        except ftd.DeviceError:
            return False

    def open(self, device_index: int = 0, freq_hz: int = 1_000_000):
        """
        Opens the JTAG connection and configures the FTDI MPSSE engine.

        Args:
            device_index (int): The index of the FTDI device to open (default is 0).
            freq_hz (int): The target JTAG clock frequency (TCK) in Hz (default is 1MHz).
        """
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
            
            # Massive buffers for extreme speed during bulk writes
            self.device.setUSBParameters(65536, 65536) 
            self.device.setChars(0, False, 0, False)
            self.device.setTimeouts(1000, 1000)
            self.device.setLatencyTimer(16)
            self.device.purge(ftd.defines.PURGE_RX | ftd.defines.PURGE_TX)
            
            setup_cmds = bytearray(b'\x8A\x97\x8D\x80\x88\xFB\x82\x00\x00')
            divisor = max(0, min(65535, int((30_000_000 / freq_hz) - 1)))
            setup_cmds += struct.pack('<BH', 0x86, divisor)
            self.device.write(bytes(setup_cmds))
            
            # Target power check
            self.device.purge(ftd.defines.PURGE_RX)
            self.device.write(self._tms_reset() + self._tms_to_shift_dr())
            self.device.write(b'\x28\x03\x00' + self._tms_to_idle() + b'\x87')
            time.sleep(0.01)
            
            rx_data = self.device.read(4)
            if len(rx_data) == 4:
                test_val = struct.unpack('<I', rx_data)[0]
                if test_val in (0xFFFFFFFF, 0x00000000):
                    print(f"WARNING: FTDI opened, but JTAG chain is DEAD (Read: 0x{test_val:08X}).")
                    self.device.close()
                    self.device = None
                    return
            else:
                print("WARNING: Target might be off.")
                self.device.close()
                self.device = None
                return
                
            print(f"FTDI connection opened. TCK set to ~{freq_hz/1e6:.1f} MHz.")
        except Exception as e:
            print(f"Error initializing FTDI: {e}")
            self.device = None

    def close(self):
        """Safely closes the active FTDI connection and resets the TAP state."""
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

    # ==========================================
    # JTAG STATE MACHINE LOW-LEVEL
    # ==========================================
    
    def _tms_reset(self): return b'\x4B\x07\xFF' * 4
    def _tms_to_shift_dr(self): return b'\x4B\x03\x02'
    def _tms_to_idle(self): return b'\x4B\x02\x03'
    def _tms_exit_to_idle(self): return b'\x4B\x01\x01'
    def _tms_tlr_to_idle(self): return b'\x4B\x00\x00'
    def _tms_idle_to_shift_ir(self): return b'\x4B\x03\x03'
    def _tms_idle_to_shift_dr(self): return b'\x4B\x02\x01'

    def _shift_bits(self, data_val, num_bits, is_ir=False):
        """Core full-duplex MPSSE engine. Writes and reads bits simultaneously."""
        payload = bytearray(self._tms_idle_to_shift_ir() if is_ir else self._tms_idle_to_shift_dr())
        num_bytes, remaining_bits = (num_bits - 1) // 8, (num_bits - 1) % 8
        last_bit = (data_val >> (num_bits - 1)) & 0x01
        
        if num_bytes > 0:
            payload += b'\x39' + struct.pack('<H', num_bytes - 1) + (data_val & ((1 << (num_bytes * 8)) - 1)).to_bytes(num_bytes, 'little')
        if remaining_bits > 0:
            payload += b'\x3B' + struct.pack('<B', remaining_bits - 1) + struct.pack('<B', (data_val >> (num_bytes * 8)) & 0xFF)
        
        payload += b'\x6B\x00' + struct.pack('<B', 0x01 | (last_bit << 7))
        payload += self._tms_exit_to_idle() + b'\x87'
        self.device.write(bytes(payload))
        
        rx_data = self.device.read(num_bytes + (1 if remaining_bits > 0 else 0) + 1)
        rx_val = 0
        if len(rx_data) == (num_bytes + (1 if remaining_bits > 0 else 0) + 1):
            idx = 0
            if num_bytes > 0:
                rx_val = int.from_bytes(rx_data[0:num_bytes], 'little')
                idx += num_bytes
            if remaining_bits > 0:
                rx_val |= ((rx_data[idx] >> (8 - remaining_bits)) << (num_bytes * 8))
                idx += 1
            rx_val |= (((rx_data[idx] >> 7) & 0x01) << (num_bits - 1))
        return rx_val

    # ==========================================
    # TAP OPERATIONS
    # ==========================================

    def scan(self, max_devices: int = 8):
        """
        Performs a blind scan of the JTAG chain.
        
        Reads up to `max_devices` to find and identify all TAPs present in the chain.

        Args:
            max_devices (int): Maximum number of devices to scan (default is 8).
        """
        if not self.is_ready(): return
        try:
            print("Scanning JTAG chain (Blind Scan)...")
            self.device.purge(ftd.defines.PURGE_RX)
            mpsse_payload = bytearray(self._tms_reset() + self._tms_to_shift_dr())
            bytes_to_read = max_devices * 4
            mpsse_payload += b'\x28' + struct.pack('<H', bytes_to_read - 1) + self._tms_to_idle() + b'\x87'              
            self.device.write(bytes(mpsse_payload))
            time.sleep(0.01)
            
            rx_data = self.device.read(bytes_to_read)
            if len(rx_data) == bytes_to_read:
                print("-" * 80)
                print(f"{'TAP':<5} | {'RAW IDCODE':<10} | {'DEVICE DESCRIPTION'}")
                print("-" * 80)
                tap_count = 0
                for i in range(max_devices):
                    idcode = struct.unpack('<I', rx_data[i*4:(i+1)*4])[0]
                    if idcode == 0xFFFFFFFF: break
                    if (idcode & 0x01) == 0: continue
                    tap_count += 1
                    device_name = KNOWN_TAPS.get(idcode & 0x0FFFFFFF, "Unknown Device")
                    print(f"{i:<5} | 0x{idcode:08X} | {device_name}")
                print("-" * 80)
                print(f"Total devices found: {tap_count}\n")
        except Exception as e:
            print(f"Error during scan: {e}")

    def shift_ir(self, instruction: int, tap_index: int):
        """
        Shifts an instruction into the target TAP while putting others in BYPASS.

        Args:
            instruction (int): The JTAG instruction to execute.
            tap_index (int): 0 for FPGA (PL), 1 for ARM (PS).
        """
        shift_value = (instruction << 6) | 0x3F if tap_index == 1 else (0x0F << 6) | instruction
        self._shift_bits(shift_value, 10, is_ir=True)

    def shift_dr(self, data_val: int, dr_len: int, tap_index: int) -> int:
        """
        Shifts data into the target TAP DR and reads the response.

        Args:
            data_val (int): The data value to shift in.
            dr_len (int): Length of the Data Register in bits.
            tap_index (int): 0 for FPGA (PL), 1 for ARM (PS).

        Returns:
            int: The read response from the target TAP.
        """
        shift_value = (data_val << 1) | 0x01 if tap_index == 1 else (0x01 << dr_len) | data_val
        rx_val = self._shift_bits(shift_value, dr_len + 1, is_ir=False)
        return (rx_val >> 1) & ((1 << dr_len) - 1) if tap_index == 1 else rx_val & ((1 << dr_len) - 1)

    def read_fpga_usercode(self):
        """Reads the USERCODE (Instruction 0x08) of the Zynq PL (FPGA)."""
        if not self.is_ready(): return
        print("\nTargeting FPGA TAP -> Reading USERCODE...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
        self.shift_ir(0x08, tap_index=0)
        print(f"FPGA USERCODE: 0x{self.shift_dr(0x00000000, 32, 0):08X}")

    # ==========================================
    # CORESIGHT DAP & AHB-AP INTERACTIONS
    # ==========================================

    def test_arm_dap(self):
        """Interrogates and initializes the ARM Debug Port (CoreSight JTAG-DP)."""
        if not self.is_ready(): return
        print("\nTargeting ARM DAP -> CoreSight Initialization...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
        
        self.shift_ir(0x0E, tap_index=1)
        print(f"ARM IDCODE     : 0x{self.shift_dr(0x00000000, 32, 1):08X}")
        
        self.shift_ir(0x0A, tap_index=1)
        self.shift_dr((0x0000001E << 3) | 0, dr_len=35, tap_index=1)
        print(f"ABORT ACK      : 0x{self.shift_dr((0x50000000 << 3) | 2, 35, 1) & 0x7:02X}")
        print(f"PWRUP ACK      : 0x{self.shift_dr((0x00000000 << 3) | 3, 35, 1) & 0x7:02X}")
        rx_val = self.shift_dr((0x00000000 << 3) | 3, 35, 1)
        print(f"CTRL/STAT ACK  : 0x{rx_val & 0x07:02X}")

    def _dap_write(self, is_ap, a32, data):
        self.shift_ir(0x0B if is_ap else 0x0A, tap_index=1)
        return self.shift_dr((data << 3) | (a32 << 1) | 0, dr_len=35, tap_index=1) & 0x07

    def _dap_read(self, is_ap, a32):
        self.shift_ir(0x0B if is_ap else 0x0A, tap_index=1)
        self.shift_dr((0 << 3) | (a32 << 1) | 1, dr_len=35, tap_index=1)
        rx_val = self.shift_dr((0 << 3) | (a32 << 1) | 1, dr_len=35, tap_index=1)
        return (rx_val >> 3) & 0xFFFFFFFF, rx_val & 0x07

    def init_ahb_ap(self):
        """Initializes the Advanced High-performance Bus Access Port (AHB-AP)."""
        self._dap_write(False, 1, 0x50000000)
        self._dap_write(False, 2, 0x00000000)
        # CSW: Size=32bit (2), AddrInc=Single (1)
        self._dap_write(True, 0, 0x23000012)

    def write_mem32(self, address: int, data: int):
        """
        Writes a single 32-bit word to the physical memory address.

        Args:
            address (int): The physical 32-bit destination address.
            data (int): The 32-bit value to write.
        """
        self._dap_write(True, 1, address)
        self._dap_write(True, 3, data)

    def read_mem32(self, address: int) -> int:
        """
        Reads a single 32-bit word from the physical memory address.

        Args:
            address (int): The physical 32-bit source address.

        Returns:
            int: The 32-bit value read from memory.
        """
        self._dap_write(True, 1, address)
        return self._dap_read(True, 3)[0]
        
    def write_mem32_bulk(self, start_address: int, words: list):
        """
        High-Speed Bulk Engine: Packs multiple APB transactions into single USB transfers.
        
        Args:
            start_address (int): The starting physical memory address.
            words (list): List of 32-bit integers to write sequentially.
        """
        self._dap_write(is_ap=True, a32=1, data=start_address)
        self.shift_ir(0x0B, tap_index=1)
        
        batch_size = 800
        for i in range(0, len(words), batch_size):
            batch = words[i:i+batch_size]
            payload = bytearray()
            for w in batch:
                req = (w << 3) | (3 << 1) | 0
                shift_val = (req << 1) | 0x01
                
                payload += self._tms_idle_to_shift_dr()
                payload += b'\x39\x03\x00' + (shift_val & 0xFFFFFFFF).to_bytes(4, 'little')
                
                rem_val = (shift_val >> 32) & 0x0F
                payload += b'\x3B\x02' + struct.pack('<B', rem_val & 0x07)
                
                tms_byte = 0x01 | (((rem_val >> 3) & 0x01) << 7)
                payload += b'\x4B\x00' + struct.pack('<B', tms_byte)
                payload += self._tms_exit_to_idle()
                
            self.device.write(bytes(payload))
            self.device.purge(ftd.defines.PURGE_RX) # Skip ACKs for maximum throughput

    # ==========================================
    # ZYNQ-SPECIFIC HARDWARE WORKFLOWS
    # ==========================================

    def test_ocm_ram(self):
        """Verifies read/write capabilities on the Zynq internal OCM memory."""
        if not self.is_ready(): return
        print("\nTargeting ARM AHB-AP -> Testing OCM Memory Access...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
        
        self.init_ahb_ap()
        magic_word = 0xDEADBEEF
        
        self.write_mem32(ZynqRegs.OCM_BASE_ADDR, magic_word)
        read_back = self.read_mem32(ZynqRegs.OCM_BASE_ADDR)
        print(f"Read Value : 0x{read_back:08X}")
        print("SUCCESS: OCM memory is accessible!" if read_back == magic_word else "ERROR: Memory write failed.")

    def run_fsbl_bin(self, filepath: str = "fsbl.bin"):
        """
        Loads and executes the First Stage Boot Loader (FSBL) into OCM.

        This method uses the high-speed bulk write to transfer the binary
        and properly controls the CPU0 reset line to start execution.

        Args:
            filepath (str): Path to the FSBL binary file (default is "fsbl.bin").
        """
        if not self.is_ready(): return
        print(f"\nTargeting ARM -> Loading '{filepath}' into OCM...")
        
        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            print(f"ERROR: File '{filepath}' not found!")
            return
            
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
        self.init_ahb_ap()

        # Unlock SLCR
        self.write_mem32(ZynqRegs.SLCR_UNLOCK_ADDR, ZynqRegs.SLCR_UNLOCK_KEY)
        
        print(" -> Halting CPU0 (Reset)...")
        current_rst = self.read_mem32(ZynqRegs.A9_CPU_RST_CTRL)
        self.write_mem32(ZynqRegs.A9_CPU_RST_CTRL, current_rst | 0x01)
        
        words = []
        for i in range(0, len(data), 4):
            chunk = data[i:i+4]
            if len(chunk) < 4: chunk += b'\x00' * (4 - len(chunk))
            words.append(struct.unpack('<I', chunk)[0])
            
        print(f" -> Executing Bulk Write of {len(data)} bytes...")
        t0 = time.time()
        self.write_mem32_bulk(ZynqRegs.OCM_BASE_ADDR, words)
        print(f" -> OCM Write completed in {time.time()-t0:.2f} seconds!")

        print(" -> Waking up CPU0...")
        self.write_mem32(ZynqRegs.A9_CPU_RST_CTRL, current_rst & ~0x01)
        
        # Lock SLCR
        self.write_mem32(ZynqRegs.SLCR_LOCK_ADDR, ZynqRegs.SLCR_LOCK_KEY)
        
        print(" -> FSBL is running! Waiting 2 seconds for hardware setup...")
        time.sleep(2)
        print("SUCCESS: Board is ready.")

    def read_qspi_jedec_id(self):
        """
        Asks the external QSPI Flash for its JEDEC ID.
        Requires the hardware to be properly initialized (e.g., via FSBL) beforehand.
        """
        if not self.is_ready(): return
        print("\nTargeting QSPI Controller -> Reading Flash JEDEC ID...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
        self.init_ahb_ap()
        
        # Disable Linear QSPI (mandatory to use FIFOs manually)
        self.write_mem32(ZynqRegs.QSPI_LQSPI_CFG, 0x00000000) 
        
        # Read the perfect baseline configuration just created by the FSBL
        base_cfg = self.read_mem32(ZynqRegs.QSPI_CONFIG)
        
        # Ensure Manual CS and Manual Start control
        # Bit 15: Manual Start Enable = 1
        # Bit 14: Manual CS Enable = 1
        # Bit 10: PCS0 (Chip Select) = 1 (De-asserted / High)
        CONFIG_IDLE = base_cfg | (1 << 15) | (1 << 14) | (1 << 10)
        
        # CS Asserted (Bit 10 = 0)
        CONFIG_CS0  = CONFIG_IDLE & ~(1 << 10)
        
        # Manual trigger (Bit 16 = 1)
        CONFIG_TRIG = CONFIG_CS0 | (1 << 16)
        
        # Load IDLE config and enable the controller
        self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_IDLE)
        self.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
        
        # Assert Chip Select
        self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_CS0) 
        
        # Write JEDEC Command (0x9F) into TX FIFO
        self.write_mem32(ZynqRegs.QSPI_TXD_FIFO, 0x0000009F)
        
        # FIRE! (Trigger transmission)
        self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_TRIG)
        
        # TX FIFO Polling
        tx_success = False
        for _ in range(100):
            if self.read_mem32(ZynqRegs.QSPI_STATUS) & (1 << 2): # TX_FIFO_Empty
                tx_success = True
                break
            time.sleep(0.01)
            
        if not tx_success:
            print("ERROR: TX FIFO Timeout. Ensure the FSBL (Option 8) has run completely!")
            self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_IDLE)
            return
            
        # De-assert Chip Select
        self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_IDLE)
        
        # Read response (First 8 bits are garbage, next 24 are ID)
        rx_val = self.read_mem32(ZynqRegs.QSPI_RXD_FIFO)
        
        manuf_id = (rx_val >> 8) & 0xFF
        mem_type = (rx_val >> 16) & 0xFF
        mem_cap  = (rx_val >> 24) & 0xFF
        
        print("-" * 40)
        print(f"JEDEC ID : {manuf_id:02X} {mem_type:02X} {mem_cap:02X}")
        print("-" * 40)
        
        if manuf_id in (0x00, 0xFF):
            print("ERROR: Invalid JEDEC ID. Flash MISO line is silent.")
        elif manuf_id == 0x9D:
            print("SUCCESS: ISSI Flash memory detected perfectly!")
        else:
            print(f"SUCCESS: Flash memory detected (Manufacturer: 0x{manuf_id:02X}).")


# --- CLI INTERFACE ---
menu_list = [
    "0. Exit", "1. List FTDI devices", "2. Open JTAG", "3. Close JTAG",
    "4. Scan JTAG", "5. Read FPGA USERCODE", "6. Test ARM DAP", 
    "7. Test OCM RAM", "8. Load & Run fsbl.bin", "9. Read QSPI JEDEC ID", "?. Help"
]

def show_menu():
    print("\n" + "-" * 40)
    for item in menu_list: 
        print(item)
    print("-" * 40 + "\n")

def main_loop(jtag):
    choice = input("> ")
    match choice:
        case "0": return False
        case "1": jtag.list_ftdi_devices()
        case "2": 
            # Open JTAG at 15 MHz to unleash Bulk Write performance
            jtag.open(0, freq_hz=15_000_000)
        case "3": jtag.close()
        case "4": jtag.scan()
        case "5": jtag.read_fpga_usercode()
        case "6": jtag.test_arm_dap()
        case "7": jtag.test_ocm_ram()
        case "8": jtag.run_fsbl_bin()
        case "9": jtag.read_qspi_jedec_id()
        case "?": show_menu()
        case _: pass
    return True

if __name__ == '__main__':
    jtag = JtagController()
    show_menu()
    while main_loop(jtag): 
        pass
    print("Exiting...")
    jtag.close()