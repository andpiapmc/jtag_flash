"""
Facade orchestrator for the JTAG tools.
Wires together the lower layers (transport -> tap -> dap -> soc -> flash)
and exposes flat methods for the CLI.
"""

from mpsse_transport import MpsseTransport
from jtag_tap import JtagTap
from coresight_dap import CoreSightDap
from qspi_flash import QspiFlash
from zynq_gpio import ZynqGPIO
from zynq_soc import ZynqSoc


class JtagController:
    def __init__(self):
        self.transport = MpsseTransport()
        self.tap = JtagTap(self.transport)
        self.dap = CoreSightDap(self.tap)
        self.soc = ZynqSoc(self.dap)
        self.gpio = ZynqGPIO(self.dap, self.soc)
        self.qspi = QspiFlash(self.dap, self.soc, self.gpio)

    @staticmethod
    def list_ftdi_devices():
        MpsseTransport.list_devices()

    def is_ready(self) -> bool:
        return self.transport.is_ready()

    def open(self, device_index: int = 0, freq_hz: int = 1_000_000):
        self.transport.open(device_index, freq_hz)

    def close(self):
        self.transport.close()

    def _require_open(self) -> bool:
        if not self.is_ready():
            print("JTAG is not open. Please open a connection first.")
            return False
        return True

    def run_fsbl_bin(self, filepath: str = "fsbl.bin"):
        if self._require_open():
            self.soc.load_and_run_fsbl(filepath)

    def read_qspi_jedec_id(self):
        if self._require_open():
            self.qspi.read_jedec_id()

    def test_ocm_ram(self):
        if self._require_open():
            self.soc.test_ocm_ram()

    def read_fpga_usercode(self):
        if self._require_open():
            self.tap.read_fpga_usercode()

    def test_arm_dap(self):
        if self._require_open():
            self.dap.test_dap_handshake()

    def write_mem32(self, address: int, data: int):
        if self._require_open():
            self.dap.write_mem32(address, data)

    def read_mem32(self, address: int):
        if self._require_open():
            return self.dap.read_mem32(address)
        return None

    def scan(self, max_devices: int = 8):
        if self._require_open():
            self.tap.scan_chain(max_devices)

    def erase_qspi_chip(self):
        if self._require_open():
            self.qspi.erase_chip()

    def erase_qspi_sector(self, offset: int = 0):
        if self._require_open():
            self.qspi.erase_sector(offset)

    def write_qspi_binary(self, filepath: str = "bootblock.bin", start_offset: int = 0):
        if self._require_open():
            self.qspi.write_binary_file(filepath, start_offset)

    def enable_qspi_quad_mode(self):
        if self._require_open():
            self.qspi.enable_quad_mode()

    def disable_qspi_quad_mode(self):
        if self._require_open():
            self.qspi.disable_quad_mode()