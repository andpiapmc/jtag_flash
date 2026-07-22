"""
ARM CoreSight Debug Access Port (DAP) Driver.
Handles DPACC/APACC 35-bit transactions and AHB-AP system memory window access.
"""

import struct
from zynq_constants import JtagInstr, CoreSightRegs, DapReq, AhbApRegs, TmsCommands, MpsseOpcodes


class CoreSightDap:
    def __init__(self, tap):
        self.tap = tap

    # -------------------------------------------------------------------
    # Low-level DPACC / APACC Transactions (Private)
    # -------------------------------------------------------------------

    def _dap_write(self, is_ap: bool, a32: int, data: int) -> int:
        """Frame layout (35 bits): [34:3 Data] | [2:1 Addr A32] | [0 RnW]."""
        ir = JtagInstr.DAP_APACC if is_ap else JtagInstr.DAP_DPACC
        self.tap.shift_ir(ir, tap_index=1)
        req = (data << 3) | (a32 << 1) | DapReq.WRITE
        return self.tap.shift_dr(req, dr_len=DapReq.SHIFT_LEN, tap_index=1) & DapReq.ACK_MASK

    def _dap_read(self, is_ap: bool, a32: int):
        """
        ARM Debug reads are pipelined: 
        1st shift requests data, 2nd shift retrieves result.
        """
        ir = JtagInstr.DAP_APACC if is_ap else JtagInstr.DAP_DPACC
        self.tap.shift_ir(ir, tap_index=1)
        req = (0 << 3) | (a32 << 1) | DapReq.READ
        
        self.tap.shift_dr(req, dr_len=DapReq.SHIFT_LEN, tap_index=1)  # Request
        rx_val = self.tap.shift_dr(req, dr_len=DapReq.SHIFT_LEN, tap_index=1)  # Retrieve
        return (rx_val >> 3) & 0xFFFFFFFF, rx_val & DapReq.ACK_MASK

    def _init_ahb_ap(self):
        """Powers up debug domains and configures AHB-AP CSW register."""
        self._dap_write(is_ap=False, a32=CoreSightRegs.DP_CTRL_STAT, data=DapReq.PWRUP_REQ)
        self._dap_write(is_ap=False, a32=CoreSightRegs.DP_SELECT,    data=0x00000000)
        self._dap_write(is_ap=True,  a32=CoreSightRegs.AP_CSW,       data=AhbApRegs.CSW_DEFAULT_32BIT)

    # -------------------------------------------------------------------
    # Memory Access API
    # -------------------------------------------------------------------

    def connect(self):
        """Resets TAP and brings up the AHB-AP memory window."""
        self.tap.transport.reset_tap_to_idle()
        self._init_ahb_ap()

    def clear_sticky_errors(self):
        """Clears DP_ABORT error flags reusing the currently loaded IR."""
        req_abort = (DapReq.CLEAR_ERR << 3) | (CoreSightRegs.DP_ABORT << 1) | DapReq.WRITE
        self.tap.shift_dr(req_abort, dr_len=DapReq.SHIFT_LEN, tap_index=1)

    def write_mem32(self, address: int, data: int):
        """Writes a 32-bit word to a physical memory address via AHB-AP."""
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=address)
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_DRW, data=data)

    def read_mem32(self, address: int) -> int:
        """Reads a 32-bit word from a physical memory address via AHB-AP."""
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=address)
        return self._dap_read(is_ap=True, a32=CoreSightRegs.AP_DRW)[0]

    def write_mem32_bulk(self, start_address: int, words: list):
        """
        Fast bulk memory write bypassing standard shift_dr.
        Streams raw MPSSE packets over USB, relying on AHB-AP TAR auto-increment.
        """
        self._dap_write(is_ap=True, a32=CoreSightRegs.AP_TAR, data=start_address)
        self.tap.shift_ir(JtagInstr.DAP_APACC, tap_index=1)

        batch_size = 800
        for i in range(0, len(words), batch_size):
            batch = words[i:i + batch_size]
            payload = bytearray()
            
            for w in batch:
                req = (w << 3) | (CoreSightRegs.AP_DRW << 1)
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
        """Asserts line handshakes with ARM DAP and verifies ACKs."""
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

        # Extract status data
        rx_val = self.tap.shift_dr(req_status, dr_len=DapReq.SHIFT_LEN, tap_index=1)
        ack_ctrl = rx_val & DapReq.ACK_MASK
        print(f"CTRL/STAT ACK  : 0x{ack_ctrl:02X} [{ack_labels.get(ack_ctrl, 'INVALID/NO-ACK')}]")