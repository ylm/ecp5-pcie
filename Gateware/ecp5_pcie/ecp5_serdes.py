from nmigen import *
from nmigen.build import *
from nmigen.lib.cdc import FFSynchronizer, AsyncFFSynchronizer
from nmigen.lib.fifo import AsyncFIFOBuffered
from .serdes import PCIeSERDESInterface, K, Ctrl


__all__ = ["LatticeECP5PCIeSERDES"]


class LatticeECP5PCIeSERDES(Elaboratable): # Based on Yumewatari
    """
    Lattice ECP5 DCU configured in PCIe mode, 2.5 Gb/s. Assumes 100 MHz reference clock on SERDES clock
    input pair. Only provides a single lane.
    Uses 1:1 or 1:2 gearing.

    Clock frequencies are 250 MHz for 1:1 and 125 MHz for 1:2.

    Parameters
    ----------
    ref_clk : Signal
        100 MHz SERDES reference clock.
    rx_clk_o : Signal
        Clock recovered from received data.
    rx_clk_i : Signal
        Clock for the receive FIFO.
    tx_clk_o : Signal
        Clock generated by transmit PLL.
    tx_clk_i : Signal
        Clock for the transmit FIFO.
    """
    def __init__(self, gearing):
        assert gearing == 1 or gearing == 2

        self.ref_clk = Signal() # reference clock

        self.rx_clk = Signal()  # recovered word clock

        self.tx_clk = Signal()  # generated word clock

        # RX and TX buses from / to the SERDES
        self.rx_bus = Signal(24)
        self.tx_bus = Signal(24)

        # The PCIe lane with all signals necessary to control it
        self.lane = PCIeSERDESInterface(ratio=gearing)
        
        # Ratio, 1:1 means one symbol received per cycle, 1:2 means two symbols received per cycle, halving the output clock frequency.
        self.gearing = gearing

        # Bit Slip
        self.slip = Signal()
    
    def elaborate(self, platform: Platform) -> Module:
        m = Module()

        lane = self.lane
        m.submodules += lane # Add the PCIe Lane as a submodule

        platform.add_clock_constraint(self.rx_clk, 250e6 / self.gearing) # For NextPNR, set the maximum clock frequency such that errors are given
        platform.add_clock_constraint(self.tx_clk, 250e6 / self.gearing)

        # RX and TX clock input signals, these go to the SERDES.
        rx_clk_i = Signal()
        tx_clk_i = Signal()
        # RX and TX clock output signals, these come from the SERDES.
        rx_clk_o = Signal()
        tx_clk_o = Signal()

        # Connect RX and TX SERDES clock inputs to the RX clock output
        m.d.comb += rx_clk_i.eq(rx_clk_o)
        m.d.comb += tx_clk_i.eq(tx_clk_o)

        # Clocks exposed by this module are the clock for rx symbol input and tx symbol output, usually they should be frequency-locked but have variable phase offset.
        m.d.comb += self.rx_clk.eq(rx_clk_o)
        m.d.comb += self.tx_clk.eq(tx_clk_o)


        # The clock input, on the Versa board this comes from the ispCLOCK IC
        m.submodules.extref0 = Instance("EXTREFB",
            o_REFCLKO=self.ref_clk, # The reference clock is output to ref_clk, it is not really accessible as a signal, since it only exists within the SERDES
            p_REFCK_PWDNB="0b1",
            p_REFCK_RTERM="0b1",            # 100 Ohm
            p_REFCK_DCBIAS_EN="0b0",
        )
        m.submodules.extref0.attrs["LOC"] = "EXTREF0" # Locate it 


        if self.gearing == 1: # Different gearing compatibility!
            # If it is 1:1, only the first symbol has data
            m.d.comb += [
                lane.rx_symbol.eq(self.rx_bus[0:9]),
                lane.rx_valid.eq(self.rx_bus[0:9] != K(14,7)), # SERDES outputs K14.7 when there are coding errors
                self.tx_bus.eq(Cat(lane.tx_symbol[0:9], lane.tx_set_disp[0], lane.tx_disp[0], lane.tx_e_idle[0]))
            ]
        else:
            # For 1:2, the output symbols get composed from both symbols, structure of rx_data and tx_data is shown on page 8/9 of TN1261
            m.d.comb += [
                lane.rx_symbol.eq(Cat(
                    self.rx_bus[ 0: 9],
                    self.rx_bus[12:21])),
                lane.rx_valid.eq(Cat(
                    self.rx_bus[ 0: 9] != K(14,7),
                    self.rx_bus[12:21] != K(14,7))),
                self.tx_bus.eq(Cat(
                    lane.tx_symbol[0: 9], lane.tx_set_disp[0], lane.tx_disp[0], lane.tx_e_idle[0],
                    lane.tx_symbol[9:18], lane.tx_set_disp[1], lane.tx_disp[1], lane.tx_e_idle[1])),
            ]


        # RX signals and their domain-crossed parts
        rx_los   = Signal() # Loss of Signal
        rx_los_s = Signal()
        rx_lol   = Signal() # RX Loss of Lock
        rx_lol_s = Signal()
        rx_lsm   = Signal() # Sync state machine status
        rx_lsm_s = Signal()
        rx_inv   = Signal() # Invert RX
        rx_det   = Signal() # RX detected

        # TX signals
        tx_lol   = Signal() # TX PLL Loss of Lock
        tx_lol_s = Signal()

        # Clock domain crossing for status signals and tx data
        m.submodules += [
            FFSynchronizer(rx_los, rx_los_s, o_domain="rx"),
            FFSynchronizer(rx_lol, rx_lol_s, o_domain="rx"),
            FFSynchronizer(rx_lsm, rx_lsm_s, o_domain="rx"),
            
#            FFSynchronizer(tx_lol, tx_lol_s, o_domain="tx"),
#            FFSynchronizer(self.tx_bus, tx_bus_s, o_domain="tx"),
        ]

        # Connect the signals to the lanes signals
        m.d.comb += [
            rx_inv.eq(lane.rx_invert),
            rx_det.eq(lane.rx_align),
            lane.rx_present.eq(~rx_los_s), 
            lane.rx_locked .eq(~rx_lol_s),
            lane.rx_aligned.eq(rx_lsm_s),
            lane.tx_locked.eq(tx_lol_s)
        ]

        pcie_det_en = Signal() # Enable lane detection
        pcie_ct     = Signal() # Scan enable flag
        pcie_done   = Signal() # Scan finished flag
        pcie_done_s = Signal()
        pcie_con    = Signal() # PCIe lane connected
        pcie_con_s  = Signal()

        det_timer = Signal(range(16)) # Detection Timer

        # Clock domain crossing for PCIe detection signals
        m.submodules += [
            FFSynchronizer(pcie_done, pcie_done_s, o_domain="tx"),
            FFSynchronizer(pcie_con, pcie_con_s, o_domain="tx")
        ]

        with m.FSM(domain="tx", reset="START"):
            with m.State("START"):
                # Before starting a Receiver Detection test, the transmitter must be put into
                # electrical idle by setting the tx_idle_ch#_c input high. The Receiver Detection
                # test can begin 120 ns after tx_elec_idle is set high by driving the appropriate
                # pci_det_en_ch#_c high.
                m.d.tx += det_timer.eq(15)
                #m.d.tx += lane.det_valid.eq(0)
                with m.If(lane.det_enable):
                    m.next = "SET-DETECT-H"
            with m.State("SET-DETECT-H"):
                # 1. The user drives pcie_det_en high, putting the corresponding TX driver into
                #    receiver detect mode. [...] The TX driver takes some time to enter this state
                #    so the pcie_det_en must be driven high for at least 120ns before pcie_ct
                #    is asserted.
                with m.If(det_timer == 0):
                    m.d.tx += pcie_det_en.eq(1)
                    m.d.tx += det_timer.eq(15)
                    m.next = "SET-STROBE-H"
                with m.Else():
                    m.d.tx += det_timer.eq(det_timer - 1)
            with m.State("SET-STROBE-H"):
                # 2. The user drives pcie_ct high for four byte clocks.
                with m.If(det_timer == 0):
                    m.d.tx += pcie_ct.eq(1)
                    m.d.tx += det_timer.eq(3)
                    m.next = "SET-STROBE-L"
                with m.Else():
                    m.d.tx += det_timer.eq(det_timer - 1)
            with m.State("SET-STROBE-L"):
                # 3. SERDES drives the corresponding pcie_done low.
                # (this happens asynchronously, so we're going to observe a few samples of pcie_done
                # as high)
                with m.If(det_timer == 0):
                    m.d.tx += pcie_ct.eq(0)
                    m.next = "WAIT-DONE-L"
                with m.Else():
                    m.d.tx += det_timer.eq(det_timer - 1)
            with m.State("WAIT-DONE-L"):
                with m.If(~pcie_done_s):
                    m.next = "WAIT-DONE-H"
            with m.State("WAIT-DONE-H"):
                with m.If(pcie_done_s):
                    #m.d.tx += lane.det_status.eq(pcie_con_s) TODO: Figure this out
                    #m.d.tx += lane.det_status.eq(pcie_con_s)
                    m.d.tx += lane.det_status.eq(1)
                    m.next = "DONE"
            with m.State("DONE"):
                m.d.tx += lane.det_valid.eq(1)
                with m.If(~lane.det_enable):
                    m.next = "START"
                with m.Else():
                    m.next = "DONE"

        gearing_str = "0b0" if self.gearing == 1 else "0b1" # Automatically select value based on gearing

        m.submodules.dcu0 = Instance("DCUA",
            # DCU — power management
            p_D_MACROPDB            ="0b1",
            p_D_IB_PWDNB            ="0b1",      # undocumented, seems to be "input buffer power down"
            p_D_TXPLL_PWDNB         ="0b1",
            i_D_FFC_MACROPDB        =1,

            # DCU — reset
            i_D_FFC_MACRO_RST       =0,
            i_D_FFC_DUAL_RST        =0,
            i_D_FFC_TRST            =0,

            # DCU — clocking
            i_D_REFCLKI             =self.ref_clk,
            o_D_FFS_PLOL            =tx_lol,
            p_D_REFCK_MODE          ="0b100",   # 25x ref_clk
            p_D_TX_MAX_RATE         ="2.5",     # 2.5 Gbps
            p_D_TX_VCO_CK_DIV       ="0b000",   # DIV/1
            p_D_BITCLK_LOCAL_EN     ="0b1",     # undocumented (PCIe sample code used)
            p_D_SYNC_LOCAL_EN       ="0b1",
            p_D_BITCLK_FROM_ND_EN   ="0b0",

            # DCU ­— unknown
            p_D_CMUSETBIASI         ="0b00",    # begin undocumented (PCIe sample code used)
            p_D_CMUSETI4CPP         ="0d3",     # 0d4 in Yumewatari
            p_D_CMUSETI4CPZ         ="0d101",  # 0d3 in Yumewatari
            p_D_CMUSETI4VCO         ="0b00",
            p_D_CMUSETICP4P         ="0b01",
            p_D_CMUSETICP4Z         ="0b101",
            p_D_CMUSETINITVCT       ="0b00",
            p_D_CMUSETISCL4VCO      ="0b000",
            p_D_CMUSETP1GM          ="0b000",
            p_D_CMUSETP2AGM         ="0b000",
            #p_D_CMUSETZGM           ="0b100",
            #p_D_SETIRPOLY_AUX       ="0b10",
            p_D_CMUSETZGM           ="0b000",
            p_D_SETIRPOLY_AUX       ="0b00",
            p_D_SETICONST_AUX       ="0b01",
            #p_D_SETIRPOLY_CH        ="0b10",
            #p_D_SETICONST_CH        ="0b10",
            p_D_SETIRPOLY_CH        ="0b00",
            p_D_SETICONST_CH        ="0b00",
            p_D_SETPLLRC            ="0d1",
            p_D_RG_EN               ="0b1",
            p_D_RG_SET              ="0b00",    # end undocumented

            # DCU — FIFOs
            p_D_LOW_MARK            ="0d4",
            p_D_HIGH_MARK           ="0d12",

            # CH0 — protocol
            p_CH0_PROTOCOL          ="PCIE",
            p_CH0_PCIE_MODE         ="0b1",

            # RX CH ­— power management
            p_CH0_RPWDNB            ="0b1",
            i_CH0_FFC_RXPWDNB       =1,

            # RX CH ­— reset
            i_CH0_FFC_RRST          =0,
            i_CH0_FFC_LANE_RX_RST   =0,

            # RX CH ­— input
            i_CH0_FFC_SB_INV_RX     =rx_inv,
 
            p_CH0_REQ_EN            ="0b1",
            p_CH0_RX_RATE_SEL       ="0d8",
            p_CH0_REQ_LVL_SET       ="0b00",

            p_CH0_RTERM_RX          ="0d22",    # 50 Ohm (wizard value used, does not match datasheet)
            p_CH0_RXIN_CM           ="0b11",    # CMFB (wizard value used)
            p_CH0_RXTERM_CM         ="0b11",    # RX Input (wizard value used)

            # RX CH ­— clocking
            i_CH0_RX_REFCLK         =self.ref_clk,
            o_CH0_FF_RX_PCLK        =rx_clk_o,
            i_CH0_FF_RXI_CLK        =rx_clk_i,

            p_CH0_RX_GEAR_MODE      = gearing_str,    # 1:2 gearbox
            p_CH0_FF_RX_H_CLK_EN    = gearing_str,    # enable  DIV/2 output clock
            p_CH0_FF_RX_F_CLK_DIS   = gearing_str,    # disable DIV/1 output clock

            p_CH0_AUTO_FACQ_EN      ="0b1",     # undocumented (wizard value used)
            p_CH0_AUTO_CALIB_EN     ="0b1",     # undocumented (wizard value used)
            p_CH0_BAND_THRESHOLD    ="0b00",
            p_CH0_CDR_MAX_RATE      ="2.5",     # 2.5 Gbps
            p_CH0_RX_DCO_CK_DIV     ="0b000",   # DIV/1
            p_CH0_PDEN_SEL          ="0b1",     # phase detector disabled on ~LOS
            #p_CH0_SEL_SD_RX_CLK     ="0b1",     # FIFO driven by recovered clock
            p_CH0_SEL_SD_RX_CLK     ="0b0",     # FIFO driven by FF_EBRD_CLK
            p_CH0_CTC_BYPASS        ="0b0",     # bypass CTC FIFO

            # FIFO bridge clocking
            i_CH0_FF_EBRD_CLK       =tx_clk_i,
 
            p_CH0_TXDEPRE           = "DISABLED",
            p_CH0_TXDEPOST          = "DISABLED",

            p_CH0_DCOATDCFG         ="0b00",    # begin undocumented (PCIe sample code used)
            p_CH0_DCOATDDLY         ="0b00",
            p_CH0_DCOBYPSATD        ="0b1",
            #p_CH0_DCOCALDIV         ="0b010",
            #p_CH0_DCOCTLGI          ="0b011",
            #p_CH0_DCODISBDAVOID     ="0b1",
            #p_CH0_DCOFLTDAC         ="0b00",
            #p_CH0_DCOFTNRG          ="0b010",
            #p_CH0_DCOIOSTUNE        ="0b010",
            p_CH0_DCOCALDIV         ="0b001",
            p_CH0_DCOCTLGI          ="0b010",
            p_CH0_DCODISBDAVOID     ="0b0",
            p_CH0_DCOFLTDAC         ="0b01",
            p_CH0_DCOFTNRG          ="0b111",
            p_CH0_DCOIOSTUNE        ="0b000",
            p_CH0_DCOITUNE          ="0b00",
            #p_CH0_DCOITUNE4LSB      ="0b010",
            p_CH0_DCOITUNE4LSB      ="0b111",
            p_CH0_DCOIUPDNX2        ="0b1",
            p_CH0_DCONUOFLSB        ="0b101",
            #p_CH0_DCOSCALEI         ="0b01",
            #p_CH0_DCOSTARTVAL       ="0b010",
            #p_CH0_DCOSTEP           ="0b11",    # end undocumented
            p_CH0_DCOSCALEI         ="0b00",
            p_CH0_DCOSTARTVAL       ="0b000",
            p_CH0_DCOSTEP           ="0b00",    # end undocumented

            # RX CH — link state machine
            i_CH0_FFC_SIGNAL_DETECT =rx_det,    # WARNING: If 0, then no symbol lock happens
            o_CH0_FFS_LS_SYNC_STATUS=rx_lsm,
            p_CH0_ENABLE_CG_ALIGN   ="0b1",
            p_CH0_UDF_COMMA_MASK    ="0x3ff",   # compare all 10 bits
            p_CH0_UDF_COMMA_A       ="0x283",   # K28.5 inverted, encoded in reversed order
            p_CH0_UDF_COMMA_B       ="0x17C",   # K28.5, encoded in reversed order

            p_CH0_MIN_IPG_CNT       ="0b11",    # minimum interpacket gap of 4
            p_CH0_MATCH_4_ENABLE    ="0b1",     # 4 character skip matching
            p_CH0_CC_MATCH_1        ="0x1BC",   # K28.5 Comma
            p_CH0_CC_MATCH_2        ="0x11C",   # K28.0 Skip
            p_CH0_CC_MATCH_3        ="0x11C",   # K28.0 Skip
            p_CH0_CC_MATCH_4        ="0x11C",   # K28.0 Skip

            # RX CH — loss of signal
            o_CH0_FFS_RLOS          =rx_los,
            p_CH0_RLOS_SEL          ="0b1",
            p_CH0_RX_LOS_EN         ="0b1",
            p_CH0_RX_LOS_LVL        ="0b100",   # Lattice "TBD" (wizard value used)
            p_CH0_RX_LOS_CEQ        ="0b11",    # Lattice "TBD" (wizard value used)
            p_CH0_RX_LOS_HYST_EN    ="0b0",

            # RX CH — loss of lock
            o_CH0_FFS_RLOL          =rx_lol,

            # RX CH — data
            **{"o_CH0_FF_RX_D_%d" % n: self.rx_bus[n] for n in range(self.rx_bus.width)}, # Connect outputs to RX data signals
            p_CH0_DEC_BYPASS        ="0b0", # Bypass 8b10b?

            # TX CH — power management
            #p_CH0_TPWDNB            ="0b1",
            p_CH0_TPWDNB            ="0b0",
            i_CH0_FFC_TXPWDNB       =1,

            # TX CH ­— reset
            i_CH0_FFC_LANE_TX_RST   =0,

            # TX CH ­— output

            p_CH0_TXAMPLITUDE       ="0d1000",  # 1000 mV
            p_CH0_RTERM_TX          ="0d19",    # 50 Ohm

            p_CH0_TDRV_SLICE0_CUR   ="0b011",   # 400 uA
            p_CH0_TDRV_SLICE0_SEL   ="0b01",    # main data
            p_CH0_TDRV_SLICE1_CUR   ="0b000",   # 100 uA
            p_CH0_TDRV_SLICE1_SEL   ="0b00",    # power down
            p_CH0_TDRV_SLICE2_CUR   ="0b11",    # 3200 uA
            p_CH0_TDRV_SLICE2_SEL   ="0b01",    # main data
            p_CH0_TDRV_SLICE3_CUR   ="0b11",    # 3200 uA
            p_CH0_TDRV_SLICE3_SEL   ="0b01",    # main data
            p_CH0_TDRV_SLICE4_CUR   ="0b11",    # 3200 uA
            p_CH0_TDRV_SLICE4_SEL   ="0b01",    # main data
            p_CH0_TDRV_SLICE5_CUR   ="0b00",    # 800 uA
            p_CH0_TDRV_SLICE5_SEL   ="0b00",    # power down

            # TX CH ­— clocking
            o_CH0_FF_TX_PCLK        =tx_clk_o, # Output from SERDES
            i_CH0_FF_TXI_CLK        =tx_clk_i, # Input to SERDES

            p_CH0_TX_GEAR_MODE      = gearing_str,    # 1:2 gearbox
            p_CH0_FF_TX_H_CLK_EN    = gearing_str,    # disable DIV/1 output clock
            p_CH0_FF_TX_F_CLK_DIS   = gearing_str,    # enable  DIV/2 output clock

            # TX CH — data
            **{"o_CH0_FF_TX_D_%d" % n: self.tx_bus[n] for n in range(self.tx_bus.width)}, # Connect TX SERDES inputs to the signals
            p_CH0_ENC_BYPASS        ="0b0",

            # CH0 DET
            i_CH0_FFC_PCIE_DET_EN   = pcie_det_en,
            i_CH0_FFC_PCIE_CT       = pcie_ct,
            o_CH0_FFS_PCIE_DONE     = pcie_done,
            o_CH0_FFS_PCIE_CON      = pcie_con,

            # Bit Slip
            i_CH0_FFC_CDR_EN_BITSLIP= self.slip,
            
            #i_CH0_FFC_FB_LOOPBACK   = 3,
        )
        m.submodules.dcu0.attrs["LOC"] = "DCU0"

        return m