# Project Architecture

The `jtag_flash` tool is built with a strict **layered architecture**: each file owns exactly one level of abstraction, from raw USB bytes up to complete user-facing workflows. Every layer only talks to the one directly below it, which keeps the "spaghetti code" risk that comes from mixing raw protocol bit-banging with complex SoC workflows firmly under control - and eliminates cryptic "magic numbers" along the way.

The stack, from lowest to highest level:

```text
main.py            CLI: menu, input, dispatch
    ↓
jtag_controller.py Facade: wires everything together, flat public API
    ↓                 ↓
zynq_soc.py       qspi_flash.py    (SoC-specific workflows)
    ↓________________↓
coresight_dap.py               (ARM CoreSight DAP / AHB-AP memory access)
    ↓
jtag_tap.py                    (generic IEEE 1149.1 JTAG TAP protocol)
    ↓
mpsse_transport.py             (raw FTDI/MPSSE USB bytes)

zynq_constants.py  ← the hardware vocabulary, imported by every layer above
```

---

## 1. The Hardware Vocabulary (`zynq_constants.py`)

This module acts as the definitive dictionary for the entire project. It contains **zero execution logic** and only maps the physical and logical constants required to drive the hardware.

Key components include:

- **`MpsseOpcodes`** and **`TmsCommands`**: FTDI-specific byte commands and pre-packed TAP state-machine navigation sequences.
- **`JtagInstr`**: JTAG Instruction Register values that select the FPGA boundary scan or the ARM CoreSight DAP.
- **`CoreSightRegs`**, **`DapReq`**, **`AhbApRegs`**: Debug Port and Access Port register layouts, bitmasks, and transaction formats.
- **`ZynqRegs`**: Memory-map addresses for SLCR, OCM, and the QSPI controller.
- **`QspiConfig`** and **`FlashCmd`**: QSPI controller bitfields and standard SPI-NOR flash opcodes.
- **`FLASH_MANUFACTURERS`** and **`FLASH_MEMORY_TYPES`**: JEDEC decoder dictionaries for SPI flash identification.

This separation keeps the logic in every other file self-documenting and safe from typo-induced hardware crashes.

---

## 2. Raw Transport (`mpsse_transport.py`)

`MpsseTransport` owns the FTDI device handle. It knows nothing about JTAG - only how to open the USB endpoint, configure the MPSSE engine (clock divisor, GPIO idle state), and push/pull raw bytes (`write()`, `read()`, `purge_rx()`). It also exposes `reset_tap_to_idle()`, the byte sequence every higher layer uses to force the TAP into a known state before an operation.

## 3. Generic JTAG Protocol (`jtag_tap.py`)

`JtagTap` implements the IEEE 1149.1 TAP state machine on top of a transport: the private `_shift_bits()` primitive shifts N bits into IR or DR and reads TDO back, and the public `shift_ir()` / `shift_dr()` build on it to target a specific TAP in the chain (0 = FPGA/PL, 1 = ARM DAP). `scan_chain()` and `read_fpga_usercode()` are chain-level operations built the same way. This layer is target-agnostic - it would work on any JTAG-compliant chain.

## 4. ARM CoreSight DAP (`coresight_dap.py`)

`CoreSightDap` implements DPACC/APACC transactions on top of a `JtagTap`: the private `_dap_write()` / `_dap_read()` / `_init_ahb_ap()` handle the raw 35-bit ARM debug protocol, and the public API - `connect()`, `write_mem32()`, `read_mem32()`, `write_mem32_bulk()`, `clear_sticky_errors()` - exposes a plain "read/write a physical memory address" interface used by every layer above it. Only one Access Port is used anywhere in this project (referred to as the AHB-AP): all peripheral, SLCR, and OCM access goes through the same memory window.

### The Bulk Engine

`write_mem32_bulk()` is the one method that steps outside the generic read/write pattern. Instead of a full IR/DR round-trip per word, it builds raw MPSSE payloads directly and batches hundreds of AHB-AP write transactions into a single USB write. This is what lets an entire FSBL binary be streamed into OCM in a fraction of a second - see [FSBL Injection](fsbl_injection.md) and [MPSSE Protocol](mpsse_protocol.md) for the details.

## 5. SoC-Specific Workflows (`zynq_soc.py`, `qspi_flash.py`)

These two files sit at the same layer and both build only on `CoreSightDap`'s memory interface:

- **`ZynqSoc`**: SLCR unlock/lock, peripheral clock gating (`enable_peripheral_clock()`, `enable_qspi_ref_clock()`), CPU0 halt/release, `load_and_run_fsbl()`, and `test_ocm_ram()`.
- **`QspiFlash`**: brings up the QSPI controller (`_init_controller()`), implements the manual SPI bit-banging transfer engine (`_manual_transfer()`), and exposes the flash workflows used by the CLI - `read_jedec_id()`, `erase_chip()`, `erase_sector()`, `write_binary_file()`, `enable_quad_mode()` / `disable_quad_mode()`.

## 6. The Facade (`jtag_controller.py`)

`JtagController` composes the layers above (`transport → tap → dap → soc / qspi`) and exposes one flat method per CLI action, with a shared `_require_open()` guard. This is intentionally a thin layer - if you want to know *how* something works, follow the chain down through the files above, not this one.

## 7. The User Interface (`main.py`)

The application entry point is intentionally lightweight. It performs:

- CLI menu display.
- Standard input capture (`stdin`).
- Routing of user choices via a `menu_options` dictionary lookup (`option["func"](controller)`), not `match/case`.
- Cleanup on exit, including closing the FTDI connection so the port isn't left locked.

---

## Data Flow Summary

When the user requests FSBL loading, the flow is:

1. **`main.py`** calls `controller.run_fsbl_bin()`.
2. **`jtag_controller.py`** delegates to `soc.load_and_run_fsbl()`.
3. **`zynq_soc.py`** reads the binary, halts CPU0 via the SLCR reset register, and breaks the data into 32-bit word chunks.
4. **`coresight_dap.py`**'s bulk engine converts those words into DAP write requests using constants from **`zynq_constants.py`**.
5. **`jtag_tap.py`** / **`mpsse_transport.py`** wrap the requests into MPSSE opcodes and stream them over USB.
6. `ftd2xx` sends the final byte stream to the FTDI hardware, which drives TCK/TMS/TDI and samples TDO.
