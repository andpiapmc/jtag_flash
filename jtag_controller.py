"""
JTAG Controller Engine.
Manages low-level MPSSE sequencing, CoreSight transactions, memory injections,
and hardware handshakes for Xilinx Zynq platforms.
"""

import ftd2xx as ftd
import time
import struct
from zynq_constants import (
    MpsseOpcodes, JtagInstr, CoreSightRegs, ZynqRegs, TmsCommands,
    KNOWN_TAPS, FLASH_MANUFACTURERS, FLASH_MEMORY_TYPES,
    DapReq, AhbApRegs, QspiConfig
)

class JtagController:
    """
    Encapsulates the JTAG interface via FTDI MPSSE using ftd2xx backend.
    """
    
    def __init__(self):
        self.device = None  # Active FTDI device handle

    # =========================================================================
    # 1. FTDI NATIVE INTERFACE & INITIALIZATION (PUBLIC)
    # =========================================================================

    @staticmethod
    def list_ftdi_devices():
        """
        Scans and lists all connected FTDI devices using official D2XX drivers.
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

    def is_ready(self) -> bool:
        """
        Validates if the FTDI link is open and responsive.
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
        Opens and configures the FTDI MPSSE engine to interface with the JTAG chain.
        """
        if self.is_ready():
            print("JTAG is already open.")
            return
        
        print("Initializing JTAG...")
        try:
            self.device = ftd.open(device_index)
            self.device.setBitMode(0x00, 0)
            time.sleep(0.05)
            self.device.setBitMode(0x0B, 2)  # Active MPSSE Mode
            time.sleep(0.05)
            
            self.device.setUSBParameters(65536, 65536) 
            self.device.setChars(0, False, 0, False)
            self.device.setTimeouts(1000, 1000)
            self.device.setLatencyTimer(16)
            self.device.purge(ftd.defines.PURGE_RX | ftd.defines.PURGE_TX)
            
            # Setup initial static lines and hardware divisor
            setup_cmds = bytearray()
            setup_cmds += MpsseOpcodes.DISABLE_CLK_DIV5
            setup_cmds += MpsseOpcodes.TURN_OFF_ADAPTIVE_CLK
            setup_cmds += MpsseOpcodes.DISABLE_3_PHASE_CLK
            setup_cmds += MpsseOpcodes.SET_DATA_BITS_LOW + b'\x88\xFB'  
            setup_cmds += MpsseOpcodes.SET_DATA_BITS_HIGH + b'\x00\x00' 
            
            divisor = max(0, min(65535, int((30_000_000 / freq_hz) - 1)))
            setup_cmds += MpsseOpcodes.SET_TCK_DIVISOR + struct.pack('<H', divisor)
            self.device.write(bytes(setup_cmds))
            
            # Hardware power-on test
            self.device.purge(ftd.defines.PURGE_RX)
            self.device.write(TmsCommands.RESET + TmsCommands.TO_SHIFT_DR)
            self.device.write(MpsseOpcodes.READ_DATA_BYTES_LSB + b'\x03\x00' + TmsCommands.TO_IDLE + MpsseOpcodes.SEND_IMMEDIATE)
            time.sleep(0.01)
            
            rx_data = self.device.read(4)
            if len(rx_data) == 4:
                test_val = struct.unpack('<I', rx_data)[0]
                if test_val in (0xFFFFFFFF, 0x00000000):
                    print(f"WARNING: FTDI opened, but JTAG chain is DEAD (Read: 0x{test_val:08X}).")
                    self.close()
                    return
            else:
                print("WARNING: Target might be powered off.")
                self.close()
                return
                
            print(f"FTDI connection opened. TCK set to ~{freq_hz/1e6:.1f} MHz.")
        except Exception as e:
            print(f"Error initializing FTDI: {e}")
            self.device = None

    def close(self):
        """Resets pin configurations and safely terminates the connection."""
        if self.is_ready():
            try:
                self.device.write(MpsseOpcodes.SET_DATA_BITS_LOW + b'\x00\x00') 
                self.device.close()
                print("FTDI connection closed.")
            except Exception as e:
                print(f"Error during close: {e}")
            finally:
                self.device = None
        else:
            print("JTAG is not open.")

    # =========================================================================
    # 2. HIGH-LEVEL ZYNQ & HARDWARE WORKFLOWS (PUBLIC)
    # =========================================================================

    def run_fsbl_bin(self, filepath: str = "fsbl.bin"):
        """
        Halts Core0, injects the First Stage Boot Loader (FSBL) into OCM via bulk transfers, 
        and wakes up the core to initialize execution.
        """
        if not self.is_ready(): 
            print("JTAG is not open. Please open a connection first.")
            return
        print(f"Targeting ARM AHB-AP -> Loading '{filepath}' into OCM...")
        
        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            print(f"ERROR: File '{filepath}' not found!")
            return
            
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(TmsCommands.RESET + TmsCommands.TLR_TO_IDLE)
        self._init_ahb_ap()

        # Unlock SLCR to take control of clocking/reset lines
        self.write_mem32(ZynqRegs.SLCR_UNLOCK_ADDR, ZynqRegs.SLCR_UNLOCK_KEY)
        
        print(" -> Halting CPU0 (Reset)...")
        current_rst = self.read_mem32(ZynqRegs.A9_CPU_RST_CTRL)
        self.write_mem32(ZynqRegs.A9_CPU_RST_CTRL, current_rst | 0x01)
        
        words = []
        for i in range(0, len(data), 4):
            chunk = data[i:i+4]
            if len(chunk) < 4: 
                chunk += b'\x00' * (4 - len(chunk))
            words.append(struct.unpack('<I', chunk)[0])
            
        print(f" -> Executing Bulk Write of {len(data)} bytes...")
        t0 = time.time()
        self._write_mem32_bulk(ZynqRegs.OCM_BASE_ADDR, words)
        print(f" -> OCM Write completed in {time.time()-t0:.2f} seconds!")

        print(" -> Waking up CPU0...")
        self.write_mem32(ZynqRegs.A9_CPU_RST_CTRL, current_rst & ~0x01)
        self.write_mem32(ZynqRegs.SLCR_LOCK_ADDR, ZynqRegs.SLCR_LOCK_KEY)
        
        print(" -> FSBL is running! Waiting 2 seconds for hardware setup...")
        time.sleep(2)
        print("SUCCESS: Board is ready.")

    def read_qspi_jedec_id(self):
        """
        Takes control of the QSPI peripheral and fetches the hardware JEDEC ID 
        from the SPI Flash memory chip. Requires an active FSBL execution beforehand.
        """
        if not self.is_ready(): 
            print("JTAG is not open. Please open a connection first.")
            return
            
        print("Targeting ARM AHB-AP -> QSPI Controller -> Reading Flash JEDEC ID...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(TmsCommands.RESET + TmsCommands.TLR_TO_IDLE)
        self._init_ahb_ap()
        
        # Disable Linear QSPI to switch to manual register/FIFO mapping
        self.write_mem32(ZynqRegs.QSPI_LQSPI_CFG, 0x00000000) 
        base_cfg = self.read_mem32(ZynqRegs.QSPI_CONFIG)
        
        # Build manual toggle control words using named flags
        CONFIG_IDLE = base_cfg | QspiConfig.MANUAL_START_EN | QspiConfig.MANUAL_CS_EN | QspiConfig.PCS0_HIGH
        CONFIG_CS0  = CONFIG_IDLE & ~QspiConfig.PCS0_HIGH
        CONFIG_TRIG = CONFIG_CS0 | QspiConfig.MANUAL_START
        
        self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_IDLE)
        self.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001) # Enable QSPI peripheral
        
        # Assert Chip Select, write JEDEC command and trigger manual flash SPI transaction
        self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_CS0) 
        self.write_mem32(ZynqRegs.QSPI_TXD_FIFO, QspiConfig.JEDEC_CMD)
        self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_TRIG)
        
        tx_success = False
        for _ in range(100):
            if self.read_mem32(ZynqRegs.QSPI_STATUS) & (1 << 2): 
                tx_success = True
                break
            time.sleep(0.01)
            
        if not tx_success:
            print("ERROR: TX FIFO Timeout. Ensure the FSBL (Option 8) has run completely!")
            self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_IDLE)
            return
            
        self.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_IDLE)
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
            cap_str = f"{capacity_bytes // (1024 * 1024)} MB" if capacity_bytes >= (1024 * 1024) else f"{capacity_bytes // 1024} KB"
        except Exception:
            cap_str = "Unknown Capacity"
            
        print("SUCCESS: Flash memory detected!")
        print(f" -> Manufacturer : {manuf_name} (0x{manuf_id:02X})")
        print(f" -> Memory Type  : {mem_type_name} (0x{mem_type:02X})")
        print(f" -> Capacity     : {cap_str} (0x{mem_cap:02X})")

    def test_ocm_ram(self):
        """Verifies read/write access directly into the raw On-Chip RAM space."""
        if not self.is_ready(): 
            print("JTAG is not open. Please open a connection first.")
            return
        print("Targeting ARM AHB-AP -> Testing OCM Memory Access...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(TmsCommands.RESET + TmsCommands.TLR_TO_IDLE)
        
        self._init_ahb_ap()
        magic_word = 0xDEADBEEF
        
        self.write_mem32(ZynqRegs.OCM_BASE_ADDR, magic_word)
        read_back = self.read_mem32(ZynqRegs.OCM_BASE_ADDR)
        print(f"Read Value : 0x{read_back:08X}")
        print("SUCCESS: OCM memory is accessible!" if read_back == magic_word else "ERROR: Memory write failed.")

    def read_fpga_usercode(self):
        """Queries the programmable logic (PL) boundary TAP for its USERCODE register."""
        if not self.is_ready():
            print("JTAG is not open. Please open a connection first.")
            return
        print("Targeting FPGA TAP -> Reading USERCODE...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(TmsCommands.RESET + TmsCommands.TLR_TO_IDLE)
        self._shift_ir(JtagInstr.FPGA_USERCODE, tap_index=0)
        print(f"FPGA USERCODE: 0x{self._shift_dr(0x00000000, 32, 0):08X}")

    # =========================================================================
    # 3. INTERMEDIATE MEMORY BUS & CORESIGHT OPERATIONS (PUBLIC)
    # =========================================================================

    def test_arm_dap(self):
        """Initializes and asserts line handshakes with the ARM Debug Access Port."""
        if not self.is_ready(): 
            print("JTAG is not open. Please open a connection first.")
            return
            
        print("Targeting ARM DAP -> CoreSight Initialization...")
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(TmsCommands.RESET + TmsCommands.TLR_TO_IDLE)
        
        ack_labels = {0x01: "WAIT", 0x02: "OK", 0x04: "FAULT"}
        
        self._shift_ir(JtagInstr.DAP_IDCODE, tap_index=1)
        print(f"ARM IDCODE     : 0x{self._shift_dr(0x00000000, 32, 1):08X}")
        
        self._shift_ir(JtagInstr.DAP_DPACC, tap_index=1)
        
        # Clear sticky errors
        req_abort = (DapReq.CLEAR_ERR << 3) | (CoreSightRegs.DP_ABORT << 1) | DapReq.WRITE
        self._shift_dr(req_abort, dr_len=DapReq.SHIFT_LEN, tap_index=1)
        
        # Power up debug domains
        req_pwrup = (DapReq.PWRUP_REQ << 3) | (CoreSightRegs.DP_CTRL_STAT << 1) | DapReq.WRITE
        ack_abort = self._shift_dr(req_pwrup, dr_len=DapReq.SHIFT_LEN, tap_index=1) & DapReq.ACK_MASK
        print(f"ABORT ACK      : 0x{ack_abort:02X} [{ack_labels.get(ack_abort, 'INVALID/NO-ACK')}]")
        
        # Read back status
        req_status = (0x00000000 << 3) | (CoreSightRegs.DP_CTRL_STAT << 1) | DapReq.READ
        ack_pwrup = self._shift_dr(req_status, dr_len=DapReq.SHIFT_LEN, tap_index=1) & DapReq.ACK_MASK
        print(f"PWRUP ACK      : 0x{ack_pwrup:02X} [{ack_labels.get(ack_pwrup, 'INVALID/NO-ACK')}]")
        
        # Extract status data
        rx_val = self._shift_dr(req_status, dr_len=DapReq.SHIFT_LEN, tap_index=1)
        ack_ctrl = rx_val & DapReq.ACK_MASK
        print(f"CTRL/STAT ACK  : 0x{ack_ctrl:02X} [{ack_labels.get(ack_ctrl, 'INVALID/NO-ACK')}]")

    def _init_ahb_ap(self):
        self._dap_write(is_ap=False, a32=CoreSightRegs.DP_CTRL_STAT, data=DapReq.PWRUP_REQ)
        self._dap_write(is_ap=False, a32=CoreSightRegs.DP_SELECT,    data=0x00000000)
        self._dap_write(is_ap=True,  a32=CoreSightRegs.AP_CSW,       data=AhbApRegs.CSW_DEFAULT_32BIT)

    def write_mem32(self, address: int, data: int):
        """Writes a discrete 32-bit word directly to an absolute physical address via AHB-AP."""
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=address)
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_DRW, data=data)

    def read_mem32(self, address: int) -> int:
        """Reads a discrete 32-bit word directly from an absolute physical address via AHB-AP."""
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=address)
        return self._dap_read(is_ap=True, a32=CoreSightRegs.AP_DRW)[0]

    def scan(self, max_devices: int = 8):
        """
        Executes a JTAG blind chain discovery. Pops and logs active TAPs.
        """
        if not self.is_ready(): 
            print("JTAG is not open. Please open a connection first.")
            return
        try:
            print("Scanning JTAG chain (Blind Scan)...")
            self.device.purge(ftd.defines.PURGE_RX)
            mpsse_payload = bytearray(TmsCommands.RESET + TmsCommands.TO_SHIFT_DR)
            bytes_to_read = max_devices * 4
            mpsse_payload += MpsseOpcodes.READ_DATA_BYTES_LSB + struct.pack('<H', bytes_to_read - 1)
            mpsse_payload += TmsCommands.TO_IDLE + MpsseOpcodes.SEND_IMMEDIATE
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

    # =========================================================================
    # 4. LOWER-LEVEL CORESIGHT & BULK TRANSPORTS (INTERNAL - MANDATORY CONTROLS)
    # =========================================================================

    def _dap_write(self, is_ap: bool, a32: int, data: int):
        ir = JtagInstr.DAP_APACC if is_ap else JtagInstr.DAP_DPACC
        self._shift_ir(ir, tap_index=1)
        # 35-bit frame: Data (32) | Address (2) | Write Flag (1)
        req = (data << 3) | (a32 << 1) | DapReq.WRITE
        return self._shift_dr(req, dr_len=DapReq.SHIFT_LEN, tap_index=1) & DapReq.ACK_MASK

    def _dap_read(self, is_ap: bool, a32: int):
        ir = JtagInstr.DAP_APACC if is_ap else JtagInstr.DAP_DPACC
        self._shift_ir(ir, tap_index=1)
        # 35-bit frame: Dummy Data (32) | Address (2) | Read Flag (1)
        req = (0 << 3) | (a32 << 1) | DapReq.READ
        self._shift_dr(req, dr_len=DapReq.SHIFT_LEN, tap_index=1) # First shift initiates read
        rx_val = self._shift_dr(req, dr_len=DapReq.SHIFT_LEN, tap_index=1) # Second shift extracts data
        return (rx_val >> 3) & 0xFFFFFFFF, rx_val & DapReq.ACK_MASK

    def _init_ahb_ap(self):
        self._dap_write(is_ap=False, a32=CoreSightRegs.DP_CTRL_STAT, data=0x50000000)
        self._dap_write(is_ap=False, a32=CoreSightRegs.DP_SELECT,    data=0x00000000)
        self._dap_write(is_ap=True,  a32=CoreSightRegs.AP_CSW,       data=0x23000012)

    def _write_mem32_bulk(self, start_address: int, words: list):
        """Packs massive blocks of direct AXI memory transactions directly into raw USB endpoints."""
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=start_address)
        self._shift_ir(JtagInstr.DAP_APACC, tap_index=1)
        
        batch_size = 800
        for i in range(0, len(words), batch_size):
            batch = words[i:i+batch_size]
            payload = bytearray()
            for w in batch:
                req = (w << 3) | (CoreSightRegs.AP_DRW << 1) | 0
                shift_val = (req << 1) | 0x01
                
                payload += TmsCommands.IDLE_TO_SHIFT_DR
                payload += MpsseOpcodes.SHIFT_BYTES_LSB_RW + b'\x03\x00' + (shift_val & 0xFFFFFFFF).to_bytes(4, 'little')
                
                rem_val = (shift_val >> 32) & 0x0F
                payload += MpsseOpcodes.SHIFT_BITS_LSB_RW + b'\x02' + struct.pack('<B', rem_val & 0x07)
                
                tms_byte = 0x01 | (((rem_val >> 3) & 0x01) << 7)
                payload += MpsseOpcodes.SHIFT_TMS_NO_READ + b'\x00' + struct.pack('<B', tms_byte)
                payload += TmsCommands.EXIT_TO_IDLE
                
            self.device.write(bytes(payload))
            self.device.purge(ftd.defines.PURGE_RX)

    # =========================================================================
    # 5. ATOMIC JTAG BOUNDARY BITSTREAM OPERATIONS (INTERNAL)
    # =========================================================================

    def _shift_ir(self, instruction: int, tap_index: int):
        shift_value = (instruction << 6) | 0x3F if tap_index == 1 else (0x0F << 6) | instruction
        self._shift_bits(shift_value, 10, is_ir=True)

    def _shift_dr(self, data_val: int, dr_len: int, tap_index: int) -> int:
        shift_value = (data_val << 1) | 0x01 if tap_index == 1 else (0x01 << dr_len) | data_val
        rx_val = self._shift_bits(shift_value, dr_len + 1, is_ir=False)
        return (rx_val >> 1) & ((1 << dr_len) - 1) if tap_index == 1 else rx_val & ((1 << dr_len) - 1)

    def _shift_bits(self, data_val: int, num_bits: int, is_ir: bool = False):
        payload = bytearray(TmsCommands.IDLE_TO_SHIFT_IR if is_ir else TmsCommands.IDLE_TO_SHIFT_DR)
        num_bytes, remaining_bits = (num_bits - 1) // 8, (num_bits - 1) % 8
        last_bit = (data_val >> (num_bits - 1)) & 0x01
        
        if num_bytes > 0:
            payload += MpsseOpcodes.SHIFT_BYTES_LSB_RW + struct.pack('<H', num_bytes - 1) 
            payload += (data_val & ((1 << (num_bytes * 8)) - 1)).to_bytes(num_bytes, 'little')
        if remaining_bits > 0:
            payload += MpsseOpcodes.SHIFT_BITS_LSB_RW + struct.pack('<B', remaining_bits - 1) 
            payload += struct.pack('<B', (data_val >> (num_bytes * 8)) & 0xFF)
        
        tms_byte = 0x01 | (last_bit << 7)
        payload += MpsseOpcodes.SHIFT_TMS_READ + b'\x00' + struct.pack('<B', tms_byte)
        payload += TmsCommands.EXIT_TO_IDLE + MpsseOpcodes.SEND_IMMEDIATE
        self.device.write(bytes(payload))
        
        expected_rx_len = num_bytes + (1 if remaining_bits > 0 else 0) + 1
        rx_data = self.device.read(expected_rx_len)
        rx_val = 0
        
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