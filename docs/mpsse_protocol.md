# The FTDI MPSSE Engine

The **Multi-Protocol Synchronous Serial Engine (MPSSE)** is a highly configurable hardware block inside advanced FTDI chips (like the FT232H, FT2232H, and FT4232H). It acts as a protocol translator, converting generic USB bulk packets into specific synchronous serial protocols like SPI, I2C, or, in our case, **JTAG**.

## Why MPSSE?
Standard USB latency is around 1 millisecond per packet. If we were to toggle JTAG pins individually via software (Bit-Banging) over USB, the maximum speed would be incredibly slow. MPSSE solves this by processing massive buffers of commands in hardware. 

## Command Structure
The MPSSE reads commands from the USB stream. Each command consists of an **Opcode**, followed by **Length** bytes, and finally the **Data** payload.

Our `zynq_constants.py` file maps these opcodes explicitly:

### 1. TMS Shifting (`0x4B`)
Navigating the JTAG TAP Controller requires shifting bits on the TMS pin without necessarily reading TDO. 
* **Opcode:** `MpsseOpcodes.SHIFT_TMS_NO_READ` (`0x4B`)
* **Length:** Number of bits minus 1.
* **Payload:** The sequence of 1s and 0s.

### 2. High-Speed Data Shifting (`0x39` / `0x3B`)
When we are in the `Shift-DR` state (e.g., dumping memory or writing the FSBL), we use full-duplex data shifting. MPSSE allows us to shift full bytes (`0x39`) and remaining bits (`0x3B`) independently for maximum throughput.

### 3. The Bulk Write Optimization
In `_write_mem32_bulk()`, we pack hundreds of 32-bit ARM CoreSight AHB-AP write transactions into a single massive bytearray. By sending 64KB chunks of MPSSE opcodes directly to the USB endpoint, we achieve incredibly fast memory injection speeds required for loading the `fsbl.bin`.