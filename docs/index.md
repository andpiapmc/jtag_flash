# Zynq-7000 JTAG & QSPI Flash Management Tool[cite: 4]

An advanced Python tool for low-level JTAG interaction with the **Xilinx Zynq-7000** SoC family[cite: 4]. 
This project leverages the hardware MPSSE engine of FTDI chips (e.g., FT232H, used in Digilent JTAG-SMT2 programmers) to provide direct access to the silicon debug infrastructure, internal memory (OCM), and external peripherals (QSPI Flash)[cite: 4].

---

## 🚀 Main Features[cite: 4]

* **Direct JTAG / MPSSE Interaction**: Fine-grained control of the JTAG state machine (TAP Controller) without heavy software abstractions[cite: 4].
* **CoreSight DAP & AHB-AP**: Interrogation, power-up, and management of the Debug Access Port (DAP) for the integrated ARM Cortex-A9 processors[cite: 4].
* **High-Speed Bulk Write Engine**: Extreme optimization of USB-packetized APB transactions for ultra-fast writes to the OCM memory (SRAM)[cite: 4].
* **FSBL Execution**: Ability to load and execute a First Stage Boot Loader (`fsbl.bin`), bypassing normal boot flows[cite: 4].
* **QSPI Flash Diagnostics**: Manual control of the Zynq QSPI controller to query the JEDEC ID of the attached SPI Flash memory[cite: 4].
* **Modular & "Magic-Number-Free" Architecture**: Fully self-documenting code organized for easy extensibility and maintenance[cite: 4].

---

## 🏗 Project Architecture[cite: 4]

The codebase is strictly refactored into three main modules to ensure maximum maintainability[cite: 4]:

1. **`zynq_constants.py`**: The hardware vocabulary. Contains all FTDI opcodes, register addresses (CoreSight, Zynq SLCR, QSPI), logical constants, and bitmasks[cite: 4].
2. **`jtag_controller.py`**: The core engine. Manages the native USB interface, exposes TMS state machine primitives, and orchestrates complex flows (e.g., FSBL injection or QSPI queries)[cite: 4].
3. **`main.py`**: The application entry-point and Command Line Interface (CLI) handler[cite: 4].

---

## ⚙️ System Requirements[cite: 4]

This tool is specifically designed for **Windows** environments and requires original FTDI drivers[cite: 4]. This ensures that official Xilinx tools (Vivado/Vitis) continue to function without conflicts with the JTAG probe[cite: 4].

### Prerequisites:[cite: 4]
* **Python 3.10+**[cite: 4]
* **Python module `ftd2xx`** (Wrapper for the native FTDI library)[cite: 4]
* **High-speed FTDI-based hardware probe** (e.g., Xilinx Platform Cable, Digilent JTAG-SMT2, JTAG-HS3)[cite: 4]

### Installation:[cite: 4]

1. **Clone the repository:**[cite: 4]
   ```bash
   git clone [https://github.com/your-username/jtag_flash.git](https://github.com/your-username/jtag_flash.git)
   cd jtag_flash
   ```[cite: 4]

2. **Install dependencies:**[cite: 4]
   ```bash
   pip install ftd2xx
   ```[cite: 4]

> ⚠️ **Warning for Windows Users:** Make sure you have the native FTDI D2XX driver installed[cite: 4]. Do not replace the driver with WinUSB or libusb via Zadig, otherwise the `ftd2xx` module will fail to detect the interface[cite: 4].

## 📚 Documentation Portal[cite: 4]

This documentation site is compiled using **MkDocs** with the **Material** theme[cite: 4]. To build and view the full technical reference manuals (covering JTAG state transitions and FTDI MPSSE protocols) locally[cite: 4]:

1. **Install MkDocs and the Material theme:**[cite: 4]
   ```bash
   pip install mkdocs mkdocs-material
   ```[cite: 4]

2. **Start the live-reloading local server:**[cite: 4]
   ```bash
   mkdocs serve
   ```[cite: 4]

3. **Open your browser and navigate to:**[cite: 4]
   ```text
   [http://127.0.0.1:8000](http://127.0.0.1:8000)
   ```[cite: 4]

## 🖥 Usage[cite: 4]

Ensure your Zynq board is powered on and the JTAG cable is connected[cite: 4]. For FSBL injection, place your `fsbl.bin` file in the root folder of the script[cite: 4].
Launch the interactive interface[cite: 4]:

```bash
python main.py
```[cite: 4]

You will be presented with the following menu[cite: 4]:

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
```[cite: 4]

### Typical Workflow:[cite: 4]

1. Press `1` to verify the FTDI probe is detected[cite: 4].
2. Press `2` to open the channel and initialize the TCK (defaults to 15 MHz)[cite: 4].
3. Press `4` to validate the physical integrity of the JTAG chain[cite: 4].
4. Press `8` to inject the FSBL and unlock hardware peripherals[cite: 4].
5. Press `9` to communicate with the QSPI Flash memory[cite: 4].

## ⚠️ Disclaimer[cite: 4]

This tool interacts directly with low-level hardware registers[cite: 4]. Injecting incorrect memory addresses or incorrectly driving reset lines via AHB-AP can cause the SoC to hang temporarily (resolvable with a board power-cycle)[cite: 4]. Use with caution[cite: 4].