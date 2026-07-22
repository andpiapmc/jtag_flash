"""
IEEE 1149.1 JTAG TAP Protocol Driver.
Handles IR/DR shifting and blind JTAG chain scanning.
"""

import struct
import time
from zynq_constants import MpsseOpcodes, TmsCommands, JtagInstr, KNOWN_TAPS


class JtagTap:
    def __init__(self, transport):
        self.transport = transport

    # -------------------------------------------------------------------
    # Atomic IR/DR Shifting (Private)
    # -------------------------------------------------------------------

    def _shift_bits(self, data_val: int, num_bits: int, is_ir: bool = False) -> int:
        """
        Shifts `num_bits` into the current register (IR/DR) and reads TDO back.
        Exits the Shift state on the final bit via TMS=1.
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

        # Last bit is clocked out together with TMS transition to Exit-DR/IR
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

    # -------------------------------------------------------------------
    # High-level TAP Register Operations
    # -------------------------------------------------------------------

    def shift_ir(self, instruction: int, tap_index: int):
        """
        Shifts an instruction into a specific TAP (0 = FPGA/PL 6-bit IR, 1 = ARM 4-bit IR).
        Pads the inactive TAP with BYPASS bits.
        """
        shift_value = (instruction << 6) | 0x3F if tap_index == 1 else (0x0F << 6) | instruction
        self._shift_bits(shift_value, 10, is_ir=True)

    def shift_dr(self, data_val: int, dr_len: int, tap_index: int) -> int:
        """Shifts data into a specific TAP DR, padding for the bypassed TAP."""
        shift_value = (data_val << 1) | 0x01 if tap_index == 1 else (0x01 << dr_len) | data_val
        rx_val = self._shift_bits(shift_value, dr_len + 1, is_ir=False)
        return (rx_val >> 1) & ((1 << dr_len) - 1) if tap_index == 1 else rx_val & ((1 << dr_len) - 1)

    # -------------------------------------------------------------------
    # Chain Discovery & Diagnostics
    # -------------------------------------------------------------------

    def scan_chain(self, max_devices: int = 8):
        """Performs a blind scan of the JTAG chain and prints identified devices."""
        try:
            print("Scanning JTAG chain...")
            self.transport.purge_rx()
            
            bytes_to_read = max_devices * 4
            payload = bytearray(TmsCommands.RESET + TmsCommands.TO_SHIFT_DR)
            payload += MpsseOpcodes.READ_DATA_BYTES_LSB + struct.pack('<H', bytes_to_read - 1)
            payload += TmsCommands.TO_IDLE + MpsseOpcodes.SEND_IMMEDIATE
            
            self.transport.write(bytes(payload))
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
                    if (idcode & 0x01) == 0:  # LSB must be 1 for a valid IDCODE
                        continue
                    
                    tap_count += 1
                    device_name = KNOWN_TAPS.get(idcode & 0x0FFFFFFF, "Unknown Device")
                    print(f"{i:<5} | 0x{idcode:08X} | {device_name}")
                    
                print("-" * 70)
                print(f"Total devices found: {tap_count}")
        except Exception as e:
            print(f"Error during scan: {e}")

    def read_fpga_usercode(self):
        """Reads the USERCODE register from the FPGA/PL TAP."""
        print("Targeting FPGA TAP -> Reading USERCODE...")
        self.transport.reset_tap_to_idle()
        self.shift_ir(JtagInstr.FPGA_USERCODE, tap_index=0)
        print(f"FPGA USERCODE: 0x{self.shift_dr(0x00000000, 32, 0):08X}")