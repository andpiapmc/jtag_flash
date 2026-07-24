# Zynq-7000 JTAG & QSPI Flash Management Tool

An advanced Python tool for low-level JTAG interaction with the **Xilinx Zynq-7000** SoC family.
It leverages the hardware MPSSE engine of FTDI chips (e.g., FT232H, used in Digilent JTAG-SMT2 programmers) to provide direct access to the silicon debug infrastructure, internal memory (OCM), and external peripherals (QSPI Flash).

📖 **[Read the full documentation](https://andpiapmc.github.io/jtag_flash/)** — architecture, JTAG/MPSSE protocol internals, FSBL injection sequence, and the complete usage guide live there. This README only covers the essentials to get up and running.

## 🚀 Main Features

- **Direct JTAG / MPSSE Interaction**: fine-grained control of the JTAG TAP state machine.
- **CoreSight DAP & AHB-AP**: management of the Debug Access Port for the ARM Cortex-A9 cores.
- **High-Speed Bulk Write Engine**: optimized USB-packetized writes to OCM memory (SRAM).
- **FSBL Execution**: load and execute a First Stage Boot Loader (`fsbl.bin`) directly over JTAG.
- **QSPI Flash Programming**: read the JEDEC ID, erase the full chip or a single 64KB sector, program a binary at any offset, and toggle Quad I/O mode on the SPI-NOR flash.
- **Safe CLI Lifecycle**: the FTDI connection is always closed cleanly on exit, including on `Ctrl+C`.

For the full feature breakdown and the module-by-module architecture, see [`docs/architecture.md`](docs/architecture.md) or the hosted site above.

---

## ⚙️ Quick Install

Requires **Windows**, **Python 3.10+**, and a FTDI-based JTAG probe (e.g., Xilinx Platform Cable, Digilent JTAG-SMT2) with the original FTDI D2XX driver installed.

```bash
git clone https://github.com/andpiapmc/jtag_flash.git
cd jtag_flash

python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
```

> ⚠️ Do not replace the FTDI driver with WinUSB/libusb via Zadig — `ftd2xx` needs the native D2XX driver to detect the interface.

## ▶️ Usage

```bash
python main.py
```

This opens an interactive menu to list FTDI devices, scan the JTAG chain, inject and run `fsbl.bin`, and read/erase/program the QSPI flash (including Quad I/O mode). See the [full usage guide](https://andpiapmc.github.io/jtag_flash/#-usage) for the typical workflow and menu reference.

## 📚 Documentation site (local build)

```bash
python -m mkdocs serve   # live preview at http://127.0.0.1:8000
python -m mkdocs build   # static site in ./site
```

## 📄 License

> No license file is currently present in this repository. Add a `LICENSE` file (e.g., MIT, GPLv3, Apache-2.0) to clarify how others may use, modify, and distribute this project.
