"""
FTDI MPSSE Low-Level Transport Driver.
Handles USB endpoint communication with FT232H/FT2232H hardware.
"""

import time
import struct
import ftd2xx as ftd
from zynq_constants import MpsseOpcodes, TmsCommands


class MpsseTransport:
    def __init__(self):
        self.device = None  # Active ftd2xx handle

    # -------------------------------------------------------------------
    # Device Discovery & Lifecycle
    # -------------------------------------------------------------------

    @staticmethod
    def list_devices():
        print("Scanning for FTDI devices...")
        try:
            num_devices = ftd.createDeviceInfoList()
            if num_devices == 0:
                print("No FTDI devices detected.")
                return
            print(f"Found {num_devices} FTDI endpoint(s):")
            for i in range(num_devices):
                detail = ftd.getDeviceInfoDetail(i)
                desc = detail.get('description', b'Unknown').decode('utf-8', errors='ignore')
                serial = detail.get('serial', b'Unknown').decode('utf-8', errors='ignore')
                print(f"{i}: {desc} (Serial: {serial})")
        except Exception as e:
            print(f"Error scanning FTDI devices: {e}")

    def is_ready(self) -> bool:
        if self.device is None:
            return False
        try:
            self.device.getQueueStatus()
            return True
        except Exception:
            return False

    def open(self, device_index: int = 0, freq_hz: int = 1_000_000):
        if self.is_ready():
            print("JTAG is already open.")
            return

        print("Initializing FTDI MPSSE JTAG interface...")
        try:
            self.device = ftd.open(device_index)
            self.device.setBitMode(0x00, 0)
            time.sleep(0.05)
            self.device.setBitMode(0x0B, 2)  # Active MPSSE Mode
            time.sleep(0.05)

            self.device.setUSBParameters(65536, 65536)
            self.device.setTimeouts(1000, 1000)
            self.device.setLatencyTimer(16)
            self.device.purge(ftd.defines.PURGE_RX | ftd.defines.PURGE_TX)

            # Build hardware setup buffer
            setup = bytearray()
            setup += MpsseOpcodes.DISABLE_CLK_DIV5
            setup += MpsseOpcodes.TURN_OFF_ADAPTIVE_CLK
            setup += MpsseOpcodes.DISABLE_3_PHASE_CLK
            setup += MpsseOpcodes.SET_DATA_BITS_LOW + b'\x88\xFB'
            setup += MpsseOpcodes.SET_DATA_BITS_HIGH + b'\x00\x00'
            setup += MpsseOpcodes.SET_TCK_DIVISOR + struct.pack('<H', self._calculate_divisor(freq_hz))
            self.device.write(bytes(setup))

            if not self._check_chain_alive():
                self.close()
                return

            print(f"FTDI connected. TCK speed: ~{freq_hz / 1e6:.1f} MHz.")
        except Exception as e:
            print(f"Error initializing FTDI: {e}")
            self.device = None

    def close(self):
        if self.is_ready():
            try:
                self.device.write(MpsseOpcodes.SET_DATA_BITS_LOW + b'\x00\x00')
                self.device.close()
                print("FTDI connection closed.")
            except Exception as e:
                print(f"Error during close: {e}")
            finally:
                self.device = None

    # -------------------------------------------------------------------
    # Internal Helpers (Private)
    # -------------------------------------------------------------------

    def _calculate_divisor(self, freq_hz: int) -> int:
        return max(0, min(65535, int((30_000_000 / freq_hz) - 1)))

    def _check_chain_alive(self) -> bool:
        """Sanity check to confirm TDO is responding and not dead/shorted."""
        self.device.purge(ftd.defines.PURGE_RX)
        self.device.write(TmsCommands.RESET + TmsCommands.TO_SHIFT_DR)
        self.device.write(MpsseOpcodes.READ_DATA_BYTES_LSB + b'\x03\x00' + TmsCommands.TO_IDLE + MpsseOpcodes.SEND_IMMEDIATE)
        time.sleep(0.01)

        rx_data = self.device.read(4)
        if len(rx_data) == 4:
            test_val = struct.unpack('<I', rx_data)[0]
            if test_val not in (0xFFFFFFFF, 0x00000000):
                return True
            print(f"WARNING: Dead JTAG chain detected (Readback: 0x{test_val:08X}).")
        else:
            print("WARNING: Target not responding or unpowered.")
        return False

    # -------------------------------------------------------------------
    # Raw Hardware I/O
    # -------------------------------------------------------------------

    def write(self, data: bytes):
        self.device.write(data)

    def read(self, num_bytes: int) -> bytes:
        return self.device.read(num_bytes)

    def purge_rx(self):
        self.device.purge(ftd.defines.PURGE_RX)

    def reset_tap_to_idle(self):
        """Forces TAP controller through Reset state and parks in Run-Test/Idle."""
        self.purge_rx()
        self.write(TmsCommands.RESET + TmsCommands.TLR_TO_IDLE)