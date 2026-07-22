"""
JTAG CONTROLLER - FACADE / ORCHESTRATOR
========================================
This is the object the CLI (main.py) talks to. It is intentionally a THIN
layer: almost no hardware logic lives here. Its only job is to:
  1. Wire together the lower layers in order (transport -> tap -> dap ->
     soc / flash), and
  2. Expose one flat, easy-to-call method per CLI menu entry, doing the
     common "is the connection open?" guard once instead of repeating it
     in every method.

If you want to understand HOW something works, don't look here - follow
the chain down:
    jtag_controller.py  (this file, "what")
      -> zynq_soc.py / qspi_flash.py        (Zynq-specific workflows)
      -> coresight_dap.py                   (ARM CoreSight DAP / memory access)
      -> jtag_tap.py                        (generic JTAG TAP protocol)
      -> mpsse_transport.py                 (raw FTDI/MPSSE bytes)
"""

from mpsse_transport import MpsseTransport
from jtag_tap import JtagTap
from coresight_dap import CoreSightDap
from qspi_flash import QspiFlash
from zynq_gpio import ZynqGPIO
from zynq_soc import ZynqSoc


class JtagController:
    """
    Encapsulates the JTAG interface via FTDI MPSSE using ftd2xx backend.
    Composes the layered implementation; see module docstring above.
    """

    def __init__(self):
        self.transport = MpsseTransport()
        self.tap = JtagTap(self.transport)
        self.dap = CoreSightDap(self.tap)
        self.soc = ZynqSoc(self.dap)
        self.gpio = ZynqGPIO(self.dap, self.soc)
        self.qspi = QspiFlash(self.dap, self.soc, self.gpio)

    # -------------------------------------------------------------------
    # FTDI native interface & initialization
    # -------------------------------------------------------------------

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
        """Shared guard used by every workflow method below."""
        if not self.is_ready():
            print("JTAG is not open. Please open a connection first.")
            return False
        return True

    # -------------------------------------------------------------------
    # High-level Zynq & hardware workflows
    # -------------------------------------------------------------------

    def run_fsbl_bin(self, filepath: str = "fsbl.bin"):
        if not self._require_open():
            return
        self.soc.load_and_run_fsbl(filepath)

    def read_qspi_jedec_id(self):
        if not self._require_open():
            return
        self.qspi.read_jedec_id()

    def test_ocm_ram(self):
        if not self._require_open():
            return
        self.soc.test_ocm_ram()

    def read_fpga_usercode(self):
        if not self._require_open():
            return
        self.tap.read_fpga_usercode()

    # -------------------------------------------------------------------
    # Intermediate memory bus & CoreSight operations
    # -------------------------------------------------------------------

    def test_arm_dap(self):
        if not self._require_open():
            return
        self.dap.test_dap_handshake()

    def write_mem32(self, address: int, data: int):
        if not self._require_open():
            return
        self.dap.write_mem32(address, data)

    def read_mem32(self, address: int):
        if not self._require_open():
            return None
        return self.dap.read_mem32(address)

    def scan(self, max_devices: int = 8):
        if not self._require_open():
            return
        self.tap.scan_chain(max_devices)

    # -------------------------------------------------------------------
    # QSPI flash management
    # -------------------------------------------------------------------

    def erase_qspi_chip(self):
        if not self._require_open():
            return
        self.qspi.erase_chip()

    def erase_qspi_sector(self, offset: int = 0):
        if not self._require_open():
            return
        self.qspi.erase_sector(offset)

    def write_qspi_binary(self, filepath: str = "bootblock.bin", start_offset: int = 0):
        if not self._require_open():
            return
        self.qspi.write_binary_file(filepath, start_offset)

    def enable_qspi_quad_mode(self):
        if not self._require_open():
            return
        self.qspi.enable_quad_mode()

    def disable_qspi_quad_mode(self):
        if not self._require_open():
            return
        self.qspi.disable_quad_mode()
