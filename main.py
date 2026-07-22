"""
CLI entry point for the Zynq JTAG Management Tool.
Handles user requests and dispatches workflows to the controller engine.
"""

import sys
from jtag_controller import JtagController


def ask_int(prompt: str, default: int) -> int:
    """
    Prompts for an integer (accepts '0x...' hex or plain decimal).
    Pressing Enter keeps `default`.
    """
    raw = input(f"{prompt} [default: 0x{default:06X}]: ").strip()
    if raw == "":
        return default
    try:
        return int(raw, 0)  # base=0 auto-detects "0x" prefix
    except ValueError:
        print(f"Invalid number '{raw}', using default 0x{default:06X}.")
        return default


def ask_str(prompt: str, default: str) -> str:
    """Prompts for a string. Pressing Enter keeps `default`."""
    raw = input(f"{prompt} [default: {default}]: ").strip()
    return raw if raw else default


def cmd_erase_qspi_sector(j: JtagController):
    """Asks the user for the sector offset, then erases that 64KB sector."""
    offset = ask_int("Sector offset to erase", default=0x000000)
    j.erase_qspi_sector(offset=offset)


def cmd_write_qspi_binary(j: JtagController):
    """Asks the user for the binary file path and the target flash offset."""
    filepath = ask_str("Path of the binary file to flash", default="bootblock.bin")
    offset = ask_int("Target flash offset", default=0x000000)
    j.write_qspi_binary(filepath=filepath, start_offset=offset)


def cmd_run_fsbl_bin(j: JtagController):
    """Asks the user for the FSBL binary file path."""
    filepath = ask_str("Path of the FSBL binary to load", default="fsbl.bin")
    j.run_fsbl_bin(filepath=filepath)


# Menu options dictionary
menu_options = {
    "0": {"label": "Exit", "func": None},
    "1": {"label": "List FTDI devices", "func": lambda j: j.list_ftdi_devices()},
    "2": {"label": "Open JTAG", "func": lambda j: j.open(device_index=0, freq_hz=15_000_000)},
    "3": {"label": "Close JTAG", "func": lambda j: j.close()},
    "4": {"label": "Scan JTAG chain", "func": lambda j: j.scan()},
    "5": {"label": "Read FPGA USERCODE", "func": lambda j: j.read_fpga_usercode()},
    "6": {"label": "Test ARM DAP", "func": lambda j: j.test_arm_dap()},
    "7": {"label": "Test OCM RAM", "func": lambda j: j.test_ocm_ram()},
    "8": {"label": "Load & Run FSBL binary", "func": cmd_run_fsbl_bin},
    "9": {"label": "Read QSPI JEDEC ID", "func": lambda j: j.read_qspi_jedec_id()},
    "10": {"label": "Erase QSPI Flash (full chip)", "func": lambda j: j.erase_qspi_chip()},
    "11": {"label": "Erase QSPI Sector (64KB, ask offset)", "func": cmd_erase_qspi_sector},
    "12": {"label": "Program QSPI Flash (ask file + offset)", "func": cmd_write_qspi_binary},
    "13": {"label": "Enable QSPI Quad Mode", "func": lambda j: j.enable_qspi_quad_mode()},
    "14": {"label": "Disable QSPI Quad Mode (diagnostic)", "func": lambda j: j.disable_qspi_quad_mode()},
    "?": {"label": "Help", "func": lambda j: show_menu()},
}


def show_menu():
    """Prints the commands list."""
    print("\n" + "-" * 40)
    for key, option in menu_options.items():
        print(f"{key}. {option['label']}")
    print("-" * 40 + "\n")


def main_loop(jtag_controller: JtagController) -> bool:
    """Evaluates inputs and dispatches to the handler."""
    choice = input("> ").strip()

    if choice == "0":
        return False

    if choice in menu_options and menu_options[choice]["func"]:
        try:
            menu_options[choice]["func"](jtag_controller)
        except Exception as e:
            print(f"ERROR: command failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    return True


if __name__ == '__main__':
    # Initialize the JTAG controller and start the CLI loop
    controller = JtagController()
    show_menu()
    try:
        while main_loop(controller):
            pass
    except KeyboardInterrupt:
        print("\nAborting manual routine...")
    finally:
        print("Exiting...")
        controller.close()
        sys.exit(0)
