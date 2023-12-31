from amaranth import *
from amaranth.build import *
from amaranth.lib.fifo import SyncFIFOBuffered, AsyncFIFOBuffered
from .serdes import PCIeSERDESInterface, K, Ctrl
from .ecp5_serdes import LatticeECP5PCIeSERDES


__all__ = ["LatticeECP5PCIeSERDESx4"]

class LatticeECP5PCIeSERDESx4(Elaboratable): # Based on Yumewatari
	"""
	Lattice ECP5 DCU configured in PCIe mode, 2.5 or 5 GT/s. Assumes 100 MHz reference clock on SERDES clock input pair. Only provides a single lane.
	Uses 1:4 gearing.

	Clock frequencies are 125 MHz for 5 GT/s and 62.5 MHz for 2.5 GT/s.

	Parameters
	----------
	speed5GTps : bool
		Whether to support 5 GT/s
	DCU : int
		Which DCU to use
	CH : int
		Which channel within the DCU to use

	Attributes
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
	def __init__(self, speed_5GTps=True, DCU=0, CH=0, clkfreq = 200e6, fabric_clk = False):
		self.rx_clk = Signal(name="pcie_clk")  # recovered word clock

		self.tx_clk = Signal(name="tx_slow_clk")  # generated word clock

		# The PCIe lane with all signals necessary to control it
		self.lane = PCIeSERDESInterface(4)

		self.gearing = 4

		assert DCU == 0 or DCU == 1
		assert CH == 0 or CH == 1
		
		self.DCU = DCU
		self.CH = CH

		# Bit Slip
		self.slip = Signal()

		self.speed_5GTps = speed_5GTps

		#self.__serdes = LatticeECP5PCIeSERDES(2, speed_5GTps = self.speed_5GTps, DCU=self.DCU, CH=self.CH)
		self.__serdes = DomainRenamer({"tx": "txf"})(LatticeECP5PCIeSERDES(2, speed_5GTps = self.speed_5GTps, DCU=self.DCU, CH=self.CH, clkfreq=clkfreq, fabric_clk=fabric_clk))
		self.serdes = self.__serdes # For testing

		self.lane.frequency     = int(self.__serdes.lane.frequency / 2)
		self.lane.speed         = self.__serdes.lane.speed
		self.lane.use_speed     = self.__serdes.lane.use_speed

		self.debug              = self.__serdes.debug

		self.lane.rx_invert     = self.__serdes.lane.rx_invert
		self.lane.rx_align      = self.__serdes.lane.rx_align
		self.lane.rx_aligned    = self.__serdes.lane.rx_aligned
		self.lane.rx_locked     = self.__serdes.lane.rx_locked
		self.lane.rx_present    = self.__serdes.lane.rx_present

		self.lane.tx_locked     = self.__serdes.lane.tx_locked
		#self.lane.tx_e_idle     = self.__serdes.lane.tx_e_idle

		self.lane.det_enable    = self.__serdes.lane.det_enable
		self.lane.det_valid     = self.__serdes.lane.det_valid
		self.lane.det_status    = self.__serdes.lane.det_status
		self.slip               = self.__serdes.slip

		self.lane.reset_done    = self.__serdes.lane.reset_done
	
	def elaborate(self, platform: Platform) -> Module:
		m = Module()


		m.submodules.serdes = serdes = self.__serdes

		m.submodules += self.lane

		#m.d.comb += serdes.lane.speed.eq(self.lane.speed)

		m.d.comb += serdes.lane.reset.eq(self.lane.reset)

		data_width = len(serdes.lane.rx_symbol)

		m.domains.rxf = ClockDomain()
		m.domains.txf = ClockDomain()
		m.d.comb += [
			#ClockSignal("sync").eq(serdes.refclk),
			ClockSignal("rxf").eq(serdes.rx_clk),
			ClockSignal("txf").eq(serdes.tx_clk),
		]

		platform.add_clock_constraint(self.rx_clk, 125e6 if self.speed_5GTps else 625e5) # For NextPNR, set the maximum clock frequency such that errors are given
		platform.add_clock_constraint(self.tx_clk, 125e6 if self.speed_5GTps else 625e5)

		m.submodules.lane = lane = PCIeSERDESInterface(4) # TODO: Uhh is this supposed to be here? // I think it might be the fast lane

		# IF SOMETHING IS BROKE: Check if the TX actually transmits good data and not order-swapped data
		# TODO: Maybe use hardware divider? Though this seems to be fine
		m.d.rxf += self.rx_clk.eq(~self.rx_clk)

		with m.If(~self.rx_clk):
			m.d.rxf += lane.rx_symbol   [data_width     :data_width     * 2 ].eq(serdes.lane.rx_symbol)
			m.d.rxf += lane.rx_valid    [serdes.gearing :serdes.gearing * 2 ].eq(serdes.lane.rx_valid)
		with m.Else():
			m.d.rxf += lane.rx_symbol   [0:data_width       ].eq(serdes.lane.rx_symbol)
			m.d.rxf += lane.rx_valid    [0:serdes.gearing   ].eq(serdes.lane.rx_valid)

			# To ensure that it outputs consistent data
			# m.d.rxf += self.lane.rx_symbol.eq(lane.rx_symbol)
			# m.d.rxf += self.lane.rx_valid.eq(lane.rx_valid)

		m.d.txf += self.tx_clk.eq(~self.tx_clk)

		m.d.txf += serdes.lane.tx_symbol    .eq(Mux(self.tx_clk, lane.tx_symbol     [data_width    :data_width * 2      ],  lane.tx_symbol  [0:data_width]))
		m.d.txf += serdes.lane.tx_disp      .eq(Mux(self.tx_clk, lane.tx_disp       [serdes.gearing:serdes.gearing * 2  ],  lane.tx_disp    [0:serdes.gearing]))
		m.d.txf += serdes.lane.tx_set_disp  .eq(Mux(self.tx_clk, lane.tx_set_disp   [serdes.gearing:serdes.gearing * 2  ],  lane.tx_set_disp[0:serdes.gearing]))
		m.d.txf += serdes.lane.tx_e_idle    .eq(Mux(self.tx_clk, lane.tx_e_idle     [serdes.gearing:serdes.gearing * 2  ],  lane.tx_e_idle  [0:serdes.gearing]))


		# CDC
		# TODO: Keep the SyncFIFO? Its faster but is it reliable?
		#rx_fifo = m.submodules.rx_fifo = AsyncFIFOBuffered(width=(data_width + serdes.gearing) * 2, depth=4, r_domain="rx", w_domain="rxf")
		rx_fifo = m.submodules.rx_fifo = DomainRenamer("rxf")(SyncFIFOBuffered(width=(data_width + serdes.gearing) * 2, depth=4))
		m.d.rxf += rx_fifo.w_data.eq(Cat(lane.rx_symbol, lane.rx_valid))
		m.d.comb += Cat(self.lane.rx_symbol, self.lane.rx_valid).eq(rx_fifo.r_data)
		m.d.comb += rx_fifo.r_en.eq(1)
		m.d.rxf += rx_fifo.w_en.eq(self.rx_clk)

		#tx_fifo = m.submodules.tx_fifo = AsyncFIFOBuffered(width=(data_width + serdes.gearing * 3) * 2, depth=4, r_domain="txf", w_domain="tx")
		tx_fifo = m.submodules.tx_fifo = DomainRenamer("txf")(SyncFIFOBuffered(width=(data_width + serdes.gearing * 3) * 2, depth=4))
		m.d.comb += tx_fifo.w_data.eq(Cat(self.lane.tx_symbol, self.lane.tx_set_disp, self.lane.tx_disp, self.lane.tx_e_idle))
		m.d.txf  += Cat(lane.tx_symbol, lane.tx_set_disp, lane.tx_disp, lane.tx_e_idle).eq(tx_fifo.r_data)
		m.d.txf  += tx_fifo.r_en.eq(self.tx_clk)
		m.d.comb += tx_fifo.w_en.eq(1)
		#m.d.txf  += Cat(lane.tx_symbol, lane.tx_set_disp, lane.tx_disp, lane.tx_e_idle).eq(Cat(self.lane.tx_symbol, self.lane.tx_set_disp, self.lane.tx_disp, self.lane.tx_e_idle))

		return m