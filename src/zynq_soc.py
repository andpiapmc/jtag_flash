"""
Zynq-7000 SoC Control.
Handles CPU0 halt/release, SLCR unlock/lock, OCM RAM access, and FSBL binary injection.
"""

import time
import struct
from zynq_constants import ZynqRegs, DEFAULT_FSBL_PATH


class ZynqSoc:
    def __init__(self, dap):
        self.dap = dap

    # -------------------------------------------------------------------
    # SLCR Unlock / Lock (Public API for other modules)
    # -------------------------------------------------------------------

    def slcr_unlock(self):
        self.dap.write_mem32(ZynqRegs.SLCR_UNLOCK_ADDR, ZynqRegs.SLCR_UNLOCK_KEY)

    def slcr_lock(self):
        self.dap.write_mem32(ZynqRegs.SLCR_LOCK_ADDR, ZynqRegs.SLCR_LOCK_KEY)

    def enable_peripheral_clock(self, clock_enable_mask: int):
        self.slcr_unlock()
        current = self.dap.read_mem32(ZynqRegs.APER_CLK_CTRL)
        if not (current & clock_enable_mask):
            self.dap.write_mem32(ZynqRegs.APER_CLK_CTRL, current | clock_enable_mask)
        self.slcr_lock()

    def enable_qspi_ref_clock(self):
        """Overwrites the whole register with the known-good value to prevent post-FSBL stalls."""
        self.slcr_unlock()
        self.dap.write_mem32(ZynqRegs.LQSPI_CLK_CTRL, ZynqRegs.LQSPI_CLK_CTRL_SAFE_VAL)
        self.slcr_lock()

    # -------------------------------------------------------------------
    # Internal Register & CPU Helpers (Private)
    # -------------------------------------------------------------------

    def _halt_cpu0(self) -> int:
        current_rst = self.dap.read_mem32(ZynqRegs.A9_CPU_RST_CTRL)
        self.dap.write_mem32(ZynqRegs.A9_CPU_RST_CTRL, current_rst | 0x01)
        return current_rst

    def _release_cpu0(self, previous_value: int):
        self.dap.write_mem32(ZynqRegs.A9_CPU_RST_CTRL, previous_value & ~0x01)

    # -------------------------------------------------------------------
    # Public Workflows
    # -------------------------------------------------------------------

    def load_and_run_fsbl(self, filepath: str = DEFAULT_FSBL_PATH):
        print(f"Targeting ARM AHB-AP -> Loading '{filepath}' into OCM...")

        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            print(f"ERROR: File '{filepath}' not found!")
            return

        self.dap.connect()
        self.slcr_unlock()

        print(" -> Halting CPU0...")
        current_rst = self._halt_cpu0()

        words = []
        for i in range(0, len(data), 4):
            chunk = data[i:i + 4].ljust(4, b'\x00')
            words.append(struct.unpack('<I', chunk)[0])

        print(f" -> Bulk writing {len(data)} bytes to OCM...")
        t0 = time.time()
        self.dap.write_mem32_bulk(ZynqRegs.OCM_BASE_ADDR, words)
        print(f" -> OCM Write completed in {time.time()-t0:.2f}s!")

        print(" -> Waking up CPU0...")
        self._release_cpu0(current_rst)
        self.slcr_lock()

        print(" -> FSBL running! Waiting 2s for hardware setup...")
        time.sleep(2)
        print("SUCCESS: Board is ready.")

    def test_ocm_ram(self):
        print("Targeting ARM AHB-AP -> Testing OCM Memory Access...")
        self.dap.connect()
        magic_word = 0xDEADBEEF

        self.dap.write_mem32(ZynqRegs.OCM_BASE_ADDR, magic_word)
        read_back = self.dap.read_mem32(ZynqRegs.OCM_BASE_ADDR)
        print(f"Read Value : 0x{read_back:08X}")
        
        if read_back == magic_word:
            print("SUCCESS: OCM memory is accessible!")
        else:
            print("ERROR: Memory write failed.")