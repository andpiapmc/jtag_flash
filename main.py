"""
CLI entry point for the Zynq JTAG Management Tool.
Handles user requests and dispatches workflows to the controller engine.
"""

import sys
from jtag_controller import JtagController

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
    "8": {"label": "Load & Run fsbl.bin", "func": lambda j: j.run_fsbl_bin()},
    "9": {"label": "Read QSPI JEDEC ID", "func": lambda j: j.read_qspi_jedec_id()},
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
        menu_options[choice]["func"](jtag_controller)
    
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

