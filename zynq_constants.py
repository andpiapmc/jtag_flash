"""
Zynq-7000 Hardware Constants and Definitions.
Includes JTAG instruction registers, MPSSE opcodes, CoreSight layout,
and Flash memory JEDEC decoding dictionaries.
"""

import struct

class MpsseOpcodes:
    """FTDI MPSSE Command Opcodes."""
    DISABLE_CLK_DIV5      = b'\x8A'
    TURN_OFF_ADAPTIVE_CLK = b'\x97'
    DISABLE_3_PHASE_CLK   = b'\x8D'
    SET_DATA_BITS_LOW     = b'\x80'
    SET_DATA_BITS_HIGH    = b'\x82'
    SET_TCK_DIVISOR       = b'\x86'
    
    # Data Shifting Opcodes
    READ_DATA_BYTES_LSB   = b'\x28'
    SHIFT_BYTES_LSB_RW    = b'\x39'
    SHIFT_BITS_LSB_RW     = b'\x3B'
    SHIFT_TMS_NO_READ     = b'\x4B'
    SHIFT_TMS_READ        = b'\x6B'
    SEND_IMMEDIATE        = b'\x87'


class TmsCommands:
    """TAP CONTROLLER STATE MACHINE CONSTANTS
    Pre-calculated MPSSE TMS shift commands for state transitions.
    """
    RESET            = (MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 7, 0xFF)) * 4
    TO_SHIFT_DR      = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 3, 0x02)
    TO_IDLE          = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 2, 0x03)
    EXIT_TO_IDLE     = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 1, 0x01)
    TLR_TO_IDLE      = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 0, 0x00)
    IDLE_TO_SHIFT_IR = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 3, 0x03)
    IDLE_TO_SHIFT_DR = MpsseOpcodes.SHIFT_TMS_NO_READ + struct.pack('<BB', 2, 0x01)


class JtagInstr:
    """JTAG Instruction Register (IR) Values."""
    FPGA_USERCODE = 0x08
    DAP_DPACC     = 0x0A  # Debug Port Access
    DAP_APACC     = 0x0B  # Access Port Access
    DAP_IDCODE    = 0x0E  # ARM CoreSight ID


class CoreSightRegs:
    """ARM CoreSight DAP Register Addresses (A[3:2] indices)."""
    # Debug Port (DP) Registers
    DP_ABORT      = 0x0
    DP_CTRL_STAT  = 0x1
    DP_SELECT     = 0x2
    DP_RDBUFF     = 0x3
    # Access Port (AP) Registers (specifically AHB-AP)
    AP_CSW        = 0x0  # Control/Status Word
    AP_TAR        = 0x1  # Transfer Address Register
    AP_DRW        = 0x3  # Data Read/Write Register


class DapReq:
    """ARM DAP Transaction Constants and Bitmasks."""
    # A DAP request is 35 bits: [34:3 Data] | [2:1 Addr A32] | [0 RnW]
    READ      = 1
    WRITE     = 0
    SHIFT_LEN = 35 
    ACK_MASK  = 0x07

    # DP_ABORT Register Flags
    CLEAR_ERR = 0x0000001E  # Clears WDATAERR, STICKYERR, STICKYCMP, STICKYORUN

    # DP_CTRL_STAT Register Flags
    PWRUP_REQ = 0x50000000  # Sets CSYSPWRUPREQ and CDBGPWRUPREQ


class AhbApRegs:
    """Advanced High-performance Bus Access Port (AHB-AP) Constants."""
    # Control/Status Word (CSW) configuration
    # Size=32bit (b010), AddrInc=Single (b01), DeviceEn=1, Prot=0x23
    CSW_DEFAULT_32BIT = 0x23000012


class QspiConfig:
    """Zynq QSPI Controller Configuration Flags."""
    MANUAL_START_EN = (1 << 15)
    MANUAL_CS_EN    = (1 << 14)
    PCS0_HIGH       = (1 << 10)  # Chip Select 0 De-asserted (High)
    MANUAL_START    = (1 << 16)  # Trigger transmission bit
    JEDEC_CMD       = 0x0000009F # Standard Read JEDEC ID command

    
class ZynqRegs:
    """Memory Map and Constants for Xilinx Zynq-7000 Series."""
    # SLCR (System Level Control Registers)
    SLCR_UNLOCK_ADDR = 0xF8000008
    SLCR_LOCK_ADDR   = 0xF8000004
    SLCR_UNLOCK_KEY  = 0x0000DF0D
    SLCR_LOCK_KEY    = 0x0000767B
    A9_CPU_RST_CTRL  = 0xF8000244

    # QSPI Controller
    QSPI_BASE        = 0xE000D000
    QSPI_CONFIG      = QSPI_BASE + 0x00
    QSPI_STATUS      = QSPI_BASE + 0x04
    QSPI_ENABLE      = QSPI_BASE + 0x14
    QSPI_TXD_FIFO    = QSPI_BASE + 0x1C
    QSPI_RXD_FIFO    = QSPI_BASE + 0x20
    QSPI_LQSPI_CFG   = QSPI_BASE + 0xA0
    
    # On-Chip Memory
    OCM_BASE_ADDR    = 0x00000000


class FlashCmd:
    """Standard JEDEC QSPI Flash Opcodes"""
    WRSR = 0x01  # Write Status Register
    WRSR2 = 0x31 # Write Status Register-2
    WREN = 0x06  # Write Enable
    RDSR = 0x05  # Read Status Register
    RDSR2 = 0x35 # Read Status Register-2
    SE   = 0xD8  # Sector Erase (64KB)
    CE   = 0xC7  # Chip Erase
    PP   = 0x02  # Page Program
    FAST_READ = 0x0B  # Fast Read with dummy cycle
    RDID = 0x9F # Read JEDEC ID
    QE_BIT = 0x40  # Quad Enable bit


# Dictionary of known TAP IDCODEs
KNOWN_TAPS = {
    0x0BA00477: "ARM Cortex-A9 CoreSight DAP (Zynq 7000)",
    0x0BA02477: "ARM Cortex-A53 CoreSight DAP (Zynq UltraScale+)",
    0x0BA04477: "ARM Cortex-R5 CoreSight DAP",
    0x03722093: "Xilinx Zynq Z-7010",
    0x03727093: "Xilinx Zynq Z-7015 / Z-7020",
    0x0372C093: "Xilinx Zynq Z-7030",
    0x03731093: "Xilinx Zynq Z-7045",
    0x03736093: "Xilinx Zynq Z-7100",
    0x04711093: "Xilinx Zynq UltraScale+ ZU2EG/ZU3EG",
    0x04721093: "Xilinx Zynq UltraScale+ ZU4/ZU5/ZU7"
}

# Dictionary of common SPI Flash Manufacturer IDs (JEP106)
FLASH_MANUFACTURERS = {
    0x01: "Spansion / Cypress (Infineon)",
    0x1C: "EON Silicon Devices",
    0x1F: "Adesto / Dialog Semiconductor",
    0x20: "Micron / ST (Numonyx)",
    0x85: "Puya Semiconductor",
    0x9D: "ISSI (Integrated Silicon Solution Inc.)",
    0xBF: "SST / Microchip",
    0xC2: "Macronix (MXIC)",
    0xC8: "GigaDevice",
    0xEF: "Winbond"
}

# Dictionary for Memory Types based on Manufacturer ID
FLASH_MEMORY_TYPES = {
    0x9D: { 0x40: "IS25LQ (3.0V Quad)", 0x60: "IS25LP (3.0V Quad)", 0x70: "IS25WP (1.8V Quad)" },
    0xEF: { 0x30: "W25X", 0x40: "W25Q (SPI)", 0x60: "W25Q (QPI)" },
    0xC2: { 0x20: "MX25L (3.0V)", 0x25: "MX25U (1.8V)", 0x28: "MX25R (Ultra Low Power)" },
    0x20: { 0x20: "M25P", 0xBA: "N25Q / MT25QL (3.0V)", 0xBB: "MT25QU (1.8V)" },
    0x01: { 0x02: "S25FL-A/K (3.0V)", 0x20: "S25FL-S (3.0V)" },
    0xC8: { 0x40: "GD25Q (3.0V)", 0x60: "GD25LQ (1.8V)" }
}

