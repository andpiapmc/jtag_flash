"""
JTAG Management Tool for Xilinx Zynq-7000 Series.
Provides low-level JTAG interactions, CoreSight Debug Access Port (DAP) debugging,
On-Chip Memory (OCM) access, and QSPI Flash manipulation via FTDI MPSSE.
Uses the official 'ftd2xx' backend for native Windows driver compatibility.
"""

import ftd2xx as ftd
import time
import struct


# =========================================================================
# HARDWARE DEFINITIONS & CONSTANTS
# =========================================================================

class MpsseOpcodes:
    """FTDI MPSSE Command Opcodes."""
    DISABLE_CLK_DIV5      = b'\x8A'
    TURN_OFF_ADAPTIVE_CLK = b'\x97'
    DISABLE_3_PHASE_CLK   = b'\x8D'
    SET_DATA_BITS_LOW     = b'\x80'
    SET_DATA_BITS_HIGH    = b'\x82'
    SET_TCK_DIVISOR       = b'\x86'
    
    # Data Shifting Opcodes
    READ_DATA_BYTES_LSB   = b'\x28'
    SHIFT_BYTES_LSB_RW    = b'\x39'
    SHIFT_BITS_LSB_RW     = b'\x3B'
    SHIFT_TMS_NO_READ     = b'\x4B'
    SHIFT_TMS_READ        = b'\x6B'
    SEND_IMMEDIATE        = b'\x87'

class JtagInstr:
    """JTAG Instruction Register (IR) Values."""
    FPGA_USERCODE = 0x08
    DAP_DPACC     = 0x0A  # Debug Port Access
    DAP_APACC     = 0x0B  # Access Port Access
    DAP_IDCODE    = 0x0E  # ARM CoreSight ID

class CoreSightRegs:
    """ARM CoreSight DP/AP Register Addresses (A[3:2] indices)."""
    # Debug Port (DP) Registers
    DP_ABORT      = 0x0
    DP_CTRL_STAT  = 0x1
    DP_SELECT     = 0x2
    DP_RDBUFF     = 0x3
    # Access Port (AP) Registers (specifically AHB-AP)
    AP_CSW        = 0x0  # Control/Status Word
    AP_TAR        = 0x1  # Transfer Address Register
    AP_DRW        = 0x3  # Data Read/Write Register

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
    
    # On-Chip Memory
    OCM_BASE_ADDR    = 0x00000000

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

# Dictionary of common SPI Flash Manufacturer IDs (JEP106)
FLASH_MANUFACTURERS = {
    0x01: "Spansion / Cypress (Infineon)",
    0x1C: "EON Silicon Devices",
    0x1F: "Adesto / Dialog Semiconductor",
    0x20: "Micron / ST (Numonyx)",
    0x85: "Puya Semiconductor",
    0x9D: "ISSI (Integrated Silicon Solution Inc.)",
    0xBF: "SST / Microchip",
    0xC2: "Macronix (MXIC)",
    0xC8: "GigaDevice",
    0xEF: "Winbond"
}

# Dictionary for Memory Types based on Manufacturer ID
FLASH_MEMORY_TYPES = {
    0x9D: { 0x40: "IS25LQ (3.0V Quad)", 0x60: "IS25LP (3.0V Quad)", 0x70: "IS25WP (1.8V Quad)" },
    0xEF: { 0x30: "W25X", 0x40: "W25Q (SPI)", 0x60: "W25Q (QPI)" },
    0xC2: { 0x20: "MX25L (3.0V)", 0x25: "MX25U (1.8V)", 0x28: "MX25R (Ultra Low Power)" },
    0x20: { 0x20: "M25P", 0xBA: "N25Q / MT25QL (3.0V)", 0xBB: "MT25QU (1.8V)" },
    0x01: { 0x02: "S25FL-A/K (3.0V)", 0x20: "S25FL-S (3.0V)" },
    0xC8: { 0x40: "GD25Q (3.0V)", 0x60: "GD25LQ (1.8V)" }
}


# =========================================================================
# JTAG CONTROLLER CLASS
# =========================================================================

class JtagController:
    """
    Class to encapsulate and manage the JTAG interface via FTDI MPSSE using ftd2xx.
    """
    
    def __init__(self):
        self.device = None  # FTDI device handle (ftd2xx object) for JTAG communication

    # ==========================================
    # FTDI DEVICE MANAGEMENT
    # ==========================================

    @staticmethod
    def list_ftdi_devices():
        """
        Scans and lists all connected FTDI devices using the official drivers.
        Prints the device index, description, and serial number.
        """
        print("Scanning for FTDI devices via ftd2xx...")
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
                print(f"{i}: {desc} (Serial: {serial})")
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
            self.device.setBitMode(0x0B, 2)  # Enable MPSSE
            time.sleep(0.05)
            
            # Massive buffers for extreme speed during bulk writes
            self.device.setUSBParameters(65536, 65536) 
            self.device.setChars(0, False, 0, False)
            self.device.setTimeouts(1000, 1000)
            self.device.setLatencyTimer(16)
            self.device.purge(ftd.defines.PURGE_RX | ftd.defines.PURGE_TX)
            
            # MPSSE Engine Configuration Setup
            setup_cmds = bytearray()
            setup_cmds += MpsseOpcodes.DISABLE_CLK_DIV5
            setup_cmds += MpsseOpcodes.TURN_OFF_ADAPTIVE_CLK
            setup_cmds += MpsseOpcodes.DISABLE_3_PHASE_CLK
            setup_cmds += MpsseOpcodes.SET_DATA_BITS_LOW + b'\x88\xFB'  # Value/Dir for ADBUS
            setup_cmds += MpsseOpcodes.SET_DATA_BITS_HIGH + b'\x00\x00' # Value/Dir for ACBUS
            
            # TCK frequency divisor calculation
            divisor = max(0, min(65535, int((30_000_000 / freq_hz) - 1)))
            setup_cmds += MpsseOpcodes.SET_TCK_DIVISOR + struct.pack('<H', divisor)
            self.device.write(bytes(setup_cmds))
            
            # Target power check via blind TLR and read
            self.device.purge(ftd.defines.PURGE_RX)
            self.device.write(self._tms_reset() + self._tms_to_shift_dr())
            self.device.write(MpsseOpcodes.READ_DATA_BYTES_LSB + b'\x03\x00' + self._tms_to_idle() + MpsseOpcodes.SEND_IMMEDIATE)
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
                self.device.write(MpsseOpcodes.SET_DATA_BITS_LOW + b'\x00\x00') # Reset lines
                self.device.close()
                print("FTDI connection closed.")
            except Exception as e:
                print(f"Error during close: {e}")
            finally:
                self.device = None
        else:
            print("JTAG is not open.")

    # =========================================================================
    # JTAG STATE MACHINE LOW-LEVEL (TMS CONTROL)
    # =========================================================================
    
    def _build_tms_cmd(self, tms_sequence: int, bit_length: int) -> bytes:
        """
        Constructs the FTDI MPSSE command to shift bits out on the TMS pin.
        
        Args:
            tms_sequence (int): The sequence of 1s and 0s to send (LSB first).
            bit_length (int): The number of bits to shift (1 to 8).
            
        Returns:
            bytes: The formatted 3-byte MPSSE command.
        """
        # FTDI protocol requires length to be (actual_length - 1)
        # Directly concatenate the byte opcode with the packed payload
        return MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', bit_length - 1, tms_sequence)

    def _tms_reset(self): 
        """
        Forces the TAP controller into the 'Test-Logic-Reset' (TLR) state.
        JTAG requires at least 5 consecutive '1's on TMS to reset from any state.
        We send 32 consecutive '1's (8 bits * 4) to absolutely guarantee a full chain reset.
        """
        return self._build_tms_cmd(0xFF, 8) * 4

    def _tms_to_shift_dr(self): 
        """
        Navigates from 'Test-Logic-Reset' to 'Shift-DR'.
        Path: TLR(0) -> Idle(1) -> Select-DR(0) -> Capture-DR(0) -> Shift-DR
        Sequence: 0010 (LSB first) -> 0x02
        """
        return self._build_tms_cmd(0x02, 4)

    def _tms_to_idle(self): 
        """
        Navigates from 'Shift-DR' or 'Shift-IR' back to 'Run-Test/Idle'.
        Path: Shift(1) -> Exit1(1) -> Update(0) -> Idle
        Sequence: 011 (LSB first) -> 0x03
        """
        return self._build_tms_cmd(0x03, 3)

    def _tms_exit_to_idle(self): 
        """
        Navigates from 'Exit1-DR' or 'Exit1-IR' back to 'Run-Test/Idle'.
        Path: Exit1(1) -> Update(0) -> Idle
        Sequence: 01 (LSB first) -> 0x01
        """
        return self._build_tms_cmd(0x01, 2)

    def _tms_tlr_to_idle(self): 
        """
        Navigates from 'Test-Logic-Reset' to 'Run-Test/Idle'.
        Path: TLR(0) -> Idle
        Sequence: 0 (LSB first) -> 0x00
        """
        return self._build_tms_cmd(0x00, 1)

    def _tms_idle_to_shift_ir(self): 
        """
        Navigates from 'Run-Test/Idle' to 'Shift-IR'.
        Path: Idle(1) -> Select-DR(1) -> Select-IR(0) -> Capture-IR(0) -> Shift-IR
        Sequence: 0011 (LSB first) -> 0x03
        """
        return self._build_tms_cmd(0x03, 4)

    def _tms_idle_to_shift_dr(self): 
        """
        Navigates from 'Run-Test/Idle' to 'Shift-DR'.
        Path: Idle(1) -> Select-DR(0) -> Capture-DR(0) -> Shift-DR
        Sequence: 001 (LSB first) -> 0x01
        """
        return self._build_tms_cmd(0x01, 3)

    def _shift_bits(self, data_val: int, num_bits: int, is_ir: bool = False):
        """Core full-duplex MPSSE engine. Writes and reads bits simultaneously."""
        payload = bytearray(self._tms_idle_to_shift_ir() if is_ir else self._tms_idle_to_shift_dr())
        num_bytes, remaining_bits = (num_bits - 1) // 8, (num_bits - 1) % 8
        last_bit = (data_val >> (num_bits - 1)) & 0x01
        
        # Shift out full bytes
        if num_bytes > 0:
            payload += MpsseOpcodes.SHIFT_BYTES_LSB_RW + struct.pack('<H', num_bytes - 1) 
            payload += (data_val & ((1 << (num_bytes * 8)) - 1)).to_bytes(num_bytes, 'little')
        
        # Shift out remaining bits (excluding the very last bit)
        if remaining_bits > 0:
            payload += MpsseOpcodes.SHIFT_BITS_LSB_RW + struct.pack('<B', remaining_bits - 1) 
            payload += struct.pack('<B', (data_val >> (num_bytes * 8)) & 0xFF)
        
        # Shift out the final bit simultaneously with the TMS exit transition
        tms_byte = 0x01 | (last_bit << 7)
        payload += MpsseOpcodes.SHIFT_TMS_READ + b'\x00' + struct.pack('<B', tms_byte)
        
        # Conclude and flush
        payload += self._tms_exit_to_idle() + MpsseOpcodes.SEND_IMMEDIATE
        self.device.write(bytes(payload))
        
        expected_rx_len = num_bytes + (1 if remaining_bits > 0 else 0) + 1
        rx_data = self.device.read(expected_rx_len)
        rx_val = 0
        
        # Assemble received data
        if len(rx_data) == expected_rx_len:
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

    def scan(self, max_devices: int = 8):
        """
        Performs a blind scan of the JTAG chain.
        Reads up to `max_devices` to find and identify all TAPs present in the chain.
        """
        if not self.is_ready(): 
            print("JTAG is not open. Please open a connection first.")
            return
        try:
            print("Scanning JTAG chain (Blind Scan)...")
            self.device.purge(ftd.defines.PURGE_RX)
            mpsse_payload = bytearray(self._tms_reset() + self._tms_to_shift_dr())
            bytes_to_read = max_devices * 4
            mpsse_payload += MpsseOpcodes.READ_DATA_BYTES_LSB + struct.pack('<H', bytes_to_read - 1)
            mpsse_payload += self._tms_to_idle() + MpsseOpcodes.SEND_IMMEDIATE
            self.device.write(bytes(mpsse_payload))
            time.sleep(0.01)
            
            rx_data = self.device.read(bytes_to_read)
            if len(rx_data) == bytes_to_read:
                print("-" * 70)
                print(f"{'TAP':<5} | {'RAW IDCODE':<10} | {'DEVICE DESCRIPTION'}")
                print("-" * 70)
                tap_count = 0
                for i in range(max_devices):
                    idcode = struct.unpack('<I', rx_data[i*4:(i+1)*4])[0]
                    if idcode == 0xFFFFFFFF: break
                    if (idcode & 0x01) == 0: continue
                    tap_count += 1
                    device_name = KNOWN_TAPS.get(idcode & 0x0FFFFFFF, "Unknown Device")
                    print(f"{i:<5} | 0x{idcode:08X} | {device_name}")
                print("-" * 70)
                print(f"Total devices found: {tap_count}")
        except Exception as e:
            print(f"Error during scan: {e}")
            
    def read_fpga_usercode(self):
        """Reads the USERCODE of the Zynq PL (FPGA)."""
        if not self.is_ready():
            print("JTAG is not open. Please open a connection first.")
            return
        print("Targeting FPGA TAP -> Reading USERCODE...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
        self.shift_ir(JtagInstr.FPGA_USERCODE, tap_index=0)
        print(f"FPGA USERCODE: 0x{self.shift_dr(0x00000000, 32, 0):08X}")

    # ========================================================
    # CORESIGHT DAP (Debug Access Port) & AHB-AP INTERACTIONS
    # ========================================================

    def test_arm_dap(self):
        """Interrogates and initializes the ARM Debug Port (CoreSight JTAG-DP)."""
        if not self.is_ready(): 
            print("JTAG is not open. Please open a connection first.")
            return
            
        print("Targeting ARM DAP -> CoreSight Initialization...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
        
        # Dictionary to decode ARM JTAG-DP protocol ACKs
        ack_labels = {
            0x01: "WAIT",
            0x02: "OK",
            0x04: "FAULT"
        }
        
        self.shift_ir(JtagInstr.DAP_IDCODE, tap_index=1)
        print(f"ARM IDCODE     : 0x{self.shift_dr(0x00000000, 32, 1):08X}")
        
        self.shift_ir(JtagInstr.DAP_DPACC, tap_index=1)
        
        # TX 1: Issue ABORT command to clear sticky errors (Write to DP_ABORT)
        req_abort = (0x0000001E << 3) | (CoreSightRegs.DP_ABORT << 1) | 0
        self.shift_dr(req_abort, dr_len=35, tap_index=1)
        
        # TX 2: Request PWRUP (Write to DP_CTRL_STAT) and read previous ACK
        req_pwrup = (0x50000000 << 3) | (CoreSightRegs.DP_CTRL_STAT << 1) | 0
        ack_abort = self.shift_dr(req_pwrup, 35, 1) & 0x7
        print(f"ABORT ACK      : 0x{ack_abort:02X} [{ack_labels.get(ack_abort, 'INVALID/NO-ACK')}]")
        
        # TX 3: Read CTRL/STAT status and read the ACK from the PWRUP command
        req_status = (0x00000000 << 3) | (CoreSightRegs.DP_CTRL_STAT << 1) | 1
        ack_pwrup = self.shift_dr(req_status, 35, 1) & 0x7
        print(f"PWRUP ACK      : 0x{ack_pwrup:02X} [{ack_labels.get(ack_pwrup, 'INVALID/NO-ACK')}]")
        
        # TX 4: Dummy read to flush the data out and read the final ACK
        rx_val = self.shift_dr(req_status, 35, 1)
        ack_ctrl = rx_val & 0x07
        
        print(f"CTRL/STAT ACK  : 0x{ack_ctrl:02X} [{ack_labels.get(ack_ctrl, 'INVALID/NO-ACK')}]")

    def _dap_write(self, is_ap: bool, a32: int, data: int):
        """Writes a 32-bit word to a DAP register (DP or AP)."""
        ir = JtagInstr.DAP_APACC if is_ap else JtagInstr.DAP_DPACC
        self.shift_ir(ir, tap_index=1)
        req = (data << 3) | (a32 << 1) | 0
        return self.shift_dr(req, dr_len=35, tap_index=1) & 0x07

    def _dap_read(self, is_ap: bool, a32: int):
        """Reads a 32-bit word from a DAP register (DP or AP)."""
        ir = JtagInstr.DAP_APACC if is_ap else JtagInstr.DAP_DPACC
        self.shift_ir(ir, tap_index=1)
        req = (0 << 3) | (a32 << 1) | 1
        self.shift_dr(req, dr_len=35, tap_index=1)
        rx_val = self.shift_dr(req, dr_len=35, tap_index=1)
        return (rx_val >> 3) & 0xFFFFFFFF, rx_val & 0x07

    def init_ahb_ap(self):
        """Initializes the Advanced High-performance Bus Access Port (AHB-AP)."""
        self._dap_write(is_ap=False, a32=CoreSightRegs.DP_CTRL_STAT, data=0x50000000)
        self._dap_write(is_ap=False, a32=CoreSightRegs.DP_SELECT,    data=0x00000000)
        # CSW: Size=32bit (2), AddrInc=Single (1)
        self._dap_write(is_ap=True,  a32=CoreSightRegs.AP_CSW,       data=0x23000012)

    def write_mem32(self, address: int, data: int):
        """Writes a single 32-bit word to the physical memory address."""
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=address)
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_DRW, data=data)

    def read_mem32(self, address: int) -> int:
        """Reads a single 32-bit word from the physical memory address."""
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=address)
        return self._dap_read(is_ap=True, a32=CoreSightRegs.AP_DRW)[0]
        
    def write_mem32_bulk(self, start_address: int, words: list):
        """High-Speed Bulk Engine: Packs multiple APB transactions into single USB transfers."""
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=start_address)
        self.shift_ir(JtagInstr.DAP_APACC, tap_index=1)
        
        batch_size = 800
        for i in range(0, len(words), batch_size):
            batch = words[i:i+batch_size]
            payload = bytearray()
            for w in batch:
                req = (w << 3) | (CoreSightRegs.AP_DRW << 1) | 0
                shift_val = (req << 1) | 0x01
                
                payload += self._tms_idle_to_shift_dr()
                payload += MpsseOpcodes.SHIFT_BYTES_LSB_RW + b'\x03\x00' + (shift_val & 0xFFFFFFFF).to_bytes(4, 'little')
                
                rem_val = (shift_val >> 32) & 0x0F
                payload += MpsseOpcodes.SHIFT_BITS_LSB_RW + b'\x02' + struct.pack('<B', rem_val & 0x07)
                
                tms_byte = 0x01 | (((rem_val >> 3) & 0x01) << 7)
                payload += MpsseOpcodes.SHIFT_TMS_NO_READ + b'\x00' + struct.pack('<B', tms_byte)
                payload += self._tms_exit_to_idle()
                
            self.device.write(bytes(payload))
            self.device.purge(ftd.defines.PURGE_RX) # Skip ACKs for maximum throughput

    # ==========================================
    # ZYNQ-SPECIFIC HARDWARE WORKFLOWS
    # ==========================================

    def test_ocm_ram(self):
        """Verifies read/write capabilities on the Zynq internal OCM memory."""
        if not self.is_ready(): 
            print("JTAG is not open. Please open a connection first.")
            return
        print("Targeting ARM AHB-AP -> Testing OCM Memory Access...")
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
        """
        if not self.is_ready(): 
            print("JTAG is not open. Please open a connection first.")
            return
        print(f"Targeting ARM -> Loading '{filepath}' into OCM...")
        
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
        if not self.is_ready(): 
            print("JTAG is not open. Please open a connection first.")
            return
            
        print("Targeting QSPI Controller -> Reading Flash JEDEC ID...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(self._tms_reset() + self._tms_tlr_to_idle())
        self.init_ahb_ap()
        
        # Disable Linear QSPI (mandatory to use FIFOs manually)
        self.write_mem32(ZynqRegs.QSPI_LQSPI_CFG, 0x00000000) 
        
        # Read the perfect baseline configuration just created by the FSBL
        base_cfg = self.read_mem32(ZynqRegs.QSPI_CONFIG)
        
        # Ensure Manual CS and Manual Start control
        CONFIG_IDLE = base_cfg | (1 << 15) | (1 << 14) | (1 << 10)
        CONFIG_CS0  = CONFIG_IDLE & ~(1 << 10)
        CONFIG_TRIG = CONFIG_CS0 | (1 << 16)
        
        # Load IDLE config and enable the controller
        self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_IDLE)
        self.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
        
        # Assert Chip Select and Write JEDEC Command (0x9F) into TX FIFO
        self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_CS0) 
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
        
        # Read response
        rx_val = self.read_mem32(ZynqRegs.QSPI_RXD_FIFO)
        manuf_id = (rx_val >> 8) & 0xFF
        mem_type = (rx_val >> 16) & 0xFF
        mem_cap  = (rx_val >> 24) & 0xFF
        
        print("-" * 50)
        print(f"RAW JEDEC ID : {manuf_id:02X} {mem_type:02X} {mem_cap:02X}")
        print("-" * 50)
        
        if manuf_id in (0x00, 0xFF):
            print("ERROR: Invalid JEDEC ID. Flash MISO line is silent or shorted.")
            return
        
        manuf_name = FLASH_MANUFACTURERS.get(manuf_id, "Unknown Manufacturer")
        mem_type_name = FLASH_MEMORY_TYPES.get(manuf_id, {}).get(mem_type, "Unknown Type")

        try:
            capacity_bytes = 1 << mem_cap
            if capacity_bytes >= (1024 * 1024):
                cap_str = f"{capacity_bytes // (1024 * 1024)} MB"
            else:
                cap_str = f"{capacity_bytes // 1024} KB"
        except Exception:
            cap_str = "Unknown Capacity"
            
        print("SUCCESS: Flash memory detected!")
        print(f" -> Manufacturer : {manuf_name} (0x{manuf_id:02X})")
        print(f" -> Memory Type  : {mem_type_name} (0x{mem_type:02X})")
        print(f" -> Capacity     : {cap_str} (0x{mem_cap:02X})")


# --- CLI INTERFACE ---
def show_menu():
    menu_list = [
        "0. Exit",
        "1. List FTDI devices", 
        "2. Open JTAG", 
        "3. Close JTAG",
        "4. Scan JTAG", 
        "5. Read FPGA USERCODE", 
        "6. Test ARM DAP", 
        "7. Test OCM RAM", 
        "8. Load & Run fsbl.bin", 
        "9. Read QSPI JEDEC ID", 
        "?. Help"
    ]
    print("\n" + "-" * 40)
    for item in menu_list: 
        print(item)
    print("-" * 40 + "\n")

def main_loop(jtag):
    choice = input("> ")
    match choice:
        case "0": return False
        case "1": jtag.list_ftdi_devices()
        case "2": jtag.open(0, freq_hz=15_000_000)
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