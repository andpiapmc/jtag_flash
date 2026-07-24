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

The codebase is organized in strict layers, from raw USB bytes up to complete user workflows, so that no file mixes protocol bit-banging with SoC-specific logic. See [Project Architecture](architecture.md) for the full breakdown; in short:

1. **`zynq_constants.py`**: The hardware vocabulary - FTDI opcodes, register addresses (CoreSight, Zynq SLCR, QSPI), logical constants, and bitmasks.
2. **`mpsse_transport.py` → `jtag_tap.py` → `coresight_dap.py`**: Raw FTDI/MPSSE transport, generic JTAG TAP protocol, and ARM CoreSight DAP / memory access, each building on the one before it.
3. **`zynq_soc.py`** and **`qspi_flash.py`**: SoC-specific workflows (FSBL injection, OCM test, QSPI flash management) built on the DAP memory interface.
4. **`jtag_controller.py`**: The facade. Wires every layer together and exposes one flat method per CLI action.
5. **`main.py`**: The application entry-point and Command Line Interface (CLI) handler.

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

Ensure your Zynq board is powered on and the JTAG cable is connected. For FSBL injection or QSPI programming, place your `fsbl.bin` file in the `ext/` folder next to the script (these are the tool's default paths - both prompts also accept a custom path).
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
8. Load & Run FSBL binary
9. Read QSPI JEDEC ID
10. Erase QSPI Flash (full chip)
11. Erase QSPI Sector (64KB, ask offset)
12. Program QSPI Flash (ask file + offset)
13. Enable QSPI Quad Mode
14. Disable QSPI Quad Mode
?. Help
----------------------------------------
```

### Typical Workflow:

1. Press `1` to verify the FTDI probe is detected.
2. Press `2` to open the channel and initialize the TCK (defaults to 15 MHz).
3. Press `4` to validate the physical integrity of the JTAG chain.
4. Press `9` to confirm the QSPI flash responds and identify its manufacturer/capacity.
5. Press `10` or `11` to erase the chip or a single 64KB sector, then `12` to program a binary at a chosen offset.
6. Press `8` if you need to inject and run an FSBL directly from JTAG, bypassing the normal boot sequence (see [FSBL Injection](fsbl_injection.md)).

## ⚠️ Disclaimer

This tool interacts directly with low-level hardware registers. Injecting incorrect memory addresses or incorrectly driving reset lines via AHB-AP can cause the SoC to hang temporarily (resolvable with a board power-cycle). Use with caution.