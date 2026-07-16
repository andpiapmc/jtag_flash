# Zynq-7000 JTAG & QSPI Flash Management Tool

An advanced Python tool for low-level JTAG interaction with the **Xilinx Zynq-7000** SoC family. 
This project leverages the hardware MPSSE engine of FTDI chips (e.g., FT232H, used in Digilent JTAG-SMT2 programmers) to provide direct access to the silicon debug infrastructure, internal memory (OCM), and external peripherals (QSPI Flash).

## 🚀 Main Features

* **Direct JTAG / MPSSE Interaction**: Fine-grained control of the JTAG state machine (TAP Controller) without heavy software abstractions.
* **CoreSight DAP & AHB-AP**: Interrogation, power-up, and management of the Debug Access Port (DAP) for the integrated ARM Cortex-A9 processors.
* **High-Speed Bulk Write Engine**: Extreme optimization of USB-packetized APB transactions for ultra-fast writes to the OCM memory (SRAM).
* **FSBL Execution**: Ability to load and execute a First Stage Boot Loader (`fsbl.bin`), bypassing normal boot flows.
* **QSPI Flash Diagnostics**: Manual control of the Zynq QSPI controller to query the JEDEC ID of the attached SPI Flash memory.
* **Modular & "Magic-Number-Free" Architecture**: Fully self-documenting code organized for easy extensibility and maintenance.

---

## 🏗 Project Structure

The codebase is strictly refactored into three main modules to ensure maximum maintainability:

1. **`zynq_constants.py`**: The hardware vocabulary. Contains all FTDI opcodes, register addresses (CoreSight, Zynq SLCR, QSPI), logical constants, and bitmasks.
2. **`jtag_controller.py`**: The core engine. Manages the native USB interface, exposes TMS state machine primitives, and orchestrates complex flows (e.g., FSBL injection or QSPI queries).
3. **`main.py`**: The application entry-point and Command Line Interface (CLI) handler.

---

## ⚙️ System Requirements

This tool is specifically designed for **Windows** environments and requires original FTDI drivers. This ensures that official Xilinx tools (Vivado/Vitis) continue to function without conflicts with the JTAG probe.

### Prerequisites:
* **Python 3.10+**
* **Python module `ftd2xx`** (Wrapper for the native FTDI library)
* **High-speed FTDI-based hardware probe** (e.g., Xilinx Platform Cable, Digilent JTAG-SMT2, JTAG-HS3)

### Installation:

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/your-username/jtag_flash.git](https://github.com/your-username/jtag_flash.git)
   cd jtag_flash
   ```

2. **Create and activate a Python virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. **Install Python dependencies:**
   ```powershell
   python -m pip install -r requirements.txt
   ```

4. **Build or serve the documentation locally:**
   ```powershell
   python -m mkdocs serve
   ```