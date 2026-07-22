"""
LAYER 1 - MPSSE TRANSPORT
=========================
Raw communication with the FTDI FT232H/FT2232H chip running in MPSSE mode.

This layer is deliberately "dumb": it only knows how to open the USB
endpoint, configure the MPSSE engine (clock divisor, GPIO idle state) and
push/pull raw bytes. It has NO knowledge of JTAG TAP states, ARM CoreSight,
or Zynq registers - that knowledge lives in the layers built on top of it
(see jtag_tap.py, coresight_dap.py).

Keeping this separation makes it trivial to answer "how do I talk to the
FTDI chip?" by reading a single, short file.
"""

import ftd2xx as ftd
import time
import struct
from zynq_constants import MpsseOpcodes, TmsCommands


class MpsseTransport:
    """Owns the FTDI device handle and the low-level byte-level protocol."""

    def __init__(self):
        self.device = None  # Active FTDI device handle (ftd2xx object) or None

    # -------------------------------------------------------------------
    # Device discovery / lifecycle
    # -------------------------------------------------------------------

    @staticmethod
    def list_devices():
        """Scans and lists all connected FTDI devices using official D2XX drivers."""
        print("Scanning for FTDI devices via ftd2xx...")
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
            print(f"Error communicating with FTDI driver: {type(e).__name__} - {e}")

    def is_ready(self) -> bool:
        """Validates if the FTDI link is open and responsive."""
        if self.device is None:
            return False
        try:
            self.device.getQueueStatus()
            return True
        except Exception:
            return False

    def open(self, device_index: int = 0, freq_hz: int = 1_000_000):
        """
        Opens the FTDI device, switches it into MPSSE mode, and configures the
        TCK clock divisor. Also performs a quick "is anything alive on TDO?"
        sanity read so we fail fast instead of silently talking to a dead chain.
        """
        if self.is_ready():
            print("JTAG is already open.")
            return

        print("Initializing JTAG...")
        try:
            self.device = ftd.open(device_index)
            self.device.setBitMode(0x00, 0)
            time.sleep(0.05)
            self.device.setBitMode(0x0B, 2)  # Active MPSSE Mode
            time.sleep(0.05)

            self.device.setUSBParameters(65536, 65536)
            self.device.setChars(0, False, 0, False)
            self.device.setTimeouts(1000, 1000)
            self.device.setLatencyTimer(16)
            self.device.purge(ftd.defines.PURGE_RX | ftd.defines.PURGE_TX)

            # Setup initial static lines and hardware divisor
            setup_cmds = bytearray()
            setup_cmds += MpsseOpcodes.DISABLE_CLK_DIV5
            setup_cmds += MpsseOpcodes.TURN_OFF_ADAPTIVE_CLK
            setup_cmds += MpsseOpcodes.DISABLE_3_PHASE_CLK
            setup_cmds += MpsseOpcodes.SET_DATA_BITS_LOW + b'\x88\xFB'
            setup_cmds += MpsseOpcodes.SET_DATA_BITS_HIGH + b'\x00\x00'

            divisor = max(0, min(65535, int((30_000_000 / freq_hz) - 1)))
            setup_cmds += MpsseOpcodes.SET_TCK_DIVISOR + struct.pack('<H', divisor)
            self.device.write(bytes(setup_cmds))

            # Hardware power-on test: shift the TAP to Reset then Shift-DR and
            # read back 4 bytes. All-0x00 or all-0xFF means "no live chain".
            self.device.purge(ftd.defines.PURGE_RX)
            self.device.write(TmsCommands.RESET + TmsCommands.TO_SHIFT_DR)
            self.device.write(MpsseOpcodes.READ_DATA_BYTES_LSB + b'\x03\x00' + TmsCommands.TO_IDLE + MpsseOpcodes.SEND_IMMEDIATE)
            time.sleep(0.01)

            rx_data = self.device.read(4)
            if len(rx_data) == 4:
                test_val = struct.unpack('<I', rx_data)[0]
                if test_val in (0xFFFFFFFF, 0x00000000):
                    print(f"WARNING: FTDI opened, but JTAG chain is DEAD (Read: 0x{test_val:08X}).")
                    self.close()
                    return
            else:
                print("WARNING: Target might be powered off.")
                self.close()
                return

            print(f"FTDI connection opened. TCK set to ~{freq_hz/1e6:.1f} MHz.")
        except Exception as e:
            print(f"Error initializing FTDI: {e}")
            self.device = None

    def close(self):
        """Resets pin configurations and safely terminates the connection."""
        if self.is_ready():
            try:
                self.device.write(MpsseOpcodes.SET_DATA_BITS_LOW + b'\x00\x00')
                self.device.close()
                print("FTDI connection closed.")
            except Exception as e:
                print(f"Error during close: {e}")
            finally:
                self.device = None
        else:
            print("JTAG is not open.")

    # -------------------------------------------------------------------
    # Raw byte-level I/O - used by every layer above this one
    # -------------------------------------------------------------------

    def write(self, data: bytes):
        """Writes raw MPSSE command bytes to the FTDI USB endpoint."""
        self.device.write(data)

    def read(self, num_bytes: int) -> bytes:
        """Reads raw response bytes from the FTDI USB endpoint."""
        return self.device.read(num_bytes)

    def purge_rx(self):
        """Flushes the FTDI RX FIFO (drops any stale/unread bytes)."""
        self.device.purge(ftd.defines.PURGE_RX)

    def reset_tap_to_idle(self):
        """
        Standard bring-up sequence used before almost every JTAG operation:
        force the TAP state machine through Test-Logic-Reset and park it in
        Run-Test/Idle. This is the JTAG equivalent of "hello, are you there?".
        """
        self.purge_rx()
        self.write(TmsCommands.RESET + TmsCommands.TLR_TO_IDLE)
