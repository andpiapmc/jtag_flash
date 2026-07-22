"""
CLI entry point for the Zynq JTAG Management Tool.
"""

import sys
from jtag_controller import JtagController


def _ask_int(prompt: str, default: int) -> int:
    raw_input = input(f"{prompt} [default: 0x{default:06X}]: ").strip()
    
    # If the input is empty, return the default value
    if not raw_input:
        return default
        
    try:
        # base=0 allows Python to automatically understand "0x" as hexadecimal
        return int(raw_input, 0)
    except ValueError:
        print(f"Warning: '{raw_input}' is not a valid number. Using default 0x{default:06X}.")
        return default


def _ask_str(prompt: str, default: str) -> str:
    raw_input = input(f"{prompt} [default: {default}]: ").strip()
    return raw_input if raw_input else default


# --- Command Handlers ---
# These functions group multiple steps (like asking for input + calling the controller)
# so the main menu dictionary stays clean and easy to read.

def cmd_erase_qspi_sector(controller: JtagController):
    """Prompts for a sector address and erases that specific 64KB sector in the QSPI flash."""
    offset = _ask_int("Sector offset to erase", default=0x000000)
    controller.erase_qspi_sector(offset=offset)


def cmd_write_qspi_binary(controller: JtagController):
    """Prompts for a file path and a destination address, then writes the file to flash."""
    filepath = _ask_str("Path of the binary file to flash", default="bootblock.bin")
    offset = _ask_int("Target flash offset", default=0x000000)
    controller.write_qspi_binary(filepath=filepath, start_offset=offset)


def cmd_run_fsbl_bin(controller: JtagController):
    """Prompts for the First Stage Bootloader (FSBL) file and executes it on the Zynq."""
    filepath = _ask_str("Path of the FSBL binary to load", default="fsbl.bin")
    controller.run_fsbl_bin(filepath=filepath)


# --- Menu Configuration ---
# A dictionary mapping the user's numeric choice to a label and the function to execute.
# For simple commands, we use 'lambda' to quickly pass the controller object to the method.
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


def show_menu():
    """Prints all available commands to the screen."""
    print("\n" + "-" * 40)
    for key, option in menu_options.items():
        print(f"{key}. {option['label']}")
    print("-" * 40 + "\n")


def main_loop(controller: JtagController) -> bool:
    """
    Waits for user input, looks up the choice in the menu, and runs the associated function.
    Returns True to keep the loop running, or False if the user chose to exit.
    """
    choice = input("> ").strip()

    # Exit condition
    if choice == "0":
        return False

    # Execute valid commands
    if choice in menu_options and menu_options[choice]["func"]:
        try:
            # Call the mapped function, passing the JTAG controller
            menu_options[choice]["func"](controller)
        except Exception as e:
            # Catch and display any errors during execution without crashing the CLI
            print(f"ERROR: Command failed: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    return True


if __name__ == '__main__':
    jtag_controller = JtagController()
    
    show_menu()
    
    while main_loop(jtag_controller):
        pass
    
    print("Exiting...")
    jtag_controller.close()
    sys.exit(0)