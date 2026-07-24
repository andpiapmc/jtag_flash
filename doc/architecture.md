# Project Architecture

The `jtag_flash` tool is built with a strict **Separation of Concerns (SoC)** philosophy. Managing raw hardware protocols (like MPSSE and JTAG) alongside complex SoC workflows (like ARM CoreSight injection) can easily lead to "spaghetti code".

To prevent this and eliminate cryptic "magic numbers", the project is divided into three distinct layers.

---

## 1. The Hardware Vocabulary (`zynq_constants.py`)

This module acts as the definitive dictionary for the entire project. It contains absolutely **zero execution logic** and only maps the physical and logical constants required to drive the hardware.

Key components include:

- **`MpsseOpcodes`**: FTDI-specific byte commands used to configure the engine and shift data.
- **`JtagInstr`**: JTAG Instruction Register values that select the FPGA boundary scan or the ARM CoreSight DAP.
- **`CoreSightRegs`** and **`DapReq`**: Debug Port and Access Port register layouts, bitmasks, and transaction formats.
- **`AhbApRegs`** and **`ZynqRegs`**: Memory map addresses for Zynq internal buses and peripherals, including SLCR, OCM, and the QSPI controller.
- **`FLASH_MANUFACTURERS`** and **`FLASH_MEMORY_TYPES`**: JEDEC decoder dictionaries for SPI flash identification.

This separation keeps the core logic clean, self-documenting, and safe from typo-induced hardware crashes.

---

## 2. The Core Engine (`jtag_controller.py`)

`JtagController` is the workhorse of the tool. It is built in layers that descend from user workflows to raw JTAG bit-banging.

### Internal Abstraction Layers
- **Public API (Workflows)**: methods such as `run_fsbl_bin()` and `read_qspi_jedec_id()`. These orchestrate complete operations for the user.
- **Intermediate API (CoreSight)**: methods such as `test_arm_dap()` and `write_mem32()`. These handle ARM DAP transactions and route 32-bit accesses over the internal AHB bus.
- **Atomic API (JTAG & MPSSE)**: private methods such as `_shift_dr()`, `_shift_ir()`, and `_tms_to_shift_dr()`. These only care about shifting bits on the physical JTAG lines (TMS/TDI/TDO) according to the IEEE 1149.1 TAP state machine.

### The Bulk Engine
A critical feature is `_write_mem32_bulk()`. Instead of waiting for a USB acknowledgment on every memory write, it concatenates many AHB-AP transactions into a single MPSSE payload. This reduces USB latency and lets the OCM SRAM be flooded with the FSBL binary in a fraction of the time.

---

## 3. The User Interface (`main.py`)

The application entry point is intentionally lightweight. It performs:

- CLI menu display.
- standard input capture (`stdin`).
- routing of user choices via `match/case` to `JtagController` methods.
- safe cleanup when users abort (`Ctrl+C`), closing the FTDI connection and avoiding port locking.

---

## Data Flow Summary

When the user requests FSBL loading, the flow is:

1. **`main.py`** calls `jtag.run_fsbl_bin()`.
2. **`jtag_controller.py`** reads the binary, halts/reset the target via CoreSight, and breaks the data into word chunks.
3. The bulk engine converts those words into DAP requests using constants from **`zynq_constants.py`**.
4. The atomic JTAG layer wraps DAP requests into MPSSE opcodes.
5. The `ftd2xx` driver sends the final byte stream to the USB-attached FTDI hardware.