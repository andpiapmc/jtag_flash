"""
LAYER 4a - ZYNQ-7000 SoC CONTROL
=================================
Zynq-specific operations that only need plain 32-bit memory read/write via
the AHB-AP: halting/releasing the Cortex-A9 CPU0, unlocking the SLCR
(System Level Control Registers) to touch clocking/reset lines, injecting a
First Stage Boot Loader (FSBL) binary into On-Chip Memory (OCM) and kicking
it off, and a basic OCM sanity test.

Built on top of: coresight_dap.py (uses it purely as a "read/write memory"
interface - this file has no JTAG-level knowledge at all).
"""

import time
import struct
from zynq_constants import ZynqRegs


class ZynqSoc:
    """Zynq-7000 CPU/SLCR/OCM control, built on a CoreSightDap memory window."""

    def __init__(self, dap):
        self.dap = dap

    # -------------------------------------------------------------------
    # SLCR (System Level Control Registers) unlock/lock
    # -------------------------------------------------------------------

    def slcr_unlock(self):
        """Unlocks the SLCR so clocking/reset registers become writable."""
        self.dap.write_mem32(ZynqRegs.SLCR_UNLOCK_ADDR, ZynqRegs.SLCR_UNLOCK_KEY)

    def slcr_lock(self):
        """Re-locks the SLCR."""
        self.dap.write_mem32(ZynqRegs.SLCR_LOCK_ADDR, ZynqRegs.SLCR_LOCK_KEY)

    # -------------------------------------------------------------------
    # CPU0 halt / release
    # -------------------------------------------------------------------

    def halt_cpu0(self) -> int:
        """Asserts CPU0 reset. Returns the previous register value so the
        caller can restore the other reset bits unchanged when releasing."""
        current_rst = self.dap.read_mem32(ZynqRegs.A9_CPU_RST_CTRL)
        self.dap.write_mem32(ZynqRegs.A9_CPU_RST_CTRL, current_rst | 0x01)
        return current_rst

    def release_cpu0(self, previous_value: int):
        """De-asserts CPU0 reset, restoring the other bits from `previous_value`."""
        self.dap.write_mem32(ZynqRegs.A9_CPU_RST_CTRL, previous_value & ~0x01)

    # -------------------------------------------------------------------
    # High-level workflows
    # -------------------------------------------------------------------

    def load_and_run_fsbl(self, filepath: str = "fsbl.bin"):
        """
        Halts Core0, injects the First Stage Boot Loader (FSBL) into OCM via
        bulk transfers, and wakes up the core to initialize execution.
        """
        print(f"Targeting ARM AHB-AP -> Loading '{filepath}' into OCM...")

        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            print(f"ERROR: File '{filepath}' not found!")
            return

        self.dap.connect()

        # Unlock SLCR to take control of clocking/reset lines
        self.slcr_unlock()

        print(" -> Halting CPU0 (Reset)...")
        current_rst = self.halt_cpu0()

        words = []
        for i in range(0, len(data), 4):
            chunk = data[i:i + 4]
            if len(chunk) < 4:
                chunk += b'\x00' * (4 - len(chunk))
            words.append(struct.unpack('<I', chunk)[0])

        print(f" -> Executing Bulk Write of {len(data)} bytes...")
        t0 = time.time()
        self.dap.write_mem32_bulk(ZynqRegs.OCM_BASE_ADDR, words)
        print(f" -> OCM Write completed in {time.time()-t0:.2f} seconds!")

        print(" -> Waking up CPU0...")
        self.release_cpu0(current_rst)
        self.slcr_lock()

        print(" -> FSBL is running! Waiting 2 seconds for hardware setup...")
        time.sleep(2)
        print("SUCCESS: Board is ready.")

    def test_ocm_ram(self):
        """Verifies read/write access directly into the raw On-Chip RAM space."""
        print("Targeting ARM AHB-AP -> Testing OCM Memory Access...")
        self.dap.connect()
        magic_word = 0xDEADBEEF

        self.dap.write_mem32(ZynqRegs.OCM_BASE_ADDR, magic_word)
        read_back = self.dap.read_mem32(ZynqRegs.OCM_BASE_ADDR)
        print(f"Read Value : 0x{read_back:08X}")
        print("SUCCESS: OCM memory is accessible!" if read_back == magic_word else "ERROR: Memory write failed.")
