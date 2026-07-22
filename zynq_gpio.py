"""
Zynq-7000 MIO and GPIO Hardware Controller.
Manages pin multiplexing via SLCR and GPIO output registers.
"""

from zynq_constants import ZynqRegs


class ZynqGPIO:
    def __init__(self, dap, soc):
        self.dap = dap
        self.soc = soc

    # -------------------------------------------------------------------
    # Internal Hardware Helpers (Private)
    # -------------------------------------------------------------------

    def _set_mio_gpio_output(self, mio_pin: int, level: int):
        """
        Configures an MIO pin (0-31) as a GPIO Output and sets its logical level (0 or 1).
        """
        if not (0 <= mio_pin <= 31):
            raise ValueError(f"MIO pin {mio_pin} out of supported range (0-31).")

        # 1. Configure MIO Mux in SLCR as GPIO Output
        self.soc.slcr_unlock()
        mio_ctrl_reg = ZynqRegs.SLCR_MIO_CTRL_0 + (mio_pin * 4)
        self.dap.write_mem32(mio_ctrl_reg, ZynqRegs.MIO_PIN_MUX_GPIO)

        # 2. Configure Direction (DIRM) and Output Enable (OEN)
        val_dirm = self.dap.read_mem32(ZynqRegs.GPIO_DIRM_0)
        self.dap.write_mem32(ZynqRegs.GPIO_DIRM_0, val_dirm | (1 << mio_pin))

        val_oen = self.dap.read_mem32(ZynqRegs.GPIO_OEN_0)
        self.dap.write_mem32(ZynqRegs.GPIO_OEN_0, val_oen | (1 << mio_pin))

        # 3. Drive DATA bit high or low
        val_data = self.dap.read_mem32(ZynqRegs.GPIO_DATA_0)
        if level:
            val_data |= (1 << mio_pin)
        else:
            val_data &= ~(1 << mio_pin)
            
        self.dap.write_mem32(ZynqRegs.GPIO_DATA_0, val_data)

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def force_mio_gpio_high(self, mio_pin: int):
        """Forces an MIO pin (0-31) to GPIO HIGH state."""
        self._set_mio_gpio_output(mio_pin, level=1)