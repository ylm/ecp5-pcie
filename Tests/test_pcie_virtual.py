from amaranth import *
from amaranth.build import *
from amaranth.lib.cdc import FFSynchronizer
from amaranth.sim import Simulator, Delay, Settle
from ecp5_pcie.virtual_phy_Gen1_x1 import VirtualPCIePhy
from ecp5_pcie.ltssm import State
from ecp5_pcie.serdes import Ctrl, compose


class VirtualPCIeTestbench(Elaboratable):
	def __init__(self):
		self.phy_virtual_u = phy_virtual_u = VirtualPCIePhy(upstream=True)
		self.serdes_u = phy_virtual_u.serdes
		self.aligner_u = phy_virtual_u.aligner
		self.phy_u = phy_virtual_u.phy

		self.phy_virtual_d = phy_virtual_d = VirtualPCIePhy(upstream=False)
		self.serdes_d = phy_virtual_d.serdes
		self.aligner_d = phy_virtual_d.aligner
		self.phy_d = phy_virtual_d.phy

		self.refclkcounter = Signal(32)
		self.phy_u.ltssm.clocks_per_ms = 128
		self.phy_d.ltssm.clocks_per_ms = 128
		self.phy_u.dll_tlp_tx.clocks_per_ms = 128
		self.phy_d.dll_tlp_tx.clocks_per_ms = 128
		self.phy_u.ltssm.simulate = True
		self.phy_d.ltssm.simulate = True

		self.send_skp = Signal()

	def elaborate(self, platform):
		m = Module()

		time = Signal(32)
		m.d.sync += time.eq(time + 1)

		m.submodules.phy_u = phy_virtual_u = self.phy_virtual_u
		m.submodules.phy_d = phy_virtual_d = self.phy_virtual_d
		#lane = ecp5_phy.aligner

		m.d.comb += self.serdes_u.lane.rx_symbol.eq(self.serdes_d.lane.tx_symbol)
		m.d.comb += self.serdes_d.lane.rx_symbol.eq(self.serdes_u.lane.tx_symbol)

		#with m.If(time == 2000):
		#	m.d.comb += self.serdes_u.lane.rx_symbol.eq(Cat(Const(Ctrl.STP, 9), Const(0, 9), Const(0, 9), Const(4, 9)))
		#with m.If(time == 2001):
		#	m.d.comb += self.serdes_u.lane.rx_symbol.eq(Cat(Const(0, 9), Const(0, 9), Const(1, 9), Const(0, 9)))
		#with m.If(time == 2002):
		#	m.d.comb += self.serdes_u.lane.rx_symbol.eq(Cat(Const(0, 9), Const(0, 9), Const(0xF, 9), Const(1, 9)))
		#with m.If(time == 2003):
		#	m.d.comb += self.serdes_u.lane.rx_symbol.eq(Cat(Const(0, 9), Const(0, 9), Const(0, 9), Const(0x4F, 9)))
		#with m.If(time == 2004):
		#	m.d.comb += self.serdes_u.lane.rx_symbol.eq(Cat(Const(0xA6, 9), Const(0x2A, 9), Const(0xFF, 9), Const(Ctrl.END, 9)))


		m.d.comb += self.send_skp.eq(self.serdes_d.lane.tx_symbol == compose([Ctrl.COM, Ctrl.SKP, Ctrl.SKP, Ctrl.SKP]))


		refclkcounter = self.refclkcounter
		m.d.sync += refclkcounter.eq(refclkcounter + 1)

		sample = Signal()
		m.d.sync += sample.eq(refclkcounter[25])

		def has_symbol(symbols, symbol):
			assert len(symbols) % 9 == 0

			has = 0

			for i in range(int(len(symbols) / 9)):
				has |= symbols[i * 9 : i * 9 + 9] == symbol
			
			return has
				 

		#if NO_DEBUG:
		#	pass
		#else:
		#	# 64t 9R 9R 9T 9T 2v 4- 6D
		#	# t = Ticks since state was entered
		#	# R = RX symbol
		#	# T = TX symbol
		#	# v = RX valid
		#	# D = DTR Temperature, does not correspond to real temperature besides the range of 21-29 °C. After that in 10 °C steps (30 = 40 °C, 31 = 50 °C etc...), see TN1266
#
		#	start_condition = (phy.ltssm.debug_state == State.L0) & (lane.rx_symbol [0:9] == Ctrl.STP) # (lane.rx_symbol [0:9] == Ctrl.STP)
#
		#	time_since_state = Signal(64)
		#	
		#	with m.If(ltssm.debug_state != State.L0):
		#		pass
		#		#m.d.rx += time_since_state.eq(0)
		#	with m.Else():
		#		m.d.rx += time_since_state.eq(time_since_state + start_condition)
		#		#with m.If(has_symbol(lane.rx_symbol, Ctrl.STP) & (phy.ltssm.debug_state == State.L0)):
		#		#	m.d.rx += time_since_state.eq(time_since_state + 1)
		#	
		#	sample_data = Signal(range(CAPTURE_DEPTH))
		#	with m.If(sample_data > 0):
		#		m.d.rx += sample_data.eq(sample_data - 1)
#
		#	with m.If(start_condition):
		#		m.d.rx += sample_data.eq(CAPTURE_DEPTH - 1)
#
#
		#	m.submodules += UARTDebugger2(uart, 19, CAPTURE_DEPTH, Cat(
		#		time_since_state,
		#		lane.rx_symbol, lane.tx_symbol,# lane.tx_symbol,
		#		lane.rx_aligned, lane.rx_locked & lane.rx_present & lane.rx_aligned, dtr.temperature, phy.ltssm.debug_state#, phy.dll.tx.started_sending, phy.dll.tx.started_sending#dtr.temperature
		#		), "rx")#, enable = (sample_data != 0) | start_condition)#, enable = phy.ltssm.debug_state == State.L0)
#
		return m

# -------------------------------------------------------------------------------------------------

if __name__ == "__main__":
	m = Module()
	m.submodules.pcie = pcie = VirtualPCIeTestbench()

	dll_status = pcie.phy_d.dll.status

	sim = Simulator(m)

	#sim.add_clock(8e-9, domain="tx")
	#sim.add_clock(8e-9, domain="rx")
	sim.add_clock(1e-8, domain="sync") # Everything in sync domain

	def process():
		a = 1
		last_state = 0
		last_retry_buffer_occupation = 0
		last_receive_buffer_occupation = 0
		last_tx_seq_num = 0
		last_rx_seq_num = 0
		ack_scheduled_last = 0
		for i in range(100 * 1000 * 24):
			state = State((yield pcie.phy_d.ltssm.debug_state)).name
			
			rx_d_data = yield pcie.phy_d.descrambled_lane.rx_symbol
			tx_d_data = yield pcie.phy_d.descrambled_lane.tx_symbol

			rx_d_data = [(rx_d_data & (0x1FF << (i * 9))) >> i * 9 for i in range(4)]
			tx_d_data = [(tx_d_data & (0x1FF << (i * 9))) >> i * 9 for i in range(4)]

			retry_buffer_occupation = yield dll_status.retry_buffer_occupation
			receive_buffer_occupation = yield dll_status.receive_buffer_occupation
			tx_seq_num = yield dll_status.tx_seq_num
			rx_seq_num = yield dll_status.rx_seq_num

			ack_scheduled = yield pcie.phy_d.dll.schedule_ack_nak
			ack_scheduled_ack = yield pcie.phy_d.dll.scheduled_ack
			ack_scheduled_id = yield pcie.phy_d.dll.scheduled_ack_nak_id

			ack_received = yield pcie.phy_u.dll.received_ack_nak
			ack_received_ack = yield pcie.phy_u.dll.received_ack
			ack_received_id = yield pcie.phy_u.dll.received_ack_nak_id
			
			print(i, end="\r")

			if Ctrl.SDP in rx_d_data:
				print(i, "DLLP received")

			if Ctrl.SDP in tx_d_data:
				print(i, "DLLP sent")

			if Ctrl.STP in rx_d_data:
				print(i, "TLP received")

			if Ctrl.STP in tx_d_data:
				print(i, "TLP sent")

			if last_retry_buffer_occupation != retry_buffer_occupation:
				print(i, "retry_buffer_occupation:", retry_buffer_occupation)
			
			if last_receive_buffer_occupation != receive_buffer_occupation:
				print(i, "receive_buffer_occupation:", receive_buffer_occupation)
			
			if last_tx_seq_num != tx_seq_num:
				print(i, "tx_seq_num:", tx_seq_num)
			
			if last_rx_seq_num != rx_seq_num:
				print(i, "rx_seq_num:", rx_seq_num)
			
			if ack_scheduled_last:
				print(i, "Ack" if ack_scheduled_ack else "Nak", "scheduled id:", ack_scheduled_id)
			
			#if ack_received:
			#	print(i, "Ack" if ack_received_ack else "Nak", "received id:", ack_received_id)
			
			if(state != last_state):
				print(state, end="				 \n")
			#if i == a:
			#	print(i, end="\r")
			#	a *= 2
			yield
			last_state = state
			last_retry_buffer_occupation = retry_buffer_occupation
			last_receive_buffer_occupation = receive_buffer_occupation
			last_tx_seq_num = tx_seq_num
			last_rx_seq_num = rx_seq_num
			ack_scheduled_last = ack_scheduled

	sim.add_sync_process(process, domain="sync")

	traces = [pcie.serdes_d.lane.tx_symbol, pcie.serdes_d.lane.rx_symbol, pcie.refclkcounter, pcie.phy_d.ltssm.debug_state, pcie.phy_u.ltssm.debug_state, pcie.phy_d.dll.debug_state, pcie.phy_u.dll.debug_state, pcie.phy_d.descrambled_lane.tx_symbol, pcie.phy_u.descrambled_lane.tx_symbol,
			 pcie.send_skp]

	with sim.write_vcd("test.vcd", "test.gtkw", traces=traces):
		sim.run()