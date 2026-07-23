"""
Zynq QSPI Flash Controller & JEDEC Flash Commands.
Bridges the Zynq QSPI memory-mapped controller to standard SPI-NOR flash commands.
(CORRECTED REGISTER BITMAP: Bit 1 for MANUAL_CS_EN according to Zynq TRM UG585)
"""

import time
import struct
from zynq_constants import (
    ZynqRegs, QspiConfig, FlashCmd,
    FLASH_MANUFACTURERS, FLASH_MEMORY_TYPES,
)


class QspiFlash:
    def __init__(self, dap, soc):
        self.dap = dap
        self.soc = soc

    # -------------------------------------------------------------------
    # Internal Hardware Helpers
    # -------------------------------------------------------------------

    def _init_controller(self):
        self.dap.connect()

        self.soc.enable_peripheral_clock(ZynqRegs.LQSPI_CLK_ACT)
        self.soc.enable_qspi_ref_clock()

        self.soc.slcr_unlock()
        # MIO 1-6 su QSPI (0x02) con Pull-Up interni (bit 12) per sbloccare WP e HOLD
        for pin in range(1, 7):
            addr = ZynqRegs.SLCR_BASE + 0x700 + (pin * 4)
            val = self.dap.read_mem32(addr)
            val |= (1 << 12)             # Pull-up
            val = (val & ~0xFF) | 0x02   # QSPI Mux
            self.dap.write_mem32(addr, val)
        self.soc.slcr_lock()

        # Disabilita Linear QSPI controller
        self.dap.write_mem32(ZynqRegs.QSPI_LQSPI_CFG, 0x00000000)

        # Resetta controller
        self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000000)
        
        # Mappa Bit Corretta TRM Zynq-7000:
        # Bit 31: Manual IF, Bit 14: Manual Start EN, Bit 3-5: Div /16 (0x3), Bit 1: Manual CS EN, Bit 0: Master
        CLEAN_MANUAL_CFG = (1 << 31) | (1 << 14) | (0x3 << 3) | (1 << 1) | 1
        
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, CLEAN_MANUAL_CFG)
        self.dap.write_mem32(ZynqRegs.QSPI_STATUS, 0x0000007F)
        self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
        time.sleep(0.005)

        # Svuota RX FIFO
        while (self.dap.read_mem32(ZynqRegs.QSPI_STATUS) & QspiConfig.STATUS_RX_NOT_EMPTY):
            self.dap.read_mem32(ZynqRegs.QSPI_RXD_FIFO)

    def _transfer(self, tx_bytes: bytes, expected_rx_len: int | None = None) -> bytes:
        try:
            rx_bytes = self._manual_transfer(tx_bytes, expected_rx_len)
        except TimeoutError as e:
            print(f" [!] Hardware Warning: {e}")
            return b''

        target_len = expected_rx_len if expected_rx_len is not None else len(tx_bytes)
        return rx_bytes[:target_len]

    def _manual_transfer(self, tx_bytes: bytes, expected_rx_len: int | None = None) -> bytes:
        if len(tx_bytes) > 252:
            raise ValueError("Transfer exceeds QSPI TX FIFO depth.")
            
        self.dap.clear_sticky_errors()

        # Mappa Bit Corretta:
        # Bit 31 = Manual Mode
        # Bit 14 = Manual Start Enable
        # Bit 1  = Manual CS Enable
        # Bit 0  = Master Mode
        base_cfg = (1 << 31) | (1 << 14) | (0x3 << 3) | (1 << 1) | 1

        SAFE_CONFIG_IDLE = base_cfg | QspiConfig.PCS_ALL_HIGH   # CS High (0x8000401B)
        CONFIG_CS0       = base_cfg & ~QspiConfig.PCS_ALL_HIGH  # CS Low  (0x8000001B)
        CONFIG_TRIG      = CONFIG_CS0 | (1 << 16)               # Trigger Bit 16 (0x8001001B)

        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, SAFE_CONFIG_IDLE)
        self.dap.write_mem32(ZynqRegs.QSPI_STATUS, 0x0000007F)

        while (self.dap.read_mem32(ZynqRegs.QSPI_STATUS) & QspiConfig.STATUS_RX_NOT_EMPTY):
            self.dap.read_mem32(ZynqRegs.QSPI_RXD_FIFO)

        # CS LOW
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_CS0)
        self.dap.read_mem32(ZynqRegs.QSPI_STATUS)

        word_count = len(tx_bytes) // 4
        remainder = len(tx_bytes) % 4
        words_expected = word_count + (1 if remainder else 0)

        for w_idx in range(word_count):
            word = struct.unpack_from('<I', tx_bytes, w_idx * 4)[0]
            self.dap.write_mem32(ZynqRegs.QSPI_TXD_FIFO, word)

        if remainder == 1:
            self.dap.write_mem32(ZynqRegs.QSPI_TAIL_1BYTE, tx_bytes[-1])
        elif remainder == 2:
            self.dap.write_mem32(ZynqRegs.QSPI_TAIL_2BYTE, tx_bytes[-2] | (tx_bytes[-1] << 8))
        elif remainder == 3:
            self.dap.write_mem32(ZynqRegs.QSPI_TAIL_3BYTE, tx_bytes[-3] | (tx_bytes[-2] << 8) | (tx_bytes[-1] << 16))

        # TRIGGER
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_TRIG)

        # COMANDI TX-ONLY
        if expected_rx_len == 0:
            timeout = time.time() + 0.5
            while True:
                cfg = self.dap.read_mem32(ZynqRegs.QSPI_CONFIG)
                sts = self.dap.read_mem32(ZynqRegs.QSPI_STATUS)
                if not (cfg & (1 << 16)) or (sts & (1 << 2)):
                    break
                if time.time() > timeout:
                    self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, SAFE_CONFIG_IDLE)
                    raise TimeoutError("QSPI TX timeout waiting for command completion.")
            
            time.sleep(0.001)
            self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, SAFE_CONFIG_IDLE)
            return b''

        # COMANDI READ
        else:
            rx_data = bytearray()
            rx_words = 0
            timeout = time.time() + 1.0
            
            while rx_words < words_expected:
                sts = self.dap.read_mem32(ZynqRegs.QSPI_STATUS)
                if sts & QspiConfig.STATUS_RX_NOT_EMPTY:
                    word = self.dap.read_mem32(ZynqRegs.QSPI_RXD_FIFO)
                    rx_words += 1
                    
                    if rx_words == words_expected and remainder != 0:
                        rx_data.extend(word.to_bytes(4, 'little')[:remainder])
                    else:
                        rx_data.extend(word.to_bytes(4, 'little'))
                elif time.time() > timeout:
                    self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, SAFE_CONFIG_IDLE)
                    raise TimeoutError(f"QSPI timeout waiting for RX. Got {rx_words}/{words_expected} words.")

            time.sleep(0.001)
            self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, SAFE_CONFIG_IDLE)
            return bytes(rx_data)

    # -------------------------------------------------------------------
    # Internal SPI-NOR Flash Protocol Helpers
    # -------------------------------------------------------------------

    def _read_status(self, cmd: int) -> int:
        response = self._transfer(bytes([cmd, 0x00, 0x00, 0x00]), expected_rx_len=4)
        if not response:
            return 0x00
        return response[1] if len(response) > 1 else 0x00

    def _write_enable(self):
        for _ in range(5):
            self._transfer(bytes([FlashCmd.WREN]), expected_rx_len=0)
            time.sleep(0.005)
            if self._read_status(FlashCmd.RDSR) & FlashCmd.SR1_WEL:
                return
            time.sleep(0.01)
        raise RuntimeError("Flash did not set WEL after WREN")

    def _wait_ready(self, operation_name="Operation"):
        while True:
            status = self._read_status(FlashCmd.RDSR)
            if not (status & FlashCmd.SR1_WIP):
                print(f"\n{operation_name} complete.")
                break
            print('.', end='', flush=True)
            time.sleep(0.1)

    def _read_flash_data(self, address: int, length: int) -> bytes:
        if not (0 <= length <= 256):
            raise ValueError("Read length must be between 0 and 256 bytes")

        if length > 128:
            return self._read_flash_data(address, 128) + self._read_flash_data(address + 128, length - 128)

        cmd = bytes([FlashCmd.READ]) + address.to_bytes(3, 'big') + bytes(length)
        data = self._transfer(cmd, expected_rx_len=4 + length)[4:]
        
        if len(data) == length and any(b != 0x00 for b in data):
            return data

        cmd = bytes([FlashCmd.FAST_READ]) + address.to_bytes(3, 'big') + b'\x00' + bytes(length)
        return self._transfer(cmd, expected_rx_len=5 + length)[5:]

    def _verify_erased(self, address: int = 0, length: int = 64) -> bool:
        data = self._read_flash_data(address, length)
        if all(b == 0xFF for b in data):
            return True
        first_bad = next(i for i, b in enumerate(data) if b != 0xFF)
        print(f" -> Erase verification FAILED at offset 0x{first_bad:02X}: {data[first_bad:first_bad+16].hex()}")
        return False

    # -------------------------------------------------------------------
    # Public Workflows
    # -------------------------------------------------------------------

    def read_jedec_id(self):
        self._init_controller()
        response = self._transfer(bytes([FlashCmd.RDID, 0x00, 0x00, 0x00]), expected_rx_len=4)

        if len(response) < 4:
            print("ERROR: JEDEC transfer returned unexpected length or timed out.")
            return

        manuf_id, mem_type, mem_cap = response[1], response[2], response[3]
        if manuf_id in (0x00, 0xFF):
            print("ERROR: Invalid JEDEC ID. Flash MISO line is silent or shorted.")
            print(f" -> Raw response: {response.hex()}")
            return

        manuf_name = FLASH_MANUFACTURERS.get(manuf_id, "Unknown Manufacturer")
        mem_type_name = FLASH_MEMORY_TYPES.get(manuf_id, {}).get(mem_type, "Unknown Type")
        
        try:
            cap_mb = (1 << mem_cap) // (1024 * 1024)
            cap_str = f"{cap_mb} MB" if cap_mb > 0 else f"{(1 << mem_cap) // 1024} KB"
        except Exception:
            cap_str = "Unknown"

        print(f"Flash detected: {manuf_name} | {mem_type_name} | Capacity: {cap_str}")

    def erase_chip(self):
        print("Initiating Full Chip Erase... (This may take several seconds)")
        self._init_controller()
        self._write_enable()
        
        self._transfer(bytes([FlashCmd.CE]), expected_rx_len=0)
        self._wait_ready("Chip Erase")
        
        if not self._verify_erased(0, 64):
            raise RuntimeError("Flash content verification failed after erase.")

    def erase_sector(self, offset: int):
        print(f"Erasing 64KB Sector at offset 0x{offset:06X}...")
        self._init_controller()
        self._write_enable()
        
        cmd = bytes([FlashCmd.SE]) + offset.to_bytes(3, 'big')
        self._transfer(cmd, expected_rx_len=0)
        self._wait_ready("Sector Erase")

    def write_binary_file(self, filepath: str = "bootblock.bin", start_offset: int = 0):
        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            print(f"ERROR: File '{filepath}' not found!")
            return

        print(f"Flashing '{filepath}' ({len(data)} bytes) at 0x{start_offset:06X}")
        self._init_controller()

        t0 = time.time()
        for i in range(0, len(data), 128):
            chunk = data[i:i + 128]
            current_offset = start_offset + i

            self._write_enable()
            cmd = bytes([FlashCmd.PP]) + current_offset.to_bytes(3, 'big') + chunk
            self._transfer(cmd, expected_rx_len=0)
            self._wait_ready("Page Program")

            progress = min(100.0, ((i + len(chunk)) / len(data)) * 100)
            print(f" -> Progress: {progress:05.2f}%", end='\r')

        print(f"\nSUCCESS: Flashed in {time.time()-t0:.2f}s.")

    def enable_quad_mode(self):
        self._init_controller()
        status1 = self._read_status(FlashCmd.RDSR)
        status2 = self._read_status(FlashCmd.RDSR2)

        self._write_enable()
        jedec = self._transfer(bytes([FlashCmd.RDID, 0x00, 0x00, 0x00]), expected_rx_len=4)
        manuf_id = jedec[1] if len(jedec) > 1 else None

        new_status1 = status1 | FlashCmd.QE_BIT
        new_status2 = status2 | FlashCmd.SR2_QE

        if manuf_id == 0x9D:
            self._transfer(bytes([FlashCmd.WRSR, new_status1]), expected_rx_len=0)
        else:
            self._transfer(bytes([FlashCmd.WRSR, new_status1, new_status2]), expected_rx_len=0)
        
        self._wait_ready("Write Status Register")
        print("Quad Mode enabled.")

    def disable_quad_mode(self):
        self._init_controller()
        status1 = self._read_status(FlashCmd.RDSR)
        status2 = self._read_status(FlashCmd.RDSR2)

        self._write_enable()
        jedec = self._transfer(bytes([FlashCmd.RDID, 0x00, 0x00, 0x00]), expected_rx_len=4)
        manuf_id = jedec[1] if len(jedec) > 1 else None

        new_status1 = status1 & ~FlashCmd.QE_BIT
        new_status2 = status2 & ~FlashCmd.SR2_QE

        if manuf_id == 0x9D:
            self._transfer(bytes([FlashCmd.WRSR, new_status1]), expected_rx_len=0)
        else:
            self._transfer(bytes([FlashCmd.WRSR, new_status1, new_status2]), expected_rx_len=0)
            
        self._wait_ready("Write Status Register")
        print("Quad Mode disabled.")