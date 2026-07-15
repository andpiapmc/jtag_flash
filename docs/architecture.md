# Project Architecture

The `jtag_flash` tool is built with a strict **Separation of Concerns (SoC)** philosophy. Managing raw hardware protocols (like MPSSE and JTAG) alongside complex SoC workflows (like ARM CoreSight injection) can easily lead to "spaghetti code". 

To prevent this and eliminate cryptic "magic numbers", the project is divided into three distinct layers.

---

## 1. The Hardware Vocabulary (`zynq_constants.py`)

This module acts as the definitive dictionary for the entire project. It contains absolutely **zero execution logic**. Instead, it maps all the physical and logical constants required to speak with the hardware.

Key components include:
* **`MpsseOpcodes`**: Maps FTDI-specific byte commands used to configure the engine and shift data (e.g., `SHIFT_TMS_NO_READ`).
* **`JtagInstr`**: Standard JTAG Instruction Register (IR) values to target either the FPGA boundary scan or the ARM CoreSight DAP.
* **`CoreSightRegs` & `DapReq`**: The structural layout and bitmasks for the ARM Debug Port (DP) and Access Port (AP).
* **`AhbApRegs` & `ZynqRegs`**: Memory map addresses for the internal Zynq buses (SLCR, OCM, QSPI Controller).
* **`FLASH_MANUFACTURERS` & `FLASH_MEMORY_TYPES`**: Decoding dictionaries mapping raw JEDEC hex values to human-readable SPI Flash information.

By isolating these constants, the core logic remains clean, self-documenting, and safe from typo-induced hardware crashes.

---

## 2. The Core Engine (`jtag_controller.py`)

This is the workhorse of the tool. The `JtagController` class is structured hierarchically, from high-level workflows down to atomic hardware transitions. It imports the vocabulary from `zynq_constants.py` and uses the `ftd2xx` wrapper to talk to the USB endpoint.

### Internal Abstraction Layers:
1. **Public API (Workflows)**: Methods like `run_fsbl_bin()` or `read_qspi_jedec_id()`. These are the macros called by the user. They orchestrate complex sequences of hardware operations.
2. **Intermediate API (CoreSight)**: Methods like `test_arm_dap()` or `write_mem32()`. These handle the ARM-specific Debug Access Port protocol, routing 32-bit reads/writes over the internal AHB bus.
3. **Atomic API (JTAG & MPSSE)**: Private methods like `_shift_dr()`, `_shift_ir()`, and `_tms_to_shift_dr()`. These methods care only about shifting bits and bytes over the physical JTAG lines (TMS, TDI, TDO) according to the IEEE 1149.1 state machine.

### The Bulk Engine
A critical feature of the controller is the `_write_mem32_bulk()` method. Instead of waiting for a USB acknowledgment after every single memory write, it concatenates hundreds of AHB-AP transactions into a massive MPSSE bytearray payload. This completely bypasses the USB 1ms latency bottleneck, allowing the OCM SRAM to be flooded with the FSBL binary in fractions of a second.

---

## 3. The User Interface (`main.py`)

The entry point of the tool is kept intentionally minimalistic. It handles:
* The Command Line Interface (CLI) menu printing.
* Capturing standard user input (`stdin`).
* Routing choices (`match/case`) to the appropriate public methods of the `JtagController` instance.
* Safe exit handling, ensuring that if a user aborts (`Ctrl+C`), the FTDI connection is cleanly closed to prevent port locking.

---

## Data Flow Summary

When a user requests to load the FSBL, the data flows downward through the layers:

1. **`main.py`** calls `jtag.run_fsbl_bin()`.
2. **`jtag_controller.py`** reads the binary file, issues CoreSight halts/resets (`_init_ahb_ap`), and passes chunks of words to the bulk engine.
3. The bulk engine translates these words into DAP requests using definitions from **`zynq_constants.py`**.
4. The atomic JTAG methods wrap these DAP requests into MPSSE opcodes.
5. The `ftd2xx` driver sends the final byte stream to the physical USB hardware.