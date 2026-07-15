"""
CLI entry point for the Zynq JTAG Management Tool.
Handles user requests and dispatches workflows to the controller engine.
"""

import sys
from jtag_controller import JtagController

def show_menu():
    """Prints the commands list."""
    menu_list = [
        "0. Exit", 
        "1. List FTDI devices", 
        "2. Open JTAG", 
        "3. Close JTAG",
        "4. Scan JTAG chain", 
        "5. Read FPGA USERCODE", 
        "6. Test ARM DAP", 
        "7. Test OCM RAM", 
        "8. Load & Run fsbl.bin", 
        "9. Read QSPI JEDEC ID", 
        "?. Help"
    ]
    print("\n" + "-" * 40)
    for item in menu_list: 
        print(item)
    print("-" * 40 + "\n")


def main_loop(jtag: JtagController) -> bool:
    """
    Evaluates inputs from the stdin loop and handles routing execution.
    """
    choice = input("> ").strip()
    match choice:
        case "0": 
            return False
        case "1": 
            jtag.list_ftdi_devices()
        case "2": 
            # High speed default stable line initialization
            jtag.open(device_index=0, freq_hz=15_000_000)
        case "3": 
            jtag.close()
        case "4": 
            jtag.scan()
        case "5": 
            jtag.read_fpga_usercode()
        case "6": 
            jtag.test_arm_dap()
        case "7": 
            jtag.test_ocm_ram()
        case "8": 
            jtag.run_fsbl_bin()
        case "9": 
            jtag.read_qspi_jedec_id()
        case "?": 
            show_menu()
        case _: 
            pass
    return True


if __name__ == '__main__':
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

        