# Developer API Reference

This section provides the automatically generated technical documentation extracted directly from the Python source code. It serves as a comprehensive reference for extending the tool or integrating the JTAG engine into other projects.

---

## ⚙️ JTAG Controller Engine

This module handles the low-level MPSSE communication over USB and exposes the fundamental JTAG state machine primitives, as well as the high-level orchestrated flows (like FSBL injection).

::: jtag_controller

---

## 📚 Hardware Constants Vocabulary

This module contains the "magic-number-free" mapping of the Zynq-7000 silicon architecture. It includes FTDI opcodes, CoreSight DAP addresses, and QSPI registers.

::: zynq_constants