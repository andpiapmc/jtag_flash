# The FTDI MPSSE Engine

The Multi-Protocol Synchronous Serial Engine (MPSSE) is a programmable hardware engine embedded in advanced FTDI chips (for example, FT232H, FT2232H, FT4232H). It converts compact USB bulk transfers into precisely timed synchronous serial operations on the chip's GPIO pins, enabling high-throughput, low-overhead implementations of protocols such as SPI, I2C, and JTAG.

## Why MPSSE exists

- USB is a packet-oriented transport with non-negligible per-transfer latency on many hosts and drivers (USB 2.0 bulk latency is often in the order of ~1 ms). Driving JTAG by toggling pins individually over USB (bit-banging) is therefore very slow.
- MPSSE batches many low-level pin operations into a compact command stream that the FTDI chip executes in hardware. This offloads timing from the host, reduces USB round-trips, and yields much higher effective throughput for JTAG transactions.

## Where MPSSE sits (USB ↔ JTAG)

- The host application constructs MPSSE command buffers and sends them to the FTDI device via USB bulk writes.
- The FTDI device’s MPSSE engine parses the command stream and drives physical signals (TCK, TMS, TDI and the sampling of TDO) with accurate internal timing.
- The target’s JTAG TAP sees conventional JTAG signal edges; MPSSE can capture TDO samples and return them in USB reads to the host.
- Summary: USB carries MPSSE commands → MPSSE translates to pin-level JTAG operations → JTAG TAP responds → MPSSE returns sampled data to host.

## Command structure and primitives

Each MPSSE command typically follows the shape:

- Opcode (1 byte)
- Length field (1 or 2 bytes depending on command)
- Payload (0..N bytes)

Key concepts:

- Byte vs bit operations: Use byte-oriented commands for long streams (best throughput) and bit-oriented commands for the final few bits when necessary (e.g., to set the final TMS and exit Shift-DR/IR).
- Full-duplex shifting: Many commands clock data out while sampling data in simultaneously (useful for Shift-DR and Shift-IR).
- TMS-only commands: Special opcodes exist to send sequences of TMS bits without reading TDO, used to navigate the JTAG TAP state machine deterministically.

See `zynq_constants.py` for this project's opcode names and mapping.

## Important opcodes (project values from `zynq_constants.py`)

Below are the MPSSE opcode constants as defined in `zynq_constants.py` for this project. Use these names when referring to opcodes in the codebase.

```text
MpsseOpcodes.SHIFT_TMS_NO_READ   = 0x4B
MpsseOpcodes.SHIFT_BYTES_LSB_RW  = 0x39
MpsseOpcodes.SHIFT_BITS_LSB_RW   = 0x3B
MpsseOpcodes.SHIFT_TMS_READ      = 0x6B
MpsseOpcodes.READ_DATA_BYTES_LSB = 0x28
MpsseOpcodes.SEND_IMMEDIATE      = 0x87
```

- `MpsseOpcodes.SHIFT_TMS_NO_READ` (0x4B)
	- Purpose: Send a sequence of TMS bits (no TDO reads) to move the TAP through states.
	- Length encoding: number_of_bits - 1 (one byte). Payload packs bits LSB-first across bytes.
	- Useful for deterministic TAP navigation (Test-Logic-Reset → Run-Test/Idle → Select-DR-Scan → Shift-DR, etc.).

- `MpsseOpcodes.SHIFT_BYTES_LSB_RW` (0x39)
	- Purpose: Clock N bytes out and simultaneously read N bytes in (full-duplex, byte-aligned).
	- Length encoding: 16-bit little-endian representing (N - 1).
	- Best for high-throughput data transfer in Shift-DR/Shift-IR.

- `MpsseOpcodes.SHIFT_BITS_LSB_RW` (0x3B)
	- Purpose: Clock the remaining 1–7 bits and optionally set TMS on the final bit.
	- Length encoding: number_of_bits - 1 (one byte). Payload contains the low-order bits.

Note: Exact opcode values, bit order, and length formats are documented in the FTDI MPSSE application note and device datasheet. The project maps these in `zynq_constants.py`.

## How MPSSE interacts with the JTAG TAP

- TAP navigation: Use `SHIFT_TMS_NO_READ` commands to advance the TAP through states (Test-Logic-Reset, Run-Test/Idle, Select-DR-Scan, Capture-DR, Shift-DR, Exit1-DR, Pause-DR, etc.). The host builds the appropriate TMS bit pattern to reach the desired state.
- Data shifting:
	- Enter `Shift-IR` or `Shift-DR` via TMS sequences.
	- Use byte-oriented commands (`0x39`) to move large blocks of instruction or data bits.
	- Use bit-oriented commands (`0x3B`) for the final 1–7 bits and to set the TMS bit that exits the shift state.
- Termination: The last bit that sets TMS high to leave Shift-DR/IR is typically transmitted with a bit-wise command to ensure correct TAP state transition.

## Example sequences (illustrative)

These examples illustrate the format of MPSSE command buffers. Exact opcode values, bit order, and length semantics depend on the FTDI chip and driver; consult the FTDI documentation for your device.


1) TMS-only example — send 10 TMS bits (packed LSB-first) with `MpsseOpcodes.SHIFT_TMS_NO_READ` (0x4B):

```hex
0x4B, 0x09, 0x5A, 0x01
```

- `0x4B` : `MpsseOpcodes.SHIFT_TMS_NO_READ` opcode
- `0x09` : length = 10 - 1 = 9
- `0x5A 0x01` : payload bytes containing 10 TMS bits packed LSB-first across the two bytes (example pattern). The low-order bit of the first payload byte is the first TMS bit shifted out.

The payload here is an illustrative packing — construct the payload by placing TMS bits LSB-first into successive bytes.

2) Full-duplex byte shift example — write 3 bytes and read 3 bytes with `MpsseOpcodes.SHIFT_BYTES_LSB_RW` (0x39):

```hex
0x39, 0x02, 0x00, 0xAA, 0xBB, 0xCC
```

- `0x39` : `MpsseOpcodes.SHIFT_BYTES_LSB_RW` opcode
- `0x02 0x00` : 16-bit little-endian length = (3 - 1) = 2
- `0xAA 0xBB 0xCC` : data bytes clocked out; the device returns 3 bytes of sampled TDO in the read buffer corresponding to these clock cycles.

3) Trailing bits example — send 5 final bits (e.g., to set final TMS) with `MpsseOpcodes.SHIFT_BITS_LSB_RW` (0x3B):

```hex
0x3B, 0x04, 0x16
```

- `0x3B` : `MpsseOpcodes.SHIFT_BITS_LSB_RW` opcode
- `0x04` : length = 5 - 1 = 4 (i.e., 5 bits)
- `0x16` : payload byte whose low 5 bits are the bits to be clocked LSB-first

Notes:
- All bit/byte payloads used with these opcodes are LSB-first (least-significant bit is transmitted first).
- The exact behavior (whether TMS is sampled on the last bit, whether TDO is returned) can differ slightly by opcode variant — prefer the project constants in `zynq_constants.py` when writing code.

4) Combined flow example — go to Shift-DR, stream many bytes, then finalize:

- Build TMS sequence to get to Shift-DR:
	- `0x4B, <len-1>, <tms_payload...>`
- Send repeated byte-chunks in loops:
	- `0x39, <len_low>, <len_high>, <data...>`  (multiple consecutive blocks)
- Send final bits to exit:
	- `0x3B, <bits-1>, <final_bits_payload>`
- Optionally follow with a TMS sequence to move to Run-Test/Idle.

## Bulk write optimization (practical speed trick)

- For large memory writes (for example, writing an FSBL or bulk RAM loading), the host can pack hundreds or thousands of JTAG transactions into a single Python `bytearray` (or similar) and send it in large USB bulk writes (typical practice: chunk sizes tuned to device/driver limits, e.g., 64 KB chunks).
- This reduces USB overhead and maximizes the rate at which the FTDI MPSSE can toggle the JTAG pins and accept transactions.
- In this project the `_write_mem32_bulk()` pattern packs many 32-bit write sequences into large MPSSE command buffers to accelerate AHB-AP writes.

## Practical considerations and caveats

- Device specifics: Opcode encodings, bit-order, and maximum buffer sizes vary by FTDI device and firmware; always verify against the FTDI datasheet/application note for your specific chip.
- Buffer limits and flow control: The FTDI device has internal buffers. Sending huge bursts without allowing the chip to process/read can cause flow problems or dropped bytes. Chunk and throttle appropriately.
- Alignment: Prefer byte-aligned transfers when possible; use bit commands only for final alignment or to flip TMS.
- Pin mapping: The JTAG signals (TCK/TMS/TDI/TDO/TRST/SRST) are mapped to specific FTDI ADBUS/ACBUS pins — ensure the software configures directions and optional pull-ups correctly.
- Timing: MPSSE provides accurate internal timing for the clocks it generates, but USB still determines the latency between command submissions. MPSSE trades latency for aggregate throughput.

## Debugging tips

- Log raw MPSSE commands (hex) sent and the raw read buffers returned; compare expected vs. actual TDO sequences.
- Test small state transitions first: verify a TMS-only sequence moves the TAP to the expected state before streaming large data transfers.
- Use known JTAG test patterns (e.g., IDCODE read) to validate the whole path (host → USB → MPSSE → target).

## Integration notes

- Libraries: Common user-space libraries/drivers that expose MPSSE functionality include `pyftdi`, `libftdi` and FTDI’s D2XX driver bindings. They provide `write()`/`read()` operations to move raw MPSSE buffers.
- Implementation strategy: Construct command buffers on the host, send with a single `write()` per chunk, then `read()` expected TDO bytes. Keep buffer construction deterministic and minimize per-transfer syscalls.

## References

- Project opcode mapping: `zynq_constants.py`  
- FTDI MPSSE Application Note and FTDI device datasheets (refer to the datasheet for exact opcode encodings and device limits).

