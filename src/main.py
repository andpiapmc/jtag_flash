"""
CLI entry point for the Zynq JTAG Management Tool.
Provides interactive menu interfaces for debugging and flash programming.
"""

import sys
from jtag_controller import JtagController
from zynq_constants import DEFAULT_FSBL_PATH, DEFAULT_BOOTBLOCK_PATH


def _ask_int(prompt: str, default: int) -> int:
    """Prompts user for an integer or hexadecimal value."""
    raw_input = input(f"{prompt} [default: 0x{default:06X}]: ").strip()
    if not raw_input:
        return default
    try:
        return int(raw_input, 0)
    except ValueError:
        print(f"Warning: '{raw_input}' is not a valid number. Using default 0x{default:06X}.")
        return default


def _ask_str(prompt: str, default: str) -> str:
    """Prompts user for a string value with a default fallback."""
    raw_input = input(f"{prompt} [default: {default}]: ").strip()
    return raw_input if raw_input else default


def cmd_erase_qspi_sector(controller: JtagController) -> None:
    """CLI wrapper for sector erase command."""
    offset = _ask_int("Sector offset to erase", default=0x000000)
    controller.erase_qspi_sector(offset=offset)


def cmd_write_qspi_binary(controller: JtagController) -> None:
    """CLI wrapper for flashing binary file."""
    filepath = _ask_str("Path of the binary file to flash", default=DEFAULT_BOOTBLOCK_PATH)
    offset = _ask_int("Target flash offset", default=0x000000)
    controller.write_qspi_binary(filepath=filepath, start_offset=offset)


def cmd_run_fsbl_bin(controller: JtagController) -> None:
    """CLI wrapper for FSBL injection."""
    filepath = _ask_str("Path of the FSBL binary to load", default=DEFAULT_FSBL_PATH)
    controller.run_fsbl_bin(filepath=filepath)


menu_options = {
    "0": {"label": "Exit", "func": None},
    "1": {"label": "List FTDI devices", "func": lambda c: c.list_ftdi_devices()},
    "2": {"label": "Open JTAG", "func": lambda c: c.open(device_index=0, freq_hz=15_000_000)},
    "3": {"label": "Close JTAG", "func": lambda c: c.close()},
    "4": {"label": "Scan JTAG chain", "func": lambda c: c.scan()},
    "5": {"label": "Read FPGA USERCODE", "func": lambda c: c.read_fpga_usercode()},
    "6": {"label": "Test ARM DAP", "func": lambda c: c.test_arm_dap()},
    "7": {"label": "Test OCM RAM", "func": lambda c: c.test_ocm_ram()},
    "8": {"label": "Load & Run FSBL binary", "func": cmd_run_fsbl_bin},
    "9": {"label": "Read QSPI JEDEC ID", "func": lambda c: c.read_qspi_jedec_id()},
    "10": {"label": "Erase QSPI Flash (full chip)", "func": lambda c: c.erase_qspi_chip()},
    "11": {"label": "Erase QSPI Sector (64KB, ask offset)", "func": cmd_erase_qspi_sector},
    "12": {"label": "Program QSPI Flash (ask file + offset)", "func": cmd_write_qspi_binary},
    "13": {"label": "Enable QSPI Quad Mode", "func": lambda c: c.enable_qspi_quad_mode()},
    "14": {"label": "Disable QSPI Quad Mode", "func": lambda c: c.disable_qspi_quad_mode()},
    "?": {"label": "Help", "func": lambda c: show_menu()},
}


def show_menu() -> None:
    """Prints formatted interactive menu options."""
    print("\n" + "-" * 40)
    for key, option in menu_options.items():
        print(f"{key}. {option['label']}")
    print("-" * 40 + "\n")


def main_loop(controller: JtagController) -> bool:
    """Main execution loop for parsing user selection."""
    choice = input("> ").strip()

    if choice == "0":
        return False

    if choice in menu_options and menu_options[choice]["func"]:
        try:
            menu_options[choice]["func"](controller)
        except Exception as e:
            print(f"ERROR: Command failed: {e}")

    return True


if __name__ == '__main__':
    jtag_controller = JtagController()
    show_menu()

    try:
        while main_loop(jtag_controller):
            pass
    except KeyboardInterrupt:
        print("\nAborting manual routine...") # CTRL+C
    finally:
        print("Exiting...")
        jtag_controller.close()
        sys.exit(0)
