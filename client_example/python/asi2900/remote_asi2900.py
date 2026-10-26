# REMOTE_ASI2900.PY
#
# Standalone ASI2900 client for a WASP server.  Works on CPython 3 and
# MicroPython.  Depends on the stdlib, the sibling remote_common.py and
# module_ids.py, and wasp.client.WaspClient.
#
# State comes from the WASP update stream.  This module does not read the
# device filesystem or run shell commands.  The on-device front panel uses
# local_asi2900.LocalASI2900, which adds the boot image type, shell IP
# addresses, secondary MAC, connector names, and the pre-checkpoint snapshot.
#
# Run from a directory that contains this file, remote_common.py,
# module_ids.py, and the vendored wasp package:
#   python3 remote_asi2900.py <host> [port]

import asyncio
import sys

def _load_siblings():
	"""Import siblings sitting next to this file.

	Running the example puts this directory on sys.path already.  Importing
	asi.asi2900.remote_asi2900 does not, and this client must not import the
	device package to find the files.
	"""
	path = __file__
	slash = path.rfind('/')
	directory = path[:slash] if slash >= 0 else ''
	if directory and directory not in sys.path:
		sys.path.append(directory)
	import module_ids
	import remote_common
	return remote_common, module_ids.MODULE_IDS


remote_common, MODULE_IDS = _load_siblings()
RemoteWaspDevice = remote_common.RemoteWaspDevice

DISPLAY_STREAM_STATE_DISABLED = remote_common.DISPLAY_STREAM_STATE_DISABLED
DISPLAY_STREAM_STATE_PENDING = remote_common.DISPLAY_STREAM_STATE_PENDING
DISPLAY_STREAM_STATE_STREAMING = remote_common.DISPLAY_STREAM_STATE_STREAMING
DISPLAY_STREAM_STATE_ERROR = remote_common.DISPLAY_STREAM_STATE_ERROR

DISPLAY_CONNECTION_STATE_NONE = 0
DISPLAY_CONNECTION_STATE_DANTE = 1
DISPLAY_CONNECTION_STATE_AES67 = 2
DISPLAY_CONNECTION_STATE_ERROR = 3

_STREAM_STATUS_TO_DISPLAY = {
	'not-connected':  DISPLAY_CONNECTION_STATE_NONE,
	'dante-unicast':  DISPLAY_CONNECTION_STATE_DANTE,
	'aes67-multicast': DISPLAY_CONNECTION_STATE_AES67,
	'connecting':     DISPLAY_CONNECTION_STATE_NONE,
	'error':          DISPLAY_CONNECTION_STATE_ERROR,
	'dante-loopback': DISPLAY_CONNECTION_STATE_DANTE,
}

# device:operating_mode.profile values (wasp/app/asi2900/operating_mode.py).
# The two modes have mutually exclusive topologies: `dep` has Dante IO and no
# ICY streams, `icy` has 16 ICY stereo streams and no Dante IO / AES67.
OPERATING_MODE_ICECAST = 'icy'
OPERATING_MODE_DANTE = 'dep'

# WASP block:module.part_number -> numeric module id.
_PART_TO_MODULE_ID = {
	'ASI1931': MODULE_IDS.ASI1931_LINEOUT,
	'ASI1932': MODULE_IDS.ASI1932_MICLINEIN,
	'ASI1933': MODULE_IDS.ASI1933_UBMICLINEIN,
	'ASI1941': MODULE_IDS.ASI1941_AESEBUOUT,
	'ASI1942': MODULE_IDS.ASI1942_AESEBUIN,
	'ASI1951': MODULE_IDS.ASI1951_GPIO_8X8,
	'ASI1952': MODULE_IDS.ASI1952_GPIO_5X5,
	'ASI1971': MODULE_IDS.ASI1971_TUNER_AMFMWB,
	'ASI1972': MODULE_IDS.ASI1972_TUNER_AMFMHDDAB,
}

# GPIO line count for ASI1951 (8) and ASI1952 (5), used until bits_state arrives.
_GPIO_COUNT_BY_MODULE_ID = {
	MODULE_IDS.ASI1951_GPIO_8X8: 8,
	MODULE_IDS.ASI1952_GPIO_5X5: 5,
}

_PART_TO_NAME = {
	'ASI1931': 'Analog Out',
	'ASI1932': 'Analog In',
	'ASI1933': 'Unbal In',
	'ASI1941': 'AES3 Out',
	'ASI1942': 'AES3 In',
	'ASI1951': 'GPIO',
	'ASI1952': 'GPIO',
	'ASI1971': 'AM/FM/WB',
	'ASI1972': 'AM/FM/HD',
}

_PART_TO_DIR = {
	'ASI1931': 'tx',
	'ASI1932': 'rx',
	'ASI1933': 'rx',
	'ASI1941': 'tx',
	'ASI1942': 'rx',
	'ASI1951': 'none',
	'ASI1952': 'none',
	'ASI1971': 'rx',
	'ASI1972': 'rx',
}

_TYPES_OF_INTEREST = (
	'device:hw_desc',
	'device:sw_desc',
	'ctrl:mac_interface',
	'ctrl:ipv4_interface',
	'ctrl:temperature_sensor',
	'block:module',
	'ctrl:phantom',
	'block:io',
	'ctrl:aes67_transmitter',
	'ctrl:aes67_receiver',
	'ctrl:dante_streaming_status',
	'ctrl:aes3_receiver',
	'ctrl:tuner',
	'ctrl:gpi',
	'ctrl:gpo',
	'ctrl:sample_clock',
	'ctrl:dante_settings',
	'device:operating_mode',
	'ctrl:icy_stream_tx',
	'device:fw_update_status',
)


class RemoteASI2900(RemoteWaspDevice):
	def __init__(self, operating_mode=None):
		"""Cached ASI2900 view of a WASP device.

		operating_mode: optional ``'dep'`` or ``'icy'``.  Icecast pre-sizes the
		stream panel to 32 channels and zero Dante channels so the first frame
		does not allocate 64 meters.  Callers that do not know the mode yet
		leave this unset (``'dep'``).  device:operating_mode replaces it when
		the checkpoint arrives.  The on-device subclass passes the boot profile.
		"""
		RemoteWaspDevice.__init__(self)
		# Empty until a caller that knows the boot image sets it.  WASP does
		# not publish recovery vs system.
		self.image_type = ""
		self.model_number = "ASI2900"

		self.occupied_module_slots = 0
		self.module_ids = [0] * 8
		self.module_names = [""] * 8
		self.connector_names = [""] * 8
		self.module_direction = ["rx"] * 8

		self._phantom = {}
		self._tx_channels = 32
		self._rx_channels = 32
		self._tx_conn_state = {}
		self._rx_conn_state = {}
		if operating_mode not in (OPERATING_MODE_ICECAST, OPERATING_MODE_DANTE):
			operating_mode = OPERATING_MODE_DANTE
		self._operating_mode = operating_mode
		if self._operating_mode == OPERATING_MODE_ICECAST:
			self._tx_channels = 0
			self._rx_channels = 0
			self._icy_channels = 32
		self._aesebu_status = {}   # (slot, channel) -> status_bits int
		self._gpi_bits = {}        # slot -> bits_state string e.g. "10101010"
		self._gpo_bits = {}        # slot -> bits_state string e.g. "10101010"

		# DEP (Dante) sample rate — sourced from WASP sample-rate controls.
		# The WASP server currently populates these controls from ALSA and retains
		# the last-known rate while the stream is closed.
		self._dep_sample_rate = 0
		self._dep_streaming = False

	def types_of_interest(self):
		return _TYPES_OF_INTEREST

	def _default_part_number(self):
		return 'ASI2900'

	def _store_mac_address(self, obj, value=None):
		"""Keep only the primary MAC from WASP.

		Every ctrl:mac_interface object writes mac_str[0], and a missing
		address does not clear it.  Icecast mode never receives the DEP
		network-interface update.  mac_str[1] is the on-device filesystem
		value owned by LocalASI2900.
		"""
		if value is None:
			value = obj.get('mac_address')
		if value:
			self.mac_str[0] = value.upper()

	def _reset_product(self):
		self.module_ids = [0] * 8
		self.module_names = [""] * 8
		self.module_direction = ["rx"] * 8
		self.connector_names = [""] * 8
		self.occupied_module_slots = 0
		self._phantom = {}
		self._aesebu_status = {}
		self._gpi_bits = {}
		self._gpo_bits = {}
		self._tx_channels = 0
		self._rx_channels = 0

	def _after_parse(self):
		self._rebuild_connection_state()

	def _apply_object(self, obj):
		if RemoteWaspDevice._apply_object(self, obj):
			return True
		t = obj.get('_type')
		if t == 'block:module':
			slot = obj.get('slot', 0) - 1
			if 0 <= slot < 8:
				pn = obj.get('part_number', '')
				self.module_ids[slot] = _PART_TO_MODULE_ID.get(pn, 0)
				self.module_names[slot] = _PART_TO_NAME.get(pn, '?')
				self.module_direction[slot] = _PART_TO_DIR.get(pn, 'none')
				if slot + 1 > self.occupied_module_slots:
					self.occupied_module_slots = slot + 1
			return True
		if t == 'ctrl:phantom':
			slot = obj.get('_module_slot', 0) - 1
			ch = obj.get('_idx_on_slot', 1) - 1
			self._phantom[(slot, ch)] = obj.get('active', False)
			return True
		if t == 'block:io':
			io_type = obj.get('io_type')
			if io_type == 'dante':
				d = obj.get('io_dir')
				if d == 'out':
					self._tx_channels += 1
				elif d == 'in':
					self._rx_channels += 1
			elif io_type == 'icy':
				self._icy_channels += 1
			return True
		if t == 'device:operating_mode':
			profile = obj.get('profile')
			if profile in (OPERATING_MODE_ICECAST, OPERATING_MODE_DANTE):
				self._operating_mode = profile
			return True
		if t == 'ctrl:aes3_receiver':
			slot = obj.get('_module_slot', 0) - 1
			idx = obj.get('_idx_on_slot', 1) - 1  # 0-based stereo pair
			bits = obj.get('status_bits', 0)
			self._aesebu_status[(slot, idx * 2)] = bits
			self._aesebu_status[(slot, idx * 2 + 1)] = bits
			return True
		if t in ('ctrl:gpi', 'ctrl:gpo'):
			parent_id = obj.get('_parent')
			if parent_id is not None and parent_id < len(self._objects):
				parent = self._objects[parent_id]
				if parent:
					slot = parent.get('_module_slot', 0) - 1
					if 0 <= slot < 8:
						bits = obj.get('bits_state', '')
						if t == 'ctrl:gpi':
							self._gpi_bits[slot] = bits
						else:
							self._gpo_bits[slot] = bits
			return True
		if t in ('ctrl:sample_clock', 'ctrl:dante_settings'):
			self._update_dep_sample_rate_from_obj(obj)
			return True
		return False

	def _rebuild_connection_state(self):
		self._tx_conn_state = {}
		self._rx_conn_state = {}

		objects = self._objects
		n = len(objects)
		for obj in objects:
			if obj is None:
				continue
			otype = obj.get('_type')
			if otype == 'ctrl:dante_streaming_status':
				parent_id = obj.get('_parent')
				if parent_id is not None and parent_id < n:
					parent = objects[parent_id]
					if parent and parent.get('_type') == 'block:io':
						ch = parent.get('io_idx', -1)
						state = _STREAM_STATUS_TO_DISPLAY.get(obj.get('streaming_status'), DISPLAY_CONNECTION_STATE_NONE)
						if parent.get('io_dir') == 'out':
							self._tx_conn_state[ch] = state
						else:
							self._rx_conn_state[ch] = state
			elif otype == 'ctrl:aes67_receiver':
				# Fallback for firmware without ctrl:dante_streaming_status
				parent_id = obj.get('_parent')
				if parent_id is not None and parent_id < n:
					parent = objects[parent_id]
					if parent and parent.get('_type') == 'block:io':
						ch = parent.get('io_idx', -1)
						if obj.get('status') == 'connected' and ch not in self._rx_conn_state:
							self._rx_conn_state[ch] = DISPLAY_CONNECTION_STATE_AES67

	def _on_group_property(self, obj, prop, value):
		if RemoteWaspDevice._on_group_property(self, obj, prop, value):
			return True
		otype = obj.get('_type')
		if prop == 'active' and otype == 'ctrl:phantom':
			slot = obj.get('_module_slot', 0) - 1
			ch = obj.get('_idx_on_slot', 1) - 1
			self._phantom[(slot, ch)] = value
			return True
		if prop == 'streaming_status' and otype == 'ctrl:dante_streaming_status':
			self._rebuild_connection_state()
			return True
		if prop == 'status' and otype in (
			'ctrl:aes67_transmitter', 'ctrl:aes67_receiver',
		):
			self._rebuild_connection_state()
			return True
		if prop == 'status_bits' and otype == 'ctrl:aes3_receiver':
			slot = obj.get('_module_slot', 0) - 1
			idx = obj.get('_idx_on_slot', 1) - 1
			self._aesebu_status[(slot, idx * 2)] = value
			self._aesebu_status[(slot, idx * 2 + 1)] = value
			return True
		if prop == 'bits_state' and otype in ('ctrl:gpi', 'ctrl:gpo'):
			slot = self._gpio_slot_for_obj(obj)
			if slot is not None:
				if otype == 'ctrl:gpi':
					self._gpi_bits[slot] = value
				else:
					self._gpo_bits[slot] = value
			return True
		if prop in ('current_sample_rate', 'sample_rate') and otype in (
			'ctrl:sample_clock', 'ctrl:dante_settings',
		):
			self._update_dep_sample_rate_from_obj(obj)
			return True
		if prop == 'profile' and otype == 'device:operating_mode':
			if value in (OPERATING_MODE_ICECAST, OPERATING_MODE_DANTE):
				self._operating_mode = value
			return True
		return False

	def _handle_incremental(self, obj, changed):
		if RemoteWaspDevice._handle_incremental(self, obj, changed):
			return True
		otype = obj.get('_type')
		if otype == 'ctrl:phantom':
			if 'active' in changed:
				slot = obj.get('_module_slot', 0) - 1
				ch = obj.get('_idx_on_slot', 1) - 1
				self._phantom[(slot, ch)] = changed['active']
			return True
		if otype == 'ctrl:dante_streaming_status':
			if 'streaming_status' in changed:
				self._rebuild_connection_state()
			return True
		if otype in ('ctrl:aes67_transmitter', 'ctrl:aes67_receiver'):
			if 'status' in changed:
				self._rebuild_connection_state()
			return True
		if otype == 'ctrl:aes3_receiver':
			if 'status_bits' in changed:
				slot = obj.get('_module_slot', 0) - 1
				idx = obj.get('_idx_on_slot', 1) - 1
				bits = changed['status_bits']
				self._aesebu_status[(slot, idx * 2)] = bits
				self._aesebu_status[(slot, idx * 2 + 1)] = bits
			return True
		if otype in ('ctrl:gpi', 'ctrl:gpo'):
			if 'bits_state' in changed:
				slot = self._gpio_slot_for_obj(obj)
				if slot is not None:
					if otype == 'ctrl:gpi':
						self._gpi_bits[slot] = changed['bits_state']
					else:
						self._gpo_bits[slot] = changed['bits_state']
			return True
		if otype in ('ctrl:sample_clock', 'ctrl:dante_settings'):
			if 'current_sample_rate' in changed or 'sample_rate' in changed:
				self._update_dep_sample_rate_from_obj(obj)
			return True
		if otype == 'device:operating_mode':
			if 'profile' in changed:
				if changed['profile'] in (OPERATING_MODE_ICECAST, OPERATING_MODE_DANTE):
					self._operating_mode = changed['profile']
			return True
		return False

	def _update_dep_sample_rate_from_obj(self, obj):
		"""Update cached Dante sample rate from WASP sample-rate controls."""
		rate = 0
		if obj.get('_type') == 'ctrl:dante_settings':
			rate = obj.get('current_sample_rate') or obj.get('sample_rate') or 0
		elif obj.get('_type') == 'ctrl:sample_clock':
			rate = obj.get('current_sample_rate') or 0
		try:
			rate = int(rate)
		except (ValueError, TypeError):
			return
		self._dep_sample_rate = rate if rate > 0 else 0
		self._dep_streaming = self._dep_sample_rate > 0 and self.wasp_connected

	def _gpio_slot_for_obj(self, obj):
		"""Return 0-based slot for a ctrl:gpi/gpo object, or None."""
		parent_id = obj.get('_parent')
		if parent_id is not None and parent_id < len(self._objects):
			parent = self._objects[parent_id]
			if parent:
				slot = parent.get('_module_slot', 0) - 1
				if 0 <= slot < 8:
					return slot
		return None

	def phantom_state(self, slot, channel):
		return self._phantom.get((slot, channel), None)

	def occupied_slots(self):
		return self.occupied_module_slots

	def module_id(self, slot):
		slot = max(0, min(7, slot))
		return self.module_ids[slot]

	def module_name(self, slot):
		slot = max(0, min(7, slot))
		return self.module_names[slot]

	def connector_name(self, slot):
		slot = max(0, min(7, slot))
		return self.connector_names[slot]

	def dep_sample_rate(self):
		return self._dep_sample_rate

	def dep_streaming(self):
		self._dep_streaming = self._dep_sample_rate > 0 and self.wasp_connected
		return self._dep_streaming

	def tx_channels(self):
		return self._tx_channels

	def rx_channels(self):
		return self._rx_channels

	def operating_mode(self):
		"""`'dep'` (Dante) or `'icy'` (Icecast) — see device:operating_mode."""
		return self._operating_mode

	def icy_channels(self):
		"""Icecast stream channel count (2 per stereo stream); 0 in Dante mode."""
		return self._icy_channels

	def connection_state(self, direction, channel):
		if direction == 'tx':
			return self._tx_conn_state.get(channel, DISPLAY_CONNECTION_STATE_NONE)
		return self._rx_conn_state.get(channel, DISPLAY_CONNECTION_STATE_NONE)

	def aesebu_rx_valid(self, slot, channel):
		"""Return True if AES/EBU receiver is locked with no errors,
		False if not locked or errors present, or None if no
		AES/EBU receiver exists for this slot/channel."""
		key = (slot, channel)
		if key not in self._aesebu_status:
			return None
		return self._aesebu_status[key] == 0

	# GPIO — sourced from WASP ctrl:gpi / ctrl:gpo bits_state
	def _gpio_count_for_slot(self, slot):
		"""Return expected GPIO count from module ID (8 for ASI1951, 5 for ASI1952)."""
		mid = self.module_ids[slot] if 0 <= slot < 8 else 0
		return _GPIO_COUNT_BY_MODULE_ID.get(mid, 0)

	def num_gpio_inputs(self, slot):
		bits = self._gpi_bits.get(slot, '')
		return len(bits) if bits else self._gpio_count_for_slot(slot)

	def num_gpio_outputs(self, slot):
		bits = self._gpo_bits.get(slot, '')
		return len(bits) if bits else self._gpio_count_for_slot(slot)

	def get_gpio_input(self, slot, number):
		bits = self._gpi_bits.get(slot, '')
		if 0 <= number < len(bits):
			return 1 if bits[number] == '1' else 0
		return 0

	def set_gpio_output(self, slot, number, value):
		"""Set a single GPIO output bit via WASP."""
		bits = self._gpo_bits.get(slot, '')
		if not (0 <= number < len(bits)):
			return
		new_bits = bits[:number] + ('1' if value else '0') + bits[number + 1:]
		self._gpo_bits[slot] = new_bits
		self._patch_gpo(slot, new_bits)

	def get_gpio_output(self, slot, number):
		bits = self._gpo_bits.get(slot, '')
		if 0 <= number < len(bits):
			return 1 if bits[number] == '1' else 0
		return 0

	def _patch_gpo(self, slot, bits_state):
		"""Push a GPO bits_state update to the WASP server."""
		if self._wasp is None:
			return
		for obj in self._objects:
			if obj is None:
				continue
			if obj.get('_type') != 'ctrl:gpo':
				continue
			parent_id = obj.get('_parent')
			if parent_id is not None and parent_id < len(self._objects):
				parent = self._objects[parent_id]
				if parent and parent.get('_module_slot', 0) - 1 == slot:
					asyncio.ensure_future(
						self._wasp.patch_property(obj['_id'], 'bits_state', bits_state)
					)
					return


def _summarize(dev):
	name = dev.full_model_name() or dev.model_number
	print('model: ' + name)
	print('mode: ' + dev.operating_mode())
	print('slots: %d' % dev.occupied_slots())
	slot = 0
	while slot < dev.occupied_slots():
		print('  %d %s' % (slot, dev.module_name(slot)))
		slot += 1


def main(argv):
	"""CLI entry: ``remote_asi2900.py <host> [port]``."""
	return remote_common.client_main(
		argv, 'remote_asi2900.py <host> [port]', RemoteASI2900, _summarize)


if __name__ == '__main__':
	import sys
	raise SystemExit(main(sys.argv))
