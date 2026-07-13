#########################################################
#               JTAG management tool                    #
#########################################################


def show_menu():
    """
    Display a simple menu for the user to choose an action
    """
    print("\n" + "-" * 40)

    # menu list
    print("0. Exit")

    print("-" * 40 + "\n")


if __name__ == '__main__':
    # Main loop
    while True:
        show_menu()

        choice = input("> ")

        match choice:
            case "0":
                print("Exiting...\n")
                break
            case _:
                pass

