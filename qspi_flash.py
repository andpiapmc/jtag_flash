"""
LAYER 4b - ZYNQ QSPI FLASH CONTROLLER & JEDEC FLASH COMMANDS
=============================================================
Bridges the Zynq QSPI peripheral (a memory-mapped controller, accessed via
AHB-AP register writes) to standard SPI-NOR flash commands: JEDEC ID, Read,
Page Program, Sector/Chip Erase, Status Register access, Quad-Enable.

Two transfer helpers exist and it is worth understanding the difference:
  - manual_transfer(): the raw, "bit-banged via memory-mapped FIFO" SPI
    transaction. Every other method funnels through this one.
  - transfer(): a thin wrapper around manual_transfer() that additionally
    double-checks the peripheral is enabled and validates the response
    length. Prefer this one from application code.

Built on top of: coresight_dap.py (uses it purely as a "read/write memory"
interface into the QSPI controller's memory-mapped registers).
"""

import time
import struct
import traceback
from zynq_constants import (
    ZynqRegs, QspiConfig, FlashCmd, CoreSightRegs, DapReq,
    FLASH_MANUFACTURERS, FLASH_MEMORY_TYPES,
)


class QspiFlash:
    """QSPI controller + SPI-NOR flash operations, built on a CoreSightDap memory window."""

    def __init__(self, dap, soc, gpio=None):
        self.dap = dap
        self.soc = soc
        self.gpio = gpio

    # =====================================================================
    # Controller bring-up
    # =====================================================================

    def init_controller(self):
        """
        Inizializza il controller QSPI.
        Forza preventivamente WP# e HOLD# (MIO 4 e MIO 5) alti come GPIO per evitare
        stalli se il bit QE è rimasto impostato sulla flash.
        """
        if self.gpio:
            self.gpio.force_mio_gpio_high(4)
            self.gpio.force_mio_gpio_high(5)

        self.dap.connect()
        self.dap.write_mem32(ZynqRegs.QSPI_LQSPI_CFG, 0x00000000)  # Disable Linear Mode

        # Enable peripheral. Some targets use a write-only enable register,
        # so readback may remain zero even when the peripheral is active.
        try:
            self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
        except Exception as e:
            print(f"Warning: failed to write QSPI_ENABLE: {e}")
        try:
            en = self.dap.read_mem32(ZynqRegs.QSPI_ENABLE)
            if not (en & 0x1):
                print(f"Warning: QSPI_ENABLE readback=0x{en:08X}; this register may be write-only.")
        except Exception as e:
            print(f"Warning: read QSPI_ENABLE failed: {e}")

    def debug_regs(self, label: str = "QSPI"):
        """Dumps the main QSPI controller registers for troubleshooting."""
        try:
            qspi_config = self.dap.read_mem32(ZynqRegs.QSPI_CONFIG)
            qspi_status = self.dap.read_mem32(ZynqRegs.QSPI_STATUS)
            try:
                qspi_enable = self.dap.read_mem32(ZynqRegs.QSPI_ENABLE)
            except Exception:
                qspi_enable = None
            print(f"{label}: QSPI_CONFIG=0x{qspi_config:08X}, QSPI_STATUS=0x{qspi_status:08X}, "
                  f"QSPI_ENABLE={('0x%08X' % qspi_enable) if qspi_enable is not None else 'N/A'}")
        except Exception as e:
            print(f"Warning: failed to dump QSPI registers for {label}: {e}")
            traceback.print_exc()

    # =====================================================================
    # SPI transaction engine
    # =====================================================================

    def manual_transfer(self, tx_bytes: bytes, expected_rx_len: int | None = None) -> bytes:
        """
        Executes one manual (software-driven) SPI transaction: forces Linear
        Mode off, configures the controller for manual chip-select control,
        asserts CS0, streams `tx_bytes` through the TX FIFO in 128-byte
        chunks (word-aligned + a small tail register for 1-3 leftover
        bytes), and collects `expected_rx_len` bytes from the RX FIFO.
        """
        # Force LQSPI configuration to 0 (Linear mode DISABLED)
        self.dap.write_mem32(ZynqRegs.QSPI_LQSPI_CFG, 0x00000000)

        # Build a safe manual QSPI configuration from known-good settings.
        SAFE_CONFIG_IDLE = (
            1 |                      # Master mode enable
            (0x4 << 3) |             # Baud rate divisor = /32
            QspiConfig.MANUAL_START_EN |
            QspiConfig.MANUAL_CS_EN |
            QspiConfig.PCS0_HIGH
        )
        CONFIG_CS0 = SAFE_CONFIG_IDLE & ~QspiConfig.PCS0_HIGH  # CS0 Low (Active)
        CONFIG_TRIG = CONFIG_CS0 | QspiConfig.MANUAL_START

        # Ensure controller is enabled and configured before transfer.
        self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, SAFE_CONFIG_IDLE)

        # Clear DAP sticky errors. NOTE: intentionally reuses whichever IR is
        # currently loaded (see CoreSightDap.clear_sticky_errors docstring).
        self.dap.clear_sticky_errors()

        # Flush RX FIFO
        for _ in range(64):
            if not (self.dap.read_mem32(ZynqRegs.QSPI_STATUS) & 0x10):
                break
            self.dap.read_mem32(ZynqRegs.QSPI_RXD_FIFO)

        # Assert CS0
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_CS0)

        rx_bytes = bytearray()
        chunk_size = 128
        total_rx_needed = expected_rx_len if expected_rx_len is not None else len(tx_bytes)

        for i in range(0, len(tx_bytes), chunk_size):
            chunk = tx_bytes[i:i + chunk_size]
            word_count = len(chunk) // 4
            remainder = len(chunk) % 4
            chunk_rx_needed = min(total_rx_needed - len(rx_bytes), len(chunk))

            # Write TX: whole 32-bit words go through the main FIFO...
            for w_idx in range(word_count):
                word = struct.unpack_from('<I', chunk, w_idx * 4)[0]
                self.dap.write_mem32(ZynqRegs.QSPI_TXD_FIFO, word)

            # ...leftover 1-3 bytes go through the dedicated "tail" FIFO
            # registers (QSPI hardware requires this for non-word-aligned
            # transfers).
            if remainder == 1:
                self.dap.write_mem32(ZynqRegs.QSPI_BASE + 0x80, chunk[-1])
            elif remainder == 2:
                word = chunk[-2] | (chunk[-1] << 8)
                self.dap.write_mem32(ZynqRegs.QSPI_BASE + 0x84, word)
            elif remainder == 3:
                word = chunk[-3] | (chunk[-2] << 8) | (chunk[-1] << 16)
                self.dap.write_mem32(ZynqRegs.QSPI_BASE + 0x88, word)

            # Trigger Transfer
            self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_TRIG)

            if chunk_rx_needed > 0:
                remaining_rx = chunk_rx_needed
                timeout_ms = 200
                while remaining_rx > 0:
                    elapsed_ms = 0
                    while elapsed_ms < timeout_ms:
                        if self.dap.read_mem32(ZynqRegs.QSPI_STATUS) & 0x10:
                            break
                        time.sleep(0.002)
                        elapsed_ms += 2

                    if not (self.dap.read_mem32(ZynqRegs.QSPI_STATUS) & 0x10):
                        qspi_status = self.dap.read_mem32(ZynqRegs.QSPI_STATUS)
                        qspi_config = self.dap.read_mem32(ZynqRegs.QSPI_CONFIG)
                        raise RuntimeError(
                            f"QSPI transfer timeout waiting for RX data (TX={chunk.hex()}, "
                            f"STATUS=0x{qspi_status:08X}, CONFIG=0x{qspi_config:08X})"
                        )

                    word = self.dap.read_mem32(ZynqRegs.QSPI_RXD_FIFO)
                    word_bytes = word.to_bytes(4, 'little')
                    take = min(remaining_rx, 4)
                    rx_bytes.extend(word_bytes[:take])
                    remaining_rx -= take
            else:
                # Write-only command: allow the transfer to complete before de-asserting CS.
                timeout_ms = 200
                elapsed_ms = 0
                while elapsed_ms < timeout_ms:
                    status = self.dap.read_mem32(ZynqRegs.QSPI_STATUS)
                    if status & 0x04:
                        break
                    time.sleep(0.002)
                    elapsed_ms += 2

            self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, CONFIG_CS0)

        # De-assert CS0
        self.dap.write_mem32(ZynqRegs.QSPI_CONFIG, SAFE_CONFIG_IDLE)
        time.sleep(0.001)

        return bytes(rx_bytes)

    def transfer(self, tx_bytes: bytes, expected_rx_len: int | None = None) -> bytes:
        """Executes a SPI transaction via the Zynq QSPI FIFOs, with an enable-check and length validation."""
        base_cfg = self.dap.read_mem32(ZynqRegs.QSPI_CONFIG)
        try:
            qspi_en = self.dap.read_mem32(ZynqRegs.QSPI_ENABLE)
            qspi_status = self.dap.read_mem32(ZynqRegs.QSPI_STATUS)
            print(f"QSPI pre-check: QSPI_ENABLE=0x{qspi_en:08X}, QSPI_STATUS=0x{qspi_status:08X}, QSPI_CONFIG=0x{base_cfg:08X}")
            if qspi_en == 0:
                print("Warning: QSPI peripheral disabled; attempting to enable it.")
                try:
                    self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
                    time.sleep(0.01)
                except Exception as e:
                    print(f"Failed to enable QSPI peripheral: {e}")
        except Exception as e:
            print(f"Warning: could not read QSPI pre-check registers: {e}")

        rx_bytes = self.manual_transfer(tx_bytes, expected_rx_len=expected_rx_len)
        if expected_rx_len is None:
            expected_rx_len = len(tx_bytes)
        if len(rx_bytes) < expected_rx_len:
            raise RuntimeError(f"QSPI transfer returned too few bytes: expected {expected_rx_len}, got {len(rx_bytes)}")
        return rx_bytes[:expected_rx_len]

    # =====================================================================
    # Flash status register helpers
    # =====================================================================

    def read_status_register(self) -> int:
        response = self.transfer(bytes([FlashCmd.RDSR, 0x00, 0x00, 0x00]))
        if len(response) < 2:
            raise RuntimeError("Failed to read QSPI status register")
        print(f" -> RDSR raw response = {response.hex()}")
        return response[1]

    def read_status_register2(self) -> int:
        response = self.transfer(bytes([FlashCmd.RDSR2, 0x00, 0x00, 0x00]))
        if len(response) < 2:
            raise RuntimeError("Failed to read QSPI status register-2")
        print(f" -> RDSR2 raw response = {response.hex()}")
        return response[1]

    def manual_read_status_register(self) -> int:
        response = self.manual_transfer(bytes([FlashCmd.RDSR, 0x00, 0x00, 0x00]))
        if len(response) < 2:
            raise RuntimeError("Failed to read QSPI status register")
        return response[1]

    def manual_read_status_register2(self) -> int:
        response = self.manual_transfer(bytes([FlashCmd.RDSR2, 0x00, 0x00, 0x00]))
        if len(response) < 2:
            raise RuntimeError("Failed to read QSPI status register-2")
        return response[1]

    def decode_status(self, status: int) -> str:
        """Translates the flash status register bits into human-readable flags."""
        flags = []
        if status & 0x01:
            flags.append("WIP")
        if status & 0x02:
            flags.append("WEL")
        if status & FlashCmd.QE_BIT:
            flags.append("QE")
        if status & 0x20 and not (status & FlashCmd.QE_BIT):
            flags.append("SRP0")
        return ",".join(flags) if flags else "OK"

    def write_enable(self):
        """Sends the WREN (Write Enable) command to the flash and verifies WEL, with retries."""
        attempts = 3
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                print(f"Retrying WREN (attempt {attempt}/{attempts})...")
                time.sleep(0.02)

            try:
                response = self.transfer(bytes([FlashCmd.WREN]), expected_rx_len=0)
                print(f" -> WREN raw response = {response.hex()}")
            except Exception as e:
                print(f"Warning: WREN transfer attempt failed: {e}")

            self.debug_regs("after WREN")
            time.sleep(0.005)
            status = self.read_status_register()
            status2 = self.read_status_register2()
            print(f"QSPI WREN status = 0x{status:02X} ({self.decode_status(status)})")
            print(f"QSPI WREN status-2 = 0x{status2:02X}")

            if status & 0x02:
                return

            print("WEL not set; retrying WREN via manual FIFO path and re-checking RDSR...")
            try:
                try:
                    self.dap.write_mem32(ZynqRegs.QSPI_ENABLE, 0x00000001)
                    time.sleep(0.001)
                except Exception:
                    pass

                resp2 = self.manual_transfer(bytes([FlashCmd.WREN]), expected_rx_len=0)
                print(f" -> WREN manual raw response = {resp2.hex()}")
                time.sleep(0.02)
                status = self.manual_read_status_register()
                status2 = self.manual_read_status_register2()
                print(f"QSPI WREN status (manual) = 0x{status:02X} ({self.decode_status(status)})")
                print(f"QSPI WREN status-2 (manual) = 0x{status2:02X}")

                if status & 0x02:
                    return
            except Exception as e:
                print(f"Warning: manual WREN attempt failed: {e}")

        raise RuntimeError(f"Flash did not set WEL after WREN (all attempts) (RDSR=0x{status:02X}, RDSR2=0x{status2:02X})")

    def wait_ready(self, operation_name="Operation"):
        """Polls the flash RDSR (Status Register) until the WIP (Write In Progress) bit clears."""
        start = time.monotonic()
        last_dot = start
        try:
            while True:
                # Send RDSR + 1 dummy byte to clock out the response using the tested QSPI transfer path.
                response = self.transfer(bytes([FlashCmd.RDSR, 0x00]))
                if len(response) < 2:
                    raise RuntimeError(f"Unexpected RDSR response length: {len(response)}")
                status_byte = response[1]

                if not (status_byte & 0x01):  # Check WIP bit
                    if time.monotonic() - start >= 1:
                        print()
                    print(f"{operation_name} complete (RDSR=0x{status_byte:02X})")
                    break

                now = time.monotonic()
                if now - last_dot >= 1.0:
                    print('.', end='', flush=True)
                    last_dot = now

                time.sleep(0.1)
        except Exception as e:
            print(f"ERROR while waiting for flash ready during {operation_name}: {e}")
            traceback.print_exc()
            raise

    # =====================================================================
    # Flash data access
    # =====================================================================

    def read_flash_data(self, address: int, length: int) -> bytes:
        """Reads `length` bytes starting at `address`, falling back to Fast Read if Standard Read looks invalid."""
        if not (0 <= length <= 256):
            raise ValueError("Read length must be between 0 and 256 bytes")

        # Standard read command
        cmd = bytes([0x03]) + address.to_bytes(3, 'big') + bytes(length)
        response = self.transfer(cmd)
        data = response[4:]
        print(f" -> FLASH READ raw response = {response.hex()}")

        if len(data) == length and any(b != 0x00 for b in data):
            return data

        # Fallback to fast read if the standard read returns all zeros.
        print(" -> Standard FLASH read returned invalid data; retrying Fast Read (0x0B).")
        cmd = bytes([FlashCmd.FAST_READ]) + address.to_bytes(3, 'big') + b'\x00' + bytes(length)
        response = self.transfer(cmd)
        data = response[5:]
        print(f" -> FAST FLASH READ raw response = {response.hex()}")
        return data

    def verify_erased(self, address: int = 0, length: int = 64) -> bool:
        """Reads back a region and confirms every byte is 0xFF (the erased state)."""
        print(f" -> Verifying flash erase at 0x{address:06X} for {length} bytes...")
        data = self.read_flash_data(address, length)
        if all(b == 0xFF for b in data):
            print(" -> Erase verification passed: all bytes are 0xFF.")
            return True
        first_bad = next(i for i, b in enumerate(data) if b != 0xFF)
        bad_sample = data[first_bad:first_bad + 16]
        print(f" -> Erase verification FAILED at offset 0x{first_bad:02X}: {bad_sample.hex()}")
        return False

    # =====================================================================
    # Application-facing workflows (called by JtagController)
    # =====================================================================

    def read_jedec_id(self):
        """Reads and decodes the flash's JEDEC ID (manufacturer / type / capacity)."""
        print("Targeting ARM AHB-AP -> QSPI Controller -> Reading Flash JEDEC ID...")
        self.init_controller()
        self.debug_regs("QSPI Before JEDEC")

        # Use the proven manual FIFO transfer path for JEDEC ID reads.
        response = self.transfer(bytes([FlashCmd.RDID, 0x00, 0x00, 0x00]))
        if len(response) < 4:
            print(f"ERROR: JEDEC transfer returned unexpected length {len(response)}")
            return

        manuf_id = response[1]
        mem_type = response[2]
        mem_cap = response[3]
        print(f"RAW JEDEC ID   : {manuf_id:02X} {mem_type:02X} {mem_cap:02X}")
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

    def erase_chip(self):
        """Executes a full bulk erase of the entire QSPI Flash memory."""
        print("Initiating Full Chip Erase... (This may take several seconds)")
        try:
            self.init_controller()

            self.write_enable()
            status = self.read_status_register()
            print(f" -> Flash status after WREN = 0x{status:02X}")
            if not (status & 0x02):
                raise RuntimeError("Flash write enable failed before Chip Erase")

            print(" -> Sent Chip Erase command, waiting for flash to finish...")
            self.transfer(bytes([FlashCmd.CE]), expected_rx_len=0)
            self.wait_ready("Chip Erase")
            if not self.verify_erased(0, 64):
                raise RuntimeError("Erase completed, but flash content verification failed.")
            print("SUCCESS: Flash Chip completely erased.")
        except Exception as e:
            print(f"ERROR in erase_chip: {e}")
            traceback.print_exc()

    def erase_sector(self, offset: int):
        """Erases a single 64KB sector at the specified 24-bit address offset."""
        print(f"Erasing 64KB Sector at offset 0x{offset:06X}...")
        self.init_controller()

        self.write_enable()
        cmd = bytes([FlashCmd.SE]) + offset.to_bytes(3, 'big')
        self.transfer(cmd, expected_rx_len=0)
        self.wait_ready("Sector Erase")
        print(f"SUCCESS: Sector 0x{offset:06X} erased.")

    def write_binary_file(self, filepath: str = "bootblock.bin", start_offset: int = 0):
        """
        Uploads a binary file directly into the QSPI flash at the specified
        offset. Uses 128-byte chunks to safely fit within the internal Zynq
        TX FIFO. Note: this only *programs* bits from 1->0; the target
        sectors must already be erased (0xFF) beforehand.
        """
        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            print(f"ERROR: File '{filepath}' not found!")
            return

        print(f"Starting QSPI Flash Write: '{filepath}' ({len(data)} bytes) at offset 0x{start_offset:06X}")
        self.init_controller()

        # Zynq QSPI TX FIFO is 252 bytes. We use 128-byte chunks to guarantee
        # we never overrun the hardware buffer during a Page Program transaction.
        CHUNK_SIZE = 128
        t0 = time.time()

        for i in range(0, len(data), CHUNK_SIZE):
            chunk = data[i:i + CHUNK_SIZE]
            current_offset = start_offset + i

            self.write_enable()

            # 0x02 (Page Program) + 3-byte address + payload
            cmd = bytes([FlashCmd.PP]) + current_offset.to_bytes(3, 'big') + chunk
            self.transfer(cmd, expected_rx_len=0)
            self.wait_ready()

            progress = min(100.0, ((i + len(chunk)) / len(data)) * 100)
            print(f" -> Flashing... {progress:05.2f}% (Offset 0x{current_offset:06X})", end='\r')

        print(f"\nSUCCESS: Binary flashed in {time.time()-t0:.2f} seconds.")

    def enable_quad_mode(self):
        """Reads the flash's Status Registers and sets the Quad-Enable (QE) bit."""
        print("Targeting ARM AHB-AP -> QSPI Controller -> Enabling Quad SPI mode...")
        self.init_controller()

        # 1. Read current Status Register 1
        status1_raw = self.manual_transfer(bytes([FlashCmd.RDSR, 0x00, 0x00, 0x00]))
        status1 = status1_raw[1] if len(status1_raw) > 1 else 0x00

        # 2. Read Status Register 2 (0x35 is standard RDSR2 / Read Config Register)
        status2_raw = self.manual_transfer(bytes([FlashCmd.RDSR2, 0x00, 0x00, 0x00]))
        status2 = status2_raw[1] if len(status2_raw) > 1 else 0x00

        print(f"Current QSPI Status1 = 0x{status1:02X}, Status2 = 0x{status2:02X}")

        # Set WEL (Write Enable) first
        response_wren = self.manual_transfer(bytes([FlashCmd.WREN]), expected_rx_len=0)
        print(f"WREN raw response = 0x{response_wren.hex().upper()}")
        time.sleep(0.01)

        # Identify the flash so we can use the correct status register write form.
        jedec = self.transfer(bytes([FlashCmd.RDID, 0x00, 0x00, 0x00]))
        manuf_id = jedec[1] if len(jedec) > 1 else None
        print(f"Detected flash manufacturer ID for QE setup: 0x{manuf_id:02X}" if manuf_id is not None else "Unable to detect manufacturer ID for QE setup")

        # Enable QE bit. ISSI uses a single-byte WRSR write, while many Winbond/Macronix
        # chips accept a 2-byte WRSR write for SR1 + SR2.
        new_status1 = status1 | FlashCmd.QE_BIT
        new_status2 = status2 | 0x02

        if manuf_id == 0x9D:
            response_wrsr = self.manual_transfer(bytes([FlashCmd.WRSR, new_status1]), expected_rx_len=0)
        else:
            response_wrsr = self.manual_transfer(bytes([FlashCmd.WRSR, new_status1, new_status2]), expected_rx_len=0)
        print(f"WRSR raw response = 0x{response_wrsr.hex().upper()}")

        self.wait_ready("Write Status Register")

        # Verify
        check_sr1 = self.manual_transfer(bytes([FlashCmd.RDSR, 0x00, 0x00, 0x00]))[1]
        check_sr2 = self.manual_transfer(bytes([FlashCmd.RDSR2, 0x00, 0x00, 0x00]))[1]

        print(f"After WRSR: SR1 = 0x{check_sr1:02X}, SR2 = 0x{check_sr2:02X}")

        if (check_sr1 & 0x40) or (check_sr2 & 0x02):
            print("SUCCESS: Quad SPI mode enabled successfully!")
        else:
            print("Warning: QE bit not set in readback, proceeding with standard QSPI configuration...")

    def disable_quad_mode(self):
        """
        Diagnostic counterpart to enable_quad_mode(): clears the Quad-Enable
        (QE) bit. Useful to test whether QE is the root cause of a read
        failure - many SPI-NOR parts repurpose WP#/HOLD# as IO2/IO3 once QE
        is set, and if those pins are not driven/pulled correctly on the
        board for single-line (manual) transfers, reads can come back as
        all-zero even though the bus and command interface still work fine
        (RDSR etc. keep responding correctly).
        """
        print("Targeting ARM AHB-AP -> QSPI Controller -> Disabling Quad SPI mode...")
        self.init_controller()

        status1_raw = self.manual_transfer(bytes([FlashCmd.RDSR, 0x00, 0x00, 0x00]))
        status1 = status1_raw[1] if len(status1_raw) > 1 else 0x00
        status2_raw = self.manual_transfer(bytes([FlashCmd.RDSR2, 0x00, 0x00, 0x00]))
        status2 = status2_raw[1] if len(status2_raw) > 1 else 0x00
        print(f"Current QSPI Status1 = 0x{status1:02X}, Status2 = 0x{status2:02X}")

        self.manual_transfer(bytes([FlashCmd.WREN]), expected_rx_len=0)
        time.sleep(0.01)

        jedec = self.transfer(bytes([FlashCmd.RDID, 0x00, 0x00, 0x00]))
        manuf_id = jedec[1] if len(jedec) > 1 else None

        new_status1 = status1 & ~FlashCmd.QE_BIT
        new_status2 = status2 & ~0x02

        if manuf_id == 0x9D:
            response_wrsr = self.manual_transfer(bytes([FlashCmd.WRSR, new_status1]), expected_rx_len=0)
        else:
            response_wrsr = self.manual_transfer(bytes([FlashCmd.WRSR, new_status1, new_status2]), expected_rx_len=0)
        print(f"WRSR raw response = 0x{response_wrsr.hex().upper()}")

        self.wait_ready("Write Status Register")

        check_sr1 = self.manual_transfer(bytes([FlashCmd.RDSR, 0x00, 0x00, 0x00]))[1]
        check_sr2 = self.manual_transfer(bytes([FlashCmd.RDSR2, 0x00, 0x00, 0x00]))[1]
        print(f"After WRSR: SR1 = 0x{check_sr1:02X}, SR2 = 0x{check_sr2:02X}")

        if not (check_sr1 & 0x40) and not (check_sr2 & 0x02):
            print("SUCCESS: Quad SPI mode disabled.")
        else:
            print("Warning: QE bit still set in readback.")
