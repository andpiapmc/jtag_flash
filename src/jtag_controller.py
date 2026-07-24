"""
Facade orchestrator for the Zynq JTAG tools.
Wires lower layers (Transport -> TAP -> DAP -> SoC -> Flash) and exposes flat methods for CLI.
"""

from mpsse_transport import MpsseTransport
from jtag_tap import JtagTap
from coresight_dap import CoreSightDap
from qspi_flash import QspiFlash
from zynq_soc import ZynqSoc
from zynq_constants import DEFAULT_FSBL_PATH, DEFAULT_BOOTBLOCK_PATH


class JtagController:
    """High-level facade class for all JTAG and QSPI programming actions."""

    def __init__(self):
        self.transport = MpsseTransport()
        self.tap = JtagTap(self.transport)
        self.dap = CoreSightDap(self.tap)
        self.soc = ZynqSoc(self.dap)
        self.qspi = QspiFlash(self.dap, self.soc)

    @staticmethod
    def list_ftdi_devices() -> None:
        """Lists connected FTDI devices."""
        MpsseTransport.list_devices()

    def is_ready(self) -> bool:
        """Checks if the transport handle is active."""
        return self.transport.is_ready()

    def open(self, device_index: int = 0, freq_hz: int = 15_000_000) -> None:
        """Opens connection to the selected FTDI endpoint."""
        self.transport.open(device_index, freq_hz)

    def close(self) -> None:
        """Closes connection to the FTDI endpoint."""
        self.transport.close()

    def _require_open(self) -> bool:
        """Internal guard ensuring JTAG connection is established."""
        if not self.is_ready():
            print("JTAG is not open. Please open a connection first.")
            return False
        return True

    def run_fsbl_bin(self, filepath: str = DEFAULT_FSBL_PATH) -> None:
        """Loads and executes First Stage Bootloader in OCM RAM."""
        if self._require_open():
            self.soc.load_and_run_fsbl(filepath)

    def read_qspi_jedec_id(self) -> None:
        """Reads QSPI flash JEDEC ID."""
        if self._require_open():
            self.qspi.read_jedec_id()

    def test_ocm_ram(self) -> None:
        """Tests OCM RAM read/write access."""
        if self._require_open():
            self.soc.test_ocm_ram()

    def read_fpga_usercode(self) -> None:
        """Reads FPGA USERCODE from PL TAP."""
        if self._require_open():
            self.tap.read_fpga_usercode()

    def test_arm_dap(self) -> None:
        """Tests ARM CoreSight DAP handshake."""
        if self._require_open():
            self.dap.test_dap_handshake()

    def write_mem32(self, address: int, data: int) -> None:
        """Writes a 32-bit word to system memory."""
        if self._require_open():
            self.dap.write_mem32(address, data)

    def read_mem32(self, address: int) -> int | None:
        """Reads a 32-bit word from system memory."""
        if self._require_open():
            return self.dap.read_mem32(address)
        return None

    def scan(self, max_devices: int = 8) -> None:
        """Scans the JTAG chain."""
        if self._require_open():
            self.tap.scan_chain(max_devices)

    def erase_qspi_chip(self) -> None:
        """Erases the full SPI flash memory."""
        if self._require_open():
            self.qspi.erase_chip()

    def erase_qspi_sector(self, offset: int = 0) -> None:
        """Erases a specific 64KB sector."""
        if self._require_open():
            self.qspi.erase_sector(offset)

    def write_qspi_binary(self, filepath: str = DEFAULT_BOOTBLOCK_PATH, start_offset: int = 0) -> None:
        """Flashes a binary file to QSPI memory."""
        if self._require_open():
            self.qspi.write_binary_file(filepath, start_offset)

    def enable_qspi_quad_mode(self) -> None:
        """Enables Quad SPI Mode."""
        if self._require_open():
            self.qspi.enable_quad_mode()

    def disable_qspi_quad_mode(self) -> None:
        """Disables Quad SPI Mode."""
        if self._require_open():
            self.qspi.disable_quad_mode()