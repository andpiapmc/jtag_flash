#########################################################
#               JTAG management tool                    #
#########################################################

import ftd2xx as ftd


# Menu list
menu_list = [
    "0. Exit",
    "1. List FTDI devices"
]


# Function to list FTDI devices
def list_ftdi_devices():
    print("Scan for FTDI devices in progress...")
    
    devices = ftd.listDevices()
    
    if devices is None:
        print("No FTDI devices detected.")
        return

    print(f"Found {len(devices)} FTDI endpoints:")
    
    for i, dev in enumerate(devices):
        dev_name = dev.decode('utf-8', errors='ignore')
        print(f"Index {i}: {dev_name}")
        

# Menu display function
def show_menu():
    print("\n" + "-" * 40)

    for item in menu_list:
        print(item)

    print("-" * 40 + "\n")


# Main execution block
if __name__ == '__main__':
    # Main loop
    while True:
        show_menu()

        choice = input("> ")

        match choice:
            case "0":
                print("Exiting...\n")
                break

            case "1":
                list_ftdi_devices()
                
            case _:
                pass

