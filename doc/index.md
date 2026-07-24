# Zynq-7000 JTAG & QSPI Flash Management Tool

An advanced Python tool for low-level JTAG interaction with the **Xilinx Zynq-7000** SoC family. 
This project leverages the hardware MPSSE engine of FTDI chips (e.g., FT232H, used in Digilent JTAG-SMT2 programmers) to provide direct access to the silicon debug infrastructure, internal memory (OCM), and external peripherals (QSPI Flash).

---

## 🚀 Main Features

* **Direct JTAG / MPSSE Interaction**: Fine-grained control of the JTAG state machine (TAP Controller) without heavy software abstractions.
* **CoreSight DAP & AHB-AP**: Interrogation, power-up, and management of the Debug Access Port (DAP) for the integrated ARM Cortex-A9 processors.
* **High-Speed Bulk Write Engine**: Extreme optimization of USB-packetized APB transactions for ultra-fast writes to the OCM memory (SRAM).
* **FSBL Execution**: Ability to load and execute a First Stage Boot Loader (`fsbl.bin`), bypassing normal boot flows.
* **QSPI Flash Diagnostics**: Manual control of the Zynq QSPI controller to query the JEDEC ID of the attached SPI Flash memory.
* **Modular & "Magic-Number-Free" Architecture**: Fully self-documenting code organized for easy extensibility and maintenance.

---

## 🏗 Project Architecture

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
   git clone https://github.com/andpiapmc/jtag_flash.git
   cd jtag_flash
   ```

2. **Install dependencies:**
   ```bash
   pip install ftd2xx
   ```

> ⚠️ **Warning for Windows Users:** Make sure you have the native FTDI D2XX driver installed. Do not replace the driver with WinUSB or libusb via Zadig, otherwise the `ftd2xx` module will fail to detect the interface.

## 📚 Documentation Portal

This documentation site is compiled using **MkDocs** with the **Material** theme. To build and view the full technical reference manuals (covering JTAG state transitions and FTDI MPSSE protocols) locally:

1. **Install MkDocs and the Material theme:**
   ```bash
   pip install mkdocs mkdocs-material
   ```

2. **Start the live-reloading local server:**
   ```bash
   mkdocs serve
   ```

3. **Open your browser and navigate to:**
   ```text
   http://127.0.0.1:8000
   ```

## 🖥 Usage

Ensure your Zynq board is powered on and the JTAG cable is connected. For FSBL injection, place your `fsbl.bin` file in the ext folder of the script.
Launch the interactive interface:

```bash
python main.py
```

You will be presented with the following menu:

```text
----------------------------------------
0. Exit
1. List FTDI devices
2. Open JTAG
3. Close JTAG
4. Scan JTAG chain
5. Read FPGA USERCODE
6. Test ARM DAP
7. Test OCM RAM
8. Load & Run fsbl.bin
9. Read QSPI JEDEC ID
?. Help
----------------------------------------
```

### Typical Workflow:

1. Press `1` to verify the FTDI probe is detected.
2. Press `2` to open the channel and initialize the TCK (defaults to 15 MHz).
3. Press `4` to validate the physical integrity of the JTAG chain.
4. Press `8` to inject the FSBL and unlock hardware peripherals.
5. Press `9` to communicate with the QSPI Flash memory.

## ⚠️ Disclaimer

This tool interacts directly with low-level hardware registers. Injecting incorrect memory addresses or incorrectly driving reset lines via AHB-AP can cause the SoC to hang temporarily (resolvable with a board power-cycle). Use with caution.