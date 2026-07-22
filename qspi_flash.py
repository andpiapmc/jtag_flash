"""
Zynq QSPI Flash Controller & JEDEC Flash Commands.
Bridges the Zynq QSPI memory-mapped controller to standard SPI-NOR flash commands.
"""

import time
import struct
import traceback
from zynq_constants import (
    ZynqRegs, QspiConfig, FlashCmd,
    FLASH_MANUFACTURERS, FLASH_MEMORY_TYPES,
)


class QspiFlash:
    def __init__(self, dap, soc, gpio=None):
        self.dap = dap
        self.soc = soc
        self.gpio = gpio

    # -------------------------------------------------------------------
    # Internal Hardware Helpers
    # -------------------------------------------------------------------

    def _init_controller(self):
        """
        Initializes the QSPI controller.
        Forces WP# and HOLD# (MIO 4 and 5) high as GPIO to prevent stalls 
        if the QE (Quad Enable) bit is left set on the flash.
        """
        if self.gpio:
            self.gpio.force_mio_gpio_high(4)
            self.gpio.force_mio_gpio_high(5)

        self.dap.connect()
        self.dap.write_mem32(ZynqRegs.QSPI_LQSPI_CFG, 0x00000000)  # Disable Linear Mode
        
        try:
            self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
        except Exception:
            pass # Write-only register on some targets

    def _transfer(self, tx_bytes: bytes, expected_rx_len: int | None = None) -> bytes:
        """Wrapper to ensure controller is enabled before transferring."""
        try:
            if self.dap.read_mem32(ZynqRegs.QSPI_ENABLE) == 0:
                self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
        except Exception:
            pass

        rx_bytes = self._manual_transfer(tx_bytes, expected_rx_len)
        target_len = expected_rx_len if expected_rx_len is not None else len(tx_bytes)
        
        if len(rx_bytes) < target_len:
            raise RuntimeError(f"QSPI transfer returned too few bytes: expected {target_len}, got {len(rx_bytes)}")
        return rx_bytes[:target_len]

    def _manual_transfer(self, tx_bytes: bytes, expected_rx_len: int | None = None) -> bytes:
        """Core SPI bit-banging engine via memory-mapped FIFOs."""
        self.dap.write_mem32(ZynqRegs.QSPI_LQSPI_CFG, 0x00000000)

        SAFE_CONFIG_IDLE = (1 | (0x4 << 3) | QspiConfig.MANUAL_START_EN | 
                            QspiConfig.MANUAL_CS_EN | QspiConfig.PCS0_HIGH)
        CONFIG_CS0 = SAFE_CONFIG_IDLE & ~QspiConfig.PCS0_HIGH
        CONFIG_TRIG = CONFIG_CS0 | QspiConfig.MANUAL_START

        self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, SAFE_CONFIG_IDLE)
        self.dap.clear_sticky_errors()

        # Flush RX FIFO
        for _ in range(64):
            if not (self.dap.read_mem32(ZynqRegs.QSPI_STATUS) & 0x10):
                break
            self.dap.read_mem32(ZynqRegs.QSPI_RXD_FIFO)

        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_CS0)

        rx_bytes = bytearray()
        total_rx_needed = expected_rx_len if expected_rx_len is not None else len(tx_bytes)

        # 128-byte chunks to fit within the Zynq TX FIFO safely
        for i in range(0, len(tx_bytes), 128):
            chunk = tx_bytes[i:i + 128]
            word_count = len(chunk) // 4
            remainder = len(chunk) % 4
            chunk_rx_needed = min(total_rx_needed - len(rx_bytes), len(chunk))

            # Aligned words
            for w_idx in range(word_count):
                word = struct.unpack_from('<I', chunk, w_idx * 4)[0]
                self.dap.write_mem32(ZynqRegs.QSPI_TXD_FIFO, word)

            # Leftover bytes in tail registers
            if remainder == 1:
                self.dap.write_mem32(ZynqRegs.QSPI_BASE + 0x80, chunk[-1])
            elif remainder == 2:
                self.dap.write_mem32(ZynqRegs.QSPI_BASE + 0x84, chunk[-2] | (chunk[-1] << 8))
            elif remainder == 3:
                self.dap.write_mem32(ZynqRegs.QSPI_BASE + 0x88, chunk[-3] | (chunk[-2] << 8) | (chunk[-1] << 16))

            self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_TRIG)

            if chunk_rx_needed > 0:
                remaining_rx = chunk_rx_needed
                while remaining_rx > 0:
                    self._wait_rx_ready()
                    word = self.dap.read_mem32(ZynqRegs.QSPI_RXD_FIFO)
                    take = min(remaining_rx, 4)
                    rx_bytes.extend(word.to_bytes(4, 'little')[:take])
                    remaining_rx -= take
            else:
                self._wait_tx_ready()

            self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_CS0)

        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, SAFE_CONFIG_IDLE)
        time.sleep(0.001)

        return bytes(rx_bytes)

    def _wait_rx_ready(self, timeout_ms=200):
        elapsed = 0
        while elapsed < timeout_ms:
            if self.dap.read_mem32(ZynqRegs.QSPI_STATUS) & 0x10:
                return
            time.sleep(0.002)
            elapsed += 2
        raise RuntimeError("QSPI transfer timeout waiting for RX data")

    def _wait_tx_ready(self, timeout_ms=200):
        elapsed = 0
        while elapsed < timeout_ms:
            if self.dap.read_mem32(ZynqRegs.QSPI_STATUS) & 0x04:
                return
            time.sleep(0.002)
            elapsed += 2

    # -------------------------------------------------------------------
    # Internal SPI-NOR Flash Protocol Helpers
    # -------------------------------------------------------------------

    def _read_status(self, cmd: int) -> int:
        """Reads a flash status register (e.g. FlashCmd.RDSR or RDSR2)."""
        return self._transfer(bytes([cmd, 0x00]))[1]

    def _write_enable(self):
        """Sends WREN and polls until WEL (Write Enable Latch) bit is set."""
        for _ in range(3):
            self._transfer(bytes([FlashCmd.WREN]), expected_rx_len=0)
            if self._read_status(FlashCmd.RDSR) & 0x02:
                return
            time.sleep(0.02)
        raise RuntimeError("Flash did not set WEL after WREN")

    def _wait_ready(self, operation_name="Operation"):
        """Polls RDSR until WIP (Write In Progress) bit clears."""
        while True:
            if not (self._read_status(FlashCmd.RDSR) & 0x01):
                print(f"\n{operation_name} complete.")
                break
            print('.', end='', flush=True)
            time.sleep(0.1)

    def _read_flash_data(self, address: int, length: int) -> bytes:
        if not (0 <= length <= 256):
            raise ValueError("Read length must be between 0 and 256 bytes")

        cmd = bytes([0x03]) + address.to_bytes(3, 'big') + bytes(length)
        data = self._transfer(cmd)[4:]
        
        # Fallback to Fast Read if Standard Read fails (returns all zeros)
        if len(data) == length and any(b != 0x00 for b in data):
            return data

        cmd = bytes([FlashCmd.FAST_READ]) + address.to_bytes(3, 'big') + b'\x00' + bytes(length)
        return self._transfer(cmd)[5:]

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
        response = self._transfer(bytes([FlashCmd.RDID, 0x00, 0x00, 0x00]))
        
        if len(response) < 4:
            print("ERROR: JEDEC transfer returned unexpected length")
            return

        manuf_id, mem_type, mem_cap = response[1], response[2], response[3]
        if manuf_id in (0x00, 0xFF):
            print("ERROR: Invalid JEDEC ID. Flash MISO line is silent or shorted.")
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
        jedec = self._transfer(bytes([FlashCmd.RDID, 0x00, 0x00, 0x00]))
        manuf_id = jedec[1] if len(jedec) > 1 else None

        new_status1 = status1 | FlashCmd.QE_BIT
        new_status2 = status2 | 0x02

        if manuf_id == 0x9D: # ISSI uses a single-byte WRSR
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
        jedec = self._transfer(bytes([FlashCmd.RDID, 0x00, 0x00, 0x00]))
        manuf_id = jedec[1] if len(jedec) > 1 else None

        new_status1 = status1 & ~FlashCmd.QE_BIT
        new_status2 = status2 & ~0x02

        if manuf_id == 0x9D:
            self._transfer(bytes([FlashCmd.WRSR, new_status1]), expected_rx_len=0)
        else:
            self._transfer(bytes([FlashCmd.WRSR, new_status1, new_status2]), expected_rx_len=0)
            
        self._wait_ready("Write Status Register")
        print("Quad Mode disabled.")