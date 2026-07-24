# Developer API Reference

This section provides the automatically generated technical documentation extracted directly from the Python source code. It serves as a comprehensive reference for extending the tool or integrating the JTAG engine into other projects.

Modules are listed in the same order as the [Project Architecture](architecture.md) layers, from raw transport up to the facade.

---

## 🔌 MPSSE Transport

Raw FTDI/MPSSE USB communication - device discovery, connection lifecycle, and byte-level I/O. No knowledge of JTAG above the byte level.

::: mpsse_transport

---

## 🔗 JTAG TAP Protocol

Generic IEEE 1149.1 TAP state-machine operations: IR/DR shifting and blind chain scanning. Target-agnostic - works on any JTAG-compliant chain.

::: jtag_tap

---

## 🧠 ARM CoreSight DAP

DPACC/APACC transactions and the AHB-AP memory window (`read_mem32`, `write_mem32`, `write_mem32_bulk`) used by every SoC-specific workflow in this project.

::: coresight_dap

---

## 🖥️ Zynq SoC Control

SLCR unlock/lock, peripheral clock gating, CPU0 halt/release, FSBL injection, and OCM RAM testing.

::: zynq_soc

---

## 💾 QSPI Flash Controller

Manual SPI transfer engine and SPI-NOR flash workflows: JEDEC ID, chip/sector erase, page programming, and Quad-Enable management.

::: qspi_flash

---

## ⚙️ JTAG Controller Facade

The top-level orchestrator. Wires every layer above into flat methods used by the CLI.

::: jtag_controller

---

## 📚 Hardware Constants Vocabulary

The "magic-number-free" mapping of the Zynq-7000 silicon architecture: FTDI opcodes, CoreSight DAP addresses, and QSPI/SLCR registers.

::: zynq_constants
