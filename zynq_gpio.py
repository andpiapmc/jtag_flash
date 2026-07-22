import zynq_constants


class ZynqGPIO:
    """
    Layer 4c: Controllo MIO e GPIO per Zynq-7000.
    """

    def __init__(self, dap, soc):
        self.dap = dap
        self.soc = soc

    def force_mio_gpio_high(self, mio_pin: int):
        """
        Configura un pin MIO (0-31) come GPIO Output guidato a LIVELLO ALTO (1).
        """
        if not (0 <= mio_pin <= 31):
            raise ValueError(f"MIO pin {mio_pin} fuori dal range supportato (0-31).")

        # 1. Configura il MIO Mux in SLCR come GPIO
        self.soc.slcr_unlock()
        # Usa il valore base SLCR definito nel modulo constants
        slcr_base = getattr(zynq_constants, "SLCR_BASE", 0xF8000000)
        mio_ctrl_reg = slcr_base + 0x700 + (mio_pin * 4)
        
        # TRI_ENABLE=0 (output enabled), L3..L0_SEL=0 (GPIO)
        self.dap.write_mem32(mio_ctrl_reg, 0x00000600)

        # 2. Indirizzi GPIO Controller (Bank 0 per MIO 0-31)
        gpio_base = getattr(zynq_constants, "GPIO_BASE", 0xE000A000)
        gpio_dirm = gpio_base + 0x00000284
        gpio_oen  = gpio_base + 0x00000288
        gpio_data = gpio_base + 0x00000040

        # Configura Direction (DIRM_0) -> Output (1)
        val_dirm = self.dap.read_mem32(gpio_dirm)
        self.dap.write_mem32(gpio_dirm, val_dirm | (1 << mio_pin))

        # Configura Output Enable (OEN_0) -> Enabled (1)
        val_oen = self.dap.read_mem32(gpio_oen)
        self.dap.write_mem32(gpio_oen, val_oen | (1 << mio_pin))

        # Scrivi dato alto (DATA_0) -> High (1)
        val_data = self.dap.read_mem32(gpio_data)
        self.dap.write_mem32(gpio_data, val_data | (1 << mio_pin))