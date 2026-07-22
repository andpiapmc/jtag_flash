"""
LAYER 2 - JTAG TAP PROTOCOL
===========================
Implements the generic IEEE 1149.1 TAP (Test Access Port) state machine
operations: shifting values into the Instruction Register (IR) and Data
Register (DR), and performing a blind chain scan to discover devices.

This layer is target-agnostic: it would work identically on any JTAG chain
(Zynq, a plain FPGA, a random microcontroller...). It knows about TAP
states and bit shifting, but nothing about what the shifted bits *mean*
(that interpretation lives in coresight_dap.py and above).

Built on top of: mpsse_transport.py
"""

import struct
import time
from zynq_constants import MpsseOpcodes, TmsCommands, JtagInstr, KNOWN_TAPS


class JtagTap:
    """Generic JTAG TAP state-machine operations, built on an MpsseTransport."""

    def __init__(self, transport):
        self.transport = transport

    # -------------------------------------------------------------------
    # Atomic IR/DR shifting - the fundamental JTAG primitive
    # -------------------------------------------------------------------

    def shift_bits(self, data_val: int, num_bits: int, is_ir: bool = False) -> int:
        """
        Shifts `num_bits` of `data_val` into the currently selected register
        (IR or DR) and returns whatever bits come back out on TDO.

        This is the single lowest-level "move bits through the TAP" routine;
        everything else (shift_ir, shift_dr, DAP transactions, ...) is built
        by calling this with the right register length and tap-move context.
        """
        payload = bytearray(TmsCommands.IDLE_TO_SHIFT_IR if is_ir else TmsCommands.IDLE_TO_SHIFT_DR)
        num_bytes, remaining_bits = (num_bits - 1) // 8, (num_bits - 1) % 8
        last_bit = (data_val >> (num_bits - 1)) & 0x01

        if num_bytes > 0:
            payload += MpsseOpcodes.SHIFT_BYTES_LSB_RW + struct.pack('<H', num_bytes - 1)
            payload += (data_val & ((1 << (num_bytes * 8)) - 1)).to_bytes(num_bytes, 'little')
        if remaining_bits > 0:
            payload += MpsseOpcodes.SHIFT_BITS_LSB_RW + struct.pack('<B', remaining_bits - 1)
            payload += struct.pack('<B', (data_val >> (num_bytes * 8)) & 0xFF)

        # Last bit is clocked out together with the TMS transition that exits
        # the Shift-IR/DR state (JTAG requires TMS=1 on the final shifted bit).
        tms_byte = 0x01 | (last_bit << 7)
        payload += MpsseOpcodes.SHIFT_TMS_READ + b'\x00' + struct.pack('<B', tms_byte)
        payload += TmsCommands.EXIT_TO_IDLE + MpsseOpcodes.SEND_IMMEDIATE
        self.transport.write(bytes(payload))

        expected_rx_len = num_bytes + (1 if remaining_bits > 0 else 0) + 1
        rx_data = self.transport.read(expected_rx_len)
        rx_val = 0

        if len(rx_data) == expected_rx_len:
            idx = 0
            if num_bytes > 0:
                rx_val = int.from_bytes(rx_data[0:num_bytes], 'little')
                idx += num_bytes
            if remaining_bits > 0:
                rx_val |= ((rx_data[idx] >> (8 - remaining_bits)) << (num_bytes * 8))
                idx += 1
            rx_val |= (((rx_data[idx] >> 7) & 0x01) << (num_bits - 1))
        return rx_val

    def shift_ir(self, instruction: int, tap_index: int):
        """
        Shifts an instruction into the IR of a specific TAP in the chain.
        `tap_index` selects between the two Zynq TAPs: 0 = FPGA/PL TAP
        (6-bit IR), 1 = ARM CoreSight DAP TAP (4-bit IR). The other TAP in
        the chain is padded with BYPASS bits so it stays transparent.
        """
        shift_value = (instruction << 6) | 0x3F if tap_index == 1 else (0x0F << 6) | instruction
        self.shift_bits(shift_value, 10, is_ir=True)

    def shift_dr(self, data_val: int, dr_len: int, tap_index: int) -> int:
        """
        Shifts `dr_len` bits into the DR of a specific TAP, padding for the
        other (bypassed) TAP in the chain, and returns the bits read back.
        """
        shift_value = (data_val << 1) | 0x01 if tap_index == 1 else (0x01 << dr_len) | data_val
        rx_val = self.shift_bits(shift_value, dr_len + 1, is_ir=False)
        return (rx_val >> 1) & ((1 << dr_len) - 1) if tap_index == 1 else rx_val & ((1 << dr_len) - 1)

    # -------------------------------------------------------------------
    # Chain-level operations
    # -------------------------------------------------------------------

    def scan_chain(self, max_devices: int = 8):
        """
        Executes a JTAG blind chain discovery: forces Reset, jumps straight
        into Shift-DR (which shifts out each TAP's IDCODE, since BYPASS/IDCODE
        is the default instruction after reset) and reads back IDCODEs.
        """
        try:
            print("Scanning JTAG chain (Blind Scan)...")
            self.transport.purge_rx()
            mpsse_payload = bytearray(TmsCommands.RESET + TmsCommands.TO_SHIFT_DR)
            bytes_to_read = max_devices * 4
            mpsse_payload += MpsseOpcodes.READ_DATA_BYTES_LSB + struct.pack('<H', bytes_to_read - 1)
            mpsse_payload += TmsCommands.TO_IDLE + MpsseOpcodes.SEND_IMMEDIATE
            self.transport.write(bytes(mpsse_payload))
            time.sleep(0.01)

            rx_data = self.transport.read(bytes_to_read)
            if len(rx_data) == bytes_to_read:
                print("-" * 70)
                print(f"{'TAP':<5} | {'RAW IDCODE':<10} | {'DEVICE DESCRIPTION'}")
                print("-" * 70)
                tap_count = 0
                for i in range(max_devices):
                    idcode = struct.unpack('<I', rx_data[i*4:(i+1)*4])[0]
                    if idcode == 0xFFFFFFFF:
                        break
                    if (idcode & 0x01) == 0:  # LSB=1 marks a valid IDCODE (vs. BYPASS's single 0 bit)
                        continue
                    tap_count += 1
                    device_name = KNOWN_TAPS.get(idcode & 0x0FFFFFFF, "Unknown Device")
                    print(f"{i:<5} | 0x{idcode:08X} | {device_name}")
                print("-" * 70)
                print(f"Total devices found: {tap_count}")
        except Exception as e:
            print(f"Error during scan: {e}")

    def read_fpga_usercode(self):
        """Queries the programmable logic (PL) boundary TAP for its USERCODE register."""
        print("Targeting FPGA TAP -> Reading USERCODE...")
        self.transport.reset_tap_to_idle()
        self.shift_ir(JtagInstr.FPGA_USERCODE, tap_index=0)
        print(f"FPGA USERCODE: 0x{self.shift_dr(0x00000000, 32, 0):08X}")
