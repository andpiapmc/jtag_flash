"""
Zynq-7000 QSPI Controller and SPI-NOR Flash Memory Driver.
Handles memory-mapped I/O manual transfers, status polling, and SPI flash workflows.
"""

import time
import struct
from zynq_constants import (
    ZynqRegs, QspiConfig, FlashCmd,
    FLASH_MANUFACTURERS, FLASH_MEMORY_TYPES
)


class QspiFlash:
    """Provides high-level SPI-NOR flash memory operations via Zynq QSPI controller."""

    def __init__(self, dap, soc):
        self.dap = dap
        self.soc = soc

    # -------------------------------------------------------------------
    # Internal Hardware Helpers
    # -------------------------------------------------------------------

    def _init_controller(self) -> None:
        """Configures SLCR clocks, MIO multiplexing, and initializes the QSPI controller."""
        self.dap.connect()

        self.soc.enable_peripheral_clock(ZynqRegs.LQSPI_CLK_ACT)
        self.soc.enable_qspi_ref_clock()

        self.soc.slcr_unlock()
        # MIO 1-6 (SCLK/CS/MOSI/WP/HOLD/MISO) stay muxed to QSPI, with each
        # pin's internal pull-up enabled - this is what keeps WP#/HOLD# from
        # floating when the flash has Quad-Enable set, without needing to
        # steal the pins away as GPIO outputs.
        for pin in range(1, 7):
            addr = ZynqRegs.SLCR_MIO_CTRL_0 + (pin * 4)
            val = self.dap.read_mem32(addr)
            val |= ZynqRegs.MIO_PULLUP_BIT
            val = (val & ~0xFF) | ZynqRegs.MIO_PIN_MUX_QSPI
            self.dap.write_mem32(addr, val)
        self.soc.slcr_lock()

        # Disable Linear QSPI controller mode
        self.dap.write_mem32(ZynqRegs.QSPI_LQSPI_CFG, 0x00000000)

        # Reset QSPI state machine
        self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000000)

        # Zynq-7000 QSPI Manual Mode Configuration - see QspiConfig.MANUAL_BASE_CFG
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, QspiConfig.MANUAL_BASE_CFG)
        self.dap.write_mem32(ZynqRegs.QSPI_STATUS, 0x0000007F)
        self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
        time.sleep(0.005)

        # Flush stale entries in RX FIFO
        while self.dap.read_mem32(ZynqRegs.QSPI_STATUS) & QspiConfig.STATUS_RX_NOT_EMPTY:
            self.dap.read_mem32(ZynqRegs.QSPI_RXD_FIFO)

    def _transfer(self, tx_bytes: bytes, expected_rx_len: int | None = None) -> bytes:
        """Safe wrapper around _manual_transfer handling hardware timeouts."""
        try:
            rx_bytes = self._manual_transfer(tx_bytes, expected_rx_len)
        except TimeoutError as e:
            print(f" [!] Hardware Warning: {e}")
            return b''

        target_len = expected_rx_len if expected_rx_len is not None else len(tx_bytes)
        return rx_bytes[:target_len]

    def _manual_transfer(self, tx_bytes: bytes, expected_rx_len: int | None = None) -> bytes:
        """Executes a manual SPI transfer over the QSPI controller registers."""
        if len(tx_bytes) > 252:
            raise ValueError("Transfer exceeds QSPI TX FIFO depth (max 252 bytes).")

        self.dap.clear_sticky_errors()

        safe_config_idle = QspiConfig.MANUAL_BASE_CFG | QspiConfig.PCS_ALL_HIGH
        config_cs0 = QspiConfig.MANUAL_BASE_CFG & ~QspiConfig.PCS_ALL_HIGH
        config_trig = config_cs0 | QspiConfig.MANUAL_START

        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, safe_config_idle)
        self.dap.write_mem32(ZynqRegs.QSPI_STATUS, 0x0000007F)

        # Drain RX FIFO before transaction
        while self.dap.read_mem32(ZynqRegs.QSPI_STATUS) & QspiConfig.STATUS_RX_NOT_EMPTY:
            self.dap.read_mem32(ZynqRegs.QSPI_RXD_FIFO)

        # Assert Chip Select (CS Low)
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, config_cs0)
        self.dap.read_mem32(ZynqRegs.QSPI_STATUS)  # APB synchronization barrier

        word_count = len(tx_bytes) // 4
        remainder = len(tx_bytes) % 4
        words_expected = word_count + (1 if remainder else 0)

        # Fill TX FIFO
        for w_idx in range(word_count):
            word = struct.unpack_from('<I', tx_bytes, w_idx * 4)[0]
            self.dap.write_mem32(ZynqRegs.QSPI_TXD_FIFO, word)

        if remainder == 1:
            self.dap.write_mem32(ZynqRegs.QSPI_TAIL_1BYTE, tx_bytes[-1])
        elif remainder == 2:
            self.dap.write_mem32(ZynqRegs.QSPI_TAIL_2BYTE, tx_bytes[-2] | (tx_bytes[-1] << 8))
        elif remainder == 3:
            self.dap.write_mem32(ZynqRegs.QSPI_TAIL_3BYTE, tx_bytes[-3] | (tx_bytes[-2] << 8) | (tx_bytes[-1] << 16))

        # Start SPI transfer
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, config_trig)

        # TX-Only transaction handling
        if expected_rx_len == 0:
            timeout = time.time() + 0.5
            while True:
                cfg = self.dap.read_mem32(ZynqRegs.QSPI_CONFIG)
                if not (cfg & QspiConfig.MANUAL_START):
                    break
                if time.time() > timeout:
                    self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, safe_config_idle)
                    raise TimeoutError("QSPI TX timeout waiting for command completion.")

            self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, safe_config_idle)
            # Wait for CS edge rising
            time.sleep(0.005)
            return b''

        # RX-Data transaction handling
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
                self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, safe_config_idle)
                raise TimeoutError(f"QSPI timeout waiting for RX. Got {rx_words}/{words_expected} words.")

        time.sleep(0.001)
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, safe_config_idle)
        return bytes(rx_data)

    # -------------------------------------------------------------------
    # Internal SPI-NOR Flash Helpers
    # -------------------------------------------------------------------

    def _read_status(self, cmd: int) -> int:
        """Reads a status register from the SPI flash."""
        response = self._transfer(bytes([cmd, 0x00, 0x00, 0x00]), expected_rx_len=4)
        if not response:
            return 0x00
        return response[1] if len(response) > 1 else 0x00

    def _read_jedec_raw(self) -> bytes:
        """Returns the raw 4-byte JEDEC ID response (manufacturer, type, capacity)."""
        return self._transfer(bytes([FlashCmd.RDID, 0x00, 0x00, 0x00]), expected_rx_len=4)

    def _write_enable(self) -> None:
        """Sends Write Enable (WREN) instruction and verifies Write Enable Latch (WEL)."""
        for _ in range(5):
            self._transfer(bytes([FlashCmd.WREN]), expected_rx_len=0)
            time.sleep(0.005)
            if self._read_status(FlashCmd.RDSR) & FlashCmd.SR1_WEL:
                return
            time.sleep(0.01)
        raise RuntimeError("Flash did not set WEL after WREN instruction")

    def _wait_ready(self, operation_name: str = "Operation") -> None:
        """Polls the Write-In-Progress (WIP) bit with progress dots for long operations (Erase)."""
        time.sleep(0.1)  # Settling delay to allow flash hardware to set WIP bit
        for _ in range(5):
            status = self._read_status(FlashCmd.RDSR)
            if status & FlashCmd.SR1_WIP:
                break
            time.sleep(0.02)

        while True:
            status = self._read_status(FlashCmd.RDSR)
            if not (status & FlashCmd.SR1_WIP):
                print(f"\n{operation_name} complete.")
                break
            print('.', end='', flush=True)
            time.sleep(0.1)

    def _wait_ready_fast(self, timeout_sec: float = 2.0) -> None:
        """Low-latency WIP polling for fast operations (Page Programming)."""
        time.sleep(0.001)
        timeout = time.time() + timeout_sec
        while time.time() < timeout:
            status = self._read_status(FlashCmd.RDSR)
            if not (status & FlashCmd.SR1_WIP):
                return
            time.sleep(0.001)
        raise TimeoutError("Timeout waiting for Flash write operation to complete.")

    def _read_flash_data(self, address: int, length: int) -> bytes:
        """Reads arbitrary data blocks from the specified flash memory address."""
        if not (0 <= length <= 256):
            raise ValueError("Read length must be between 0 and 256 bytes")

        if length > 128:
            return self._read_flash_data(address, 128) + self._read_flash_data(address + 128, length - 128)

        cmd = bytes([FlashCmd.READ]) + address.to_bytes(3, 'big') + bytes(length)
        return self._transfer(cmd, expected_rx_len=4 + length)[4:]

    def _verify_erased(self, address: int = 0, length: int = 64) -> bool:
        """Verifies if the specified memory region is completely erased (0xFF)."""
        data = self._read_flash_data(address, length)
        if all(b == 0xFF for b in data):
            return True
        first_bad = next(i for i, b in enumerate(data) if b != 0xFF)
        print(f" -> Erase verification FAILED at offset 0x{first_bad:02X}: {data[first_bad:first_bad+16].hex()}")
        return False

    def _clear_block_protection(self) -> None:
        """Clears Block Protect bits in Status Register if enabled."""
        status1 = self._read_status(FlashCmd.RDSR)
        if status1 & 0x3C:
            self._write_enable()
            self._transfer(bytes([FlashCmd.WRSR, status1 & ~0x3C]), expected_rx_len=0)
            time.sleep(0.01)

    # -------------------------------------------------------------------
    # Public SPI-NOR Flash Workflows
    # -------------------------------------------------------------------

    def read_jedec_id(self) -> None:
        """Reads JEDEC ID (0x9F) and prints flash manufacturer details."""
        self._init_controller()
        response = self._read_jedec_raw()

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

    def erase_chip(self) -> None:
        """Executes a Full Chip Erase command (0xC7)."""
        print("Initiating Full Chip Erase... (This may take several seconds)")
        self._init_controller()
        self._clear_block_protection()
        self._write_enable()

        self._transfer(bytes([FlashCmd.CE]), expected_rx_len=0)
        self._wait_ready("Chip Erase")

        if not self._verify_erased(0, 64):
            raise RuntimeError("Flash content verification failed after erase.")

    def erase_sector(self, offset: int) -> None:
        """Erases a single 64KB Sector (0xD8) at the given offset (auto-aligned to 64KB)."""
        aligned_offset = offset & ~0xFFFF
        if aligned_offset != offset:
            print(f"Notice: Unaligned offset 0x{offset:06X} rounded to 64KB sector boundary 0x{aligned_offset:06X}")

        print(f"Erasing 64KB Sector at offset 0x{aligned_offset:06X}...")
        self._init_controller()
        self._clear_block_protection()
        self._write_enable()

        cmd = bytes([FlashCmd.SE]) + aligned_offset.to_bytes(3, 'big')
        self._transfer(cmd, expected_rx_len=0)
        self._wait_ready("Sector Erase")

    def _set_quad_mode(self, enable: bool) -> None:
        """Sets or clears the Quad-Enable (QE) bit in the flash status registers."""
        self._init_controller()
        status1 = self._read_status(FlashCmd.RDSR)
        status2 = self._read_status(FlashCmd.RDSR2)

        self._write_enable()
        jedec = self._read_jedec_raw()
        manuf_id = jedec[1] if len(jedec) > 1 else None

        if enable:
            new_status1 = status1 | FlashCmd.QE_BIT
            new_status2 = status2 | FlashCmd.SR2_QE
        else:
            new_status1 = status1 & ~FlashCmd.QE_BIT
            new_status2 = status2 & ~FlashCmd.SR2_QE

        if manuf_id == 0x9D:  # ISSI status register WRSR
            self._transfer(bytes([FlashCmd.WRSR, new_status1]), expected_rx_len=0)
        else:
            self._transfer(bytes([FlashCmd.WRSR, new_status1, new_status2]), expected_rx_len=0)

        self._wait_ready("Write Status Register")
        print(f"Quad Mode {'enabled' if enable else 'disabled'}.")

    def enable_quad_mode(self) -> None:
        """Enables Quad I/O mode in the flash status register."""
        self._set_quad_mode(True)

    def disable_quad_mode(self) -> None:
        """Disables Quad I/O mode in the flash status register."""
        self._set_quad_mode(False)