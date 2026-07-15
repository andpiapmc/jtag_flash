# JTAG Protocol Basics

The Joint Test Action Group (JTAG) standard (IEEE 1149.1) is a hardware interface used for testing, verifying, and debugging printed circuit boards and integrated circuits. 

## The 4-Wire Bus
At its core, JTAG uses four mandatory pins:
* **TCK (Test Clock):** Synchronizes the internal state machine operations.
* **TMS (Test Mode Select):** Sampled on the rising edge of TCK to navigate the internal state machine.
* **TDI (Test Data In):** Serial data shifted into the device.
* **TDO (Test Data Out):** Serial data shifted out of the device.

## The TAP Controller State Machine
Every JTAG-compliant chip contains a 16-state finite state machine called the **Test Access Port (TAP) Controller**. Navigation through these states is controlled entirely by the sequence of `1`s and `0`s sent over the **TMS** pin.

Our controller in `jtag_controller.py` heavily relies on these specific states:

* **Test-Logic-Reset (TLR):** The safe/reset state. Holding TMS `HIGH` for 5 consecutive clock cycles guarantees a return to TLR from *any* state.
* **Run-Test/Idle:** A resting state where the TAP is active but not shifting data.
* **Shift-IR (Instruction Register):** Used to load a command (e.g., Target the FPGA Usercode or the ARM CoreSight DAP).
* **Shift-DR (Data Register):** Used to read or write the actual data payload based on the active instruction.

### Example: Navigating to Shift-DR
To move from `Test-Logic-Reset` to `Shift-DR`, the TMS pin must receive the sequence `0 -> 1 -> 0 -> 0`. In our code, this is handled transparently by the `_tms_to_shift_dr()` helper method.