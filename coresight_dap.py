"""
LAYER 3 - ARM CORESIGHT DEBUG ACCESS PORT (DAP)
================================================
Implements ARM CoreSight DPACC/APACC transactions: the protocol ARM debug
hardware uses on top of plain JTAG to read/write the Debug Port (DP) and,
through the AHB-AP (Advanced High-performance Bus Access Port), arbitrary
system memory addresses.

Anyone learning ARM debug internals should be able to read *only* this file
(plus the JTAG primitives in jtag_tap.py) to understand:
  - how a DPACC/APACC 35-bit request is framed (data | address | RnW),
  - how the AHB-AP is configured (CSW/TAR/DRW) to become a "memory window",
  - how a single physical address read/write becomes two JTAG DR shifts.

Built on top of: jtag_tap.py
"""

import struct
from zynq_constants import JtagInstr, CoreSightRegs, DapReq, AhbApRegs, TmsCommands, MpsseOpcodes


class CoreSightDap:
    """ARM DAP transactions and AHB-AP memory access, built on a JtagTap."""

    def __init__(self, tap):
        self.tap = tap

    # -------------------------------------------------------------------
    # Raw DPACC / APACC transactions
    # -------------------------------------------------------------------

    def dap_write(self, is_ap: bool, a32: int, data: int) -> int:
        """
        Performs one DPACC (Debug Port) or APACC (Access Port) write.
        Frame layout (35 bits): [34:3 [Data] | [2:1 Address A[3:2]] | [0 RnW]].
        Returns the 3-bit ACK response (see DapReq.ACK_MASK).
        """
        ir = JtagInstr.DAP_APACC if is_ap else JtagInstr.DAP_DPACC
        self.tap.shift_ir(ir, tap_index=1)
        req = (data << 3) | (a32 << 1) | DapReq.WRITE
        return self.tap.shift_dr(req, dr_len=DapReq.SHIFT_LEN, tap_index=1) & DapReq.ACK_MASK

    def dap_read(self, is_ap: bool, a32: int):
        """
        Performs one DPACC/APACC read. ARM debug reads are pipelined: the
        first shift *requests* the read, the second shift *retrieves* the
        result of the previous request. Returns (value, ack).
        """
        ir = JtagInstr.DAP_APACC if is_ap else JtagInstr.DAP_DPACC
        self.tap.shift_ir(ir, tap_index=1)
        req = (0 << 3) | (a32 << 1) | DapReq.READ
        self.tap.shift_dr(req, dr_len=DapReq.SHIFT_LEN, tap_index=1)  # First shift initiates read
        rx_val = self.tap.shift_dr(req, dr_len=DapReq.SHIFT_LEN, tap_index=1)  # Second shift extracts data
        return (rx_val >> 3) & 0xFFFFFFFF, rx_val & DapReq.ACK_MASK

    def clear_sticky_errors(self):
        """
        Clears WDATAERR/STICKYERR/STICKYCMP/STICKYORUN via a DP_ABORT write.
        Note: unlike dap_write(), this does NOT re-select the DPACC
        instruction first - it deliberately reuses whatever IR is currently
        loaded (normally APACC, since it is called right after AP register
        writes) to save a JTAG IR-shift on a hot path. This matches how
        DP_ABORT was accessed in the original tested implementation.
        """
        req_abort = (DapReq.CLEAR_ERR << 3) | (CoreSightRegs.DP_ABORT << 1) | DapReq.WRITE
        self.tap.shift_dr(req_abort, dr_len=DapReq.SHIFT_LEN, tap_index=1)

    # -------------------------------------------------------------------
    # AHB-AP bring-up and memory access
    # -------------------------------------------------------------------

    def connect(self):
        """
        Standard bring-up sequence to get a usable "memory window" into the
        target: reset the TAP, power up the debug domains, then configure
        the AHB-AP for 32-bit single-increment transfers.
        """
        self.tap.transport.reset_tap_to_idle()
        self.init_ahb_ap()

    def init_ahb_ap(self):
        """Powers up the debug domains and configures the AHB-AP CSW register."""
        self.dap_write(is_ap=False, a32=CoreSightRegs.DP_CTRL_STAT, data=DapReq.PWRUP_REQ)
        self.dap_write(is_ap=False, a32=CoreSightRegs.DP_SELECT,    data=0x00000000)
        self.dap_write(is_ap=True,  a32=CoreSightRegs.AP_CSW,       data=AhbApRegs.CSW_DEFAULT_32BIT)

    def write_mem32(self, address: int, data: int):
        """Writes a discrete 32-bit word directly to an absolute physical address via AHB-AP."""
        self.dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=address)
        self.dap_write(is_ap=True, a32=CoreSightRegs.AP_DRW, data=data)

    def read_mem32(self, address: int) -> int:
        """Reads a discrete 32-bit word directly from an absolute physical address via AHB-AP."""
        self.dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=address)
        return self.dap_read(is_ap=True, a32=CoreSightRegs.AP_DRW)[0]

    def write_mem32_bulk(self, start_address: int, words: list):
        """
        Fast path for writing many consecutive 32-bit words (e.g. loading a
        whole FSBL binary into OCM). Bypasses the generic shift_dr() helper
        and builds raw MPSSE payloads directly, batching hundreds of JTAG
        transactions into a single USB write to maximize throughput.
        AP_TAR auto-increments (CSW AddrInc=Single), so we only set the
        target address once and then stream DRW writes.
        """
        self.dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=start_address)
        self.tap.shift_ir(JtagInstr.DAP_APACC, tap_index=1)

        batch_size = 800
        for i in range(0, len(words), batch_size):
            batch = words[i:i + batch_size]
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

            self.tap.transport.write(bytes(payload))
            self.tap.transport.purge_rx()

    # -------------------------------------------------------------------
    # Diagnostics
    # -------------------------------------------------------------------

    def test_dap_handshake(self):
        """Initializes and asserts line handshakes with the ARM Debug Access Port, printing ACKs."""
        print("Targeting ARM DAP -> CoreSight Initialization...")
        self.tap.transport.reset_tap_to_idle()

        ack_labels = {0x01: "WAIT", 0x02: "OK", 0x04: "FAULT"}

        self.tap.shift_ir(JtagInstr.DAP_IDCODE, tap_index=1)
        print(f"ARM IDCODE     : 0x{self.tap.shift_dr(0x00000000, 32, 1):08X}")

        self.tap.shift_ir(JtagInstr.DAP_DPACC, tap_index=1)

        # Clear sticky errors
        req_abort = (DapReq.CLEAR_ERR << 3) | (CoreSightRegs.DP_ABORT << 1) | DapReq.WRITE
        self.tap.shift_dr(req_abort, dr_len=DapReq.SHIFT_LEN, tap_index=1)

        # Power up debug domains
        req_pwrup = (DapReq.PWRUP_REQ << 3) | (CoreSightRegs.DP_CTRL_STAT << 1) | DapReq.WRITE
        ack_abort = self.tap.shift_dr(req_pwrup, dr_len=DapReq.SHIFT_LEN, tap_index=1) & DapReq.ACK_MASK
        print(f"ABORT ACK      : 0x{ack_abort:02X} [{ack_labels.get(ack_abort, 'INVALID/NO-ACK')}]")

        # Read back status
        req_status = (0x00000000 << 3) | (CoreSightRegs.DP_CTRL_STAT << 1) | DapReq.READ
        ack_pwrup = self.tap.shift_dr(req_status, dr_len=DapReq.SHIFT_LEN, tap_index=1) & DapReq.ACK_MASK
        print(f"PWRUP ACK      : 0x{ack_pwrup:02X} [{ack_labels.get(ack_pwrup, 'INVALID/NO-ACK')}]")

        # Extract status data (pipelined read - see dap_read())
        rx_val = self.tap.shift_dr(req_status, dr_len=DapReq.SHIFT_LEN, tap_index=1)
        ack_ctrl = rx_val & DapReq.ACK_MASK
        print(f"CTRL/STAT ACK  : 0x{ack_ctrl:02X} [{ack_labels.get(ack_ctrl, 'INVALID/NO-ACK')}]")
