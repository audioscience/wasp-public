# REMOTE_ASI2900.PY
#
# Standalone ASI2900 client for a WASP server.  Works on CPython 3 and
# MicroPython.  Depends on the stdlib, the sibling module_ids.py, and
# wasp.client.WaspClient.
#
# State comes from the WASP update stream.  This module does not read the
# device filesystem or run shell commands.  The on-device front panel uses
# local_asi2900.LocalASI2900, which adds the boot image type, shell IP
# addresses, secondary MAC, connector names, and the pre-checkpoint snapshot.
#
# Run from a directory that contains this file, module_ids.py, and the
# vendored wasp package:
#   python3 remote_asi2900.py <host> [port]

import asyncio
import gc
import sys

def _load_module_ids():
	"""Import MODULE_IDS from module_ids.py sitting next to this file.

	Running the example puts this directory on sys.path already.  Importing
	asi.asi2900.remote_asi2900 does not, and this client must not import the
	device package to find the file.
	"""
	path = __file__
	slash = path.rfind('/')
	directory = path[:slash] if slash >= 0 else ''
	if directory and directory not in sys.path:
		sys.path.append(directory)
	import module_ids
	return module_ids.MODULE_IDS


MODULE_IDS = _load_module_ids()

try:
	import logging
	logger = logging.getLogger()
except ImportError:
	class _NullLogger:
		def info(self, *args, **kwargs):
			return None
	logger = _NullLogger()


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

# Icecast (ICY) per-stream display state.  Mirrors statusOf() in the web UI
# (web-ui-v2 panels/streaming/StreamSettingsPopover.tsx) so the front panel and
# the browser agree: errors win, then disabled, then enabled-but-not-yet-live.
DISPLAY_STREAM_STATE_DISABLED = 0
DISPLAY_STREAM_STATE_PENDING = 1
DISPLAY_STREAM_STATE_STREAMING = 2
DISPLAY_STREAM_STATE_ERROR = 3

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

_TYPES_OF_INTEREST = frozenset((
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
))


def _stream_state_of(obj):
	"""Map a ctrl:icy_stream_tx object to a DISPLAY_STREAM_STATE_* value."""
	if obj.get('errors'):
		return DISPLAY_STREAM_STATE_ERROR
	if not obj.get('enabled'):
		return DISPLAY_STREAM_STATE_DISABLED
	if not obj.get('streaming'):
		return DISPLAY_STREAM_STATE_PENDING
	return DISPLAY_STREAM_STATE_STREAMING


def _stream_idx_of(obj):
	"""Stereo-stream index for a ctrl:icy_stream_tx object."""
	idx = obj.get('_icy_stream_idx')
	if idx is None:
		idx = obj.get('_idx_on_slot', 0)
	return idx


class RemoteASI2900:
	def __init__(self, operating_mode=None):
		"""Cached ASI2900 view of a WASP device.

		operating_mode: optional ``'dep'`` or ``'icy'``.  Icecast pre-sizes the
		stream panel to 32 channels and zero Dante channels so the first frame
		does not allocate 64 meters.  Callers that do not know the mode yet
		leave this unset (``'dep'``).  device:operating_mode replaces it when
		the checkpoint arrives.  The on-device subclass passes the boot profile.
		"""
		# Empty until a caller that knows the boot image sets it.  WASP does
		# not publish recovery vs system.
		self.image_type = ""

		# Filled from ctrl:ipv4_interface.address when that property is a
		# non-empty string.  DHCP addresses are often null on the wire.
		self.ip_str = ["0.0.0.0", "0.0.0.0"]

		self.model_number = "ASI2900"
		self.hw_rev = ""
		self.serial = ""
		self.fw_rev = ""
		self.ext_model_name = ""
		self.ext_model_number = ""
		self.mac_str = ["00:00:00:00:00:00", "00:00:00:00:00:00"]

		self.occupied_module_slots = 0
		self.module_ids = [0] * 8
		self.module_names = [""] * 8
		self.connector_names = [""] * 8
		self.module_direction = ["rx"] * 8

		self._temperature = 0.0
		self._phantom = {}
		self._tx_channels = 32
		self._rx_channels = 32
		self._tx_conn_state = {}
		self._rx_conn_state = {}
		# Icecast streaming.  `_icy_channels` counts block:io with io_type='icy'
		# (2 per stereo stream); `_icy_stream_state` is keyed by stream index.
		self._icy_channels = 0
		self._icy_stream_state = {}
		if operating_mode not in (OPERATING_MODE_ICECAST, OPERATING_MODE_DANTE):
			operating_mode = OPERATING_MODE_DANTE
		self._operating_mode = operating_mode
		if self._operating_mode == OPERATING_MODE_ICECAST:
			self._tx_channels = 0
			self._rx_channels = 0
			self._icy_channels = 32
		self._aesebu_status = {}   # (slot, channel) -> status_bits int
		self._tuner_band = {}      # (slot, pair_idx) -> band string ("AM", "FM", ...)
		self._gpi_bits = {}        # slot -> bits_state string e.g. "10101010"
		self._gpo_bits = {}        # slot -> bits_state string e.g. "10101010"

		# DEP (Dante) sample rate — sourced from WASP sample-rate controls.
		# The WASP server currently populates these controls from ALSA and retains
		# the last-known rate while the stream is closed.
		self._dep_sample_rate = 0
		self._dep_streaming = False

		self._wasp = None
		self._objects = []
		self._checkpoint_receiving = False

	@property
	def wasp_connected(self):
		"""True when the WASP update stream is connected."""
		return self._wasp is not None and self._wasp.connected

	async def init(self, host='127.0.0.1', port=80):
		"""Start the WASP update stream in the background.  Returns
		immediately; cached values are updated when the checkpoint
		arrives."""
		from wasp.client import WaspClient
		self._wasp = WaspClient(
			host, port,
			on_update=self._on_wasp_update,
			on_connected=self._prepare_for_wasp_checkpoint,
			exclude_types=['ctrl:meter'],
			checkpoint_mode='chunks',
			log=lambda msg: logger.info('WASP: %s', msg),
		)
		asyncio.create_task(self._wasp.run())

	# ---- WASP data parsing ----

	def _prepare_for_wasp_checkpoint(self):
		"""Release the previous checkpoint before WaspClient reads a new one."""
		if self._objects:
			self._objects = []
		self._checkpoint_receiving = False
		gc.collect()

	def _keep_checkpoint_obj(self, obj):
		if obj.get('_type') not in _TYPES_OF_INTEREST:
			return None
		return obj

	def _on_checkpoint_begin(self, body):
		count = body.get('count', 0) if isinstance(body, dict) else 0
		self._objects = [None] * count
		self._checkpoint_receiving = True
		gc.collect()

	def _on_checkpoint_chunk(self, body):
		if not self._checkpoint_receiving or not isinstance(body, dict):
			return
		start = body.get('start', 0)
		objects = body.get('objects', [])
		if not isinstance(objects, list):
			return
		end = start + len(objects)
		if end > len(self._objects):
			self._objects.extend([None] * (end - len(self._objects)))
		for i in range(len(objects)):
			obj = objects[i]
			if isinstance(obj, dict):
				self._objects[start + i] = self._keep_checkpoint_obj(obj)
			else:
				self._objects[start + i] = None

	def _on_checkpoint_end(self):
		if not self._checkpoint_receiving:
			return
		self._checkpoint_receiving = False
		self._parse_all()
		gc.collect()

	def _parse_all(self):
		"""Populate all cached fields from the object list in a single pass."""
		self.module_ids = [0] * 8
		self.module_names = [""] * 8
		self.module_direction = ["rx"] * 8
		self.connector_names = [""] * 8
		self.occupied_module_slots = 0
		ipv4_idx = 0
		self._phantom = {}
		self._aesebu_status = {}
		self._tuner_band = {}
		self._gpi_bits = {}
		self._gpo_bits = {}
		self._tx_channels = 0
		self._rx_channels = 0
		self._icy_channels = 0
		self._icy_stream_state = {}

		for obj in self._objects:
			if obj is None:
				continue
			t = obj.get('_type')
			if t == 'device:hw_desc':
				self.model_number = obj.get('part_number', 'ASI2900')
				self.hw_rev = obj.get('revision', '')
				self.serial = obj.get('serial_number', '')
				self.ext_model_name = obj.get('name', '')
				parts = self.ext_model_name.split()
				if len(parts) > 2:
					self.ext_model_number = self.model_number + '-' + '-'.join(parts[2:])
				else:
					self.ext_model_number = self.model_number
			elif t == 'device:sw_desc':
				self.fw_rev = obj.get('version', '')
			elif t == 'ctrl:mac_interface':
				# Keep a MAC already cached when this object has none.  Icecast
				# mode never receives the DEP network-interface update.
				mac = obj.get('mac_address')
				if mac:
					self.mac_str[0] = mac.upper()
			elif t == 'ctrl:ipv4_interface':
				if ipv4_idx < 2:
					addr = obj.get('address')
					if isinstance(addr, str) and addr:
						self.ip_str[ipv4_idx] = addr
				ipv4_idx += 1
			elif t == 'ctrl:temperature_sensor':
				try:
					self._temperature = float(obj.get('temperature', 0))
				except (ValueError, TypeError):
					pass
			elif t == 'block:module':
				slot = obj.get('slot', 0) - 1
				if 0 <= slot < 8:
					pn = obj.get('part_number', '')
					self.module_ids[slot] = _PART_TO_MODULE_ID.get(pn, 0)
					self.module_names[slot] = _PART_TO_NAME.get(pn, '?')
					self.module_direction[slot] = _PART_TO_DIR.get(pn, 'none')
					if slot + 1 > self.occupied_module_slots:
						self.occupied_module_slots = slot + 1
			elif t == 'ctrl:phantom':
				slot = obj.get('_module_slot', 0) - 1
				ch = obj.get('_idx_on_slot', 1) - 1
				self._phantom[(slot, ch)] = obj.get('active', False)
			elif t == 'block:io':
				io_type = obj.get('io_type')
				if io_type == 'dante':
					d = obj.get('io_dir')
					if d == 'out':
						self._tx_channels += 1
					elif d == 'in':
						self._rx_channels += 1
				elif io_type == 'icy':
					self._icy_channels += 1
			elif t == 'device:operating_mode':
				profile = obj.get('profile')
				if profile in (OPERATING_MODE_ICECAST, OPERATING_MODE_DANTE):
					self._operating_mode = profile
			elif t == 'ctrl:icy_stream_tx':
				self._icy_stream_state[_stream_idx_of(obj)] = _stream_state_of(obj)
			elif t == 'ctrl:aes3_receiver':
				slot = obj.get('_module_slot', 0) - 1
				idx = obj.get('_idx_on_slot', 1) - 1  # 0-based stereo pair
				bits = obj.get('status_bits', 0)
				self._aesebu_status[(slot, idx * 2)] = bits
				self._aesebu_status[(slot, idx * 2 + 1)] = bits
			elif t == 'ctrl:tuner':
				slot = obj.get('_module_slot', 0) - 1
				pair_idx = obj.get('_idx_on_slot', 1) - 1  # 0-based stereo pair
				band = obj.get('band', '')
				self._tuner_band[(slot, pair_idx)] = band
			elif t in ('ctrl:gpi', 'ctrl:gpo'):
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
			elif t in ('ctrl:sample_clock', 'ctrl:dante_settings'):
				self._update_dep_sample_rate_from_obj(obj)

		self._rebuild_connection_state()

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

	# ---- Update stream handler ----

	def _on_wasp_update(self, event_type, path, body):
		"""Called by WaspClient when the update stream delivers an event."""
		if event_type == 'update:checkpoint':
			if not isinstance(body, list):
				return
			for i in range(len(body)):
				if isinstance(body[i], dict):
					body[i] = self._keep_checkpoint_obj(body[i])
				else:
					body[i] = None
			self._objects = body
			self._checkpoint_receiving = False
			self._parse_all()

		elif event_type == 'update:checkpoint_begin':
			self._on_checkpoint_begin(body)

		elif event_type == 'update:checkpoint_chunk':
			self._on_checkpoint_chunk(body)

		elif event_type == 'update:checkpoint_end':
			self._on_checkpoint_end()

		elif event_type == 'update:obj':
			obj_id = int(path.rsplit('/', 1)[-1])
			objects = self._objects
			if obj_id < len(objects):
				obj = objects[obj_id]
				if obj is not None:
					obj.update(body)
					self._handle_incremental(obj, body)

		elif event_type == 'update:group_prefix':
			prop = body.get('prop')
			if not prop:
				return
			objects = self._objects
			n = len(objects)
			for obj_id_str, value in body.get('body', {}).items():
				obj_id = int(obj_id_str)
				if obj_id < n:
					obj = objects[obj_id]
					if obj is not None:
						obj[prop] = value
						if prop == 'temperature' and obj.get('_type') == 'ctrl:temperature_sensor':
							try:
								self._temperature = float(value)
							except (ValueError, TypeError):
								pass
						elif prop == 'active' and obj.get('_type') == 'ctrl:phantom':
							slot = obj.get('_module_slot', 0) - 1
							ch = obj.get('_idx_on_slot', 1) - 1
							self._phantom[(slot, ch)] = value
						elif prop == 'streaming_status' and obj.get('_type') == 'ctrl:dante_streaming_status':
							self._rebuild_connection_state()
						elif prop == 'status' and obj.get('_type') in (
							'ctrl:aes67_transmitter', 'ctrl:aes67_receiver',
						):
							self._rebuild_connection_state()
						elif prop == 'status_bits' and obj.get('_type') == 'ctrl:aes3_receiver':
							slot = obj.get('_module_slot', 0) - 1
							idx = obj.get('_idx_on_slot', 1) - 1
							self._aesebu_status[(slot, idx * 2)] = value
							self._aesebu_status[(slot, idx * 2 + 1)] = value
						elif prop == 'band' and obj.get('_type') == 'ctrl:tuner':
							slot = obj.get('_module_slot', 0) - 1
							pair_idx = obj.get('_idx_on_slot', 1) - 1
							self._tuner_band[(slot, pair_idx)] = value
						elif prop == 'bits_state' and obj.get('_type') in ('ctrl:gpi', 'ctrl:gpo'):
							slot = self._gpio_slot_for_obj(obj)
							if slot is not None:
								if obj.get('_type') == 'ctrl:gpi':
									self._gpi_bits[slot] = value
								else:
									self._gpo_bits[slot] = value
						elif prop in ('current_sample_rate', 'sample_rate') and obj.get('_type') in (
							'ctrl:sample_clock', 'ctrl:dante_settings',
						):
							self._update_dep_sample_rate_from_obj(obj)
						elif prop in ('enabled', 'streaming', 'errors') and obj.get('_type') == 'ctrl:icy_stream_tx':
							self._icy_stream_state[_stream_idx_of(obj)] = _stream_state_of(obj)
						elif prop == 'profile' and obj.get('_type') == 'device:operating_mode':
							if value in (OPERATING_MODE_ICECAST, OPERATING_MODE_DANTE):
								self._operating_mode = value
						elif prop == 'address' and obj.get('_type') == 'ctrl:ipv4_interface':
							self._store_ipv4_address(obj)
						elif prop == 'mac_address' and obj.get('_type') == 'ctrl:mac_interface':
							if value:
								self.mac_str[0] = value.upper()

	def _handle_incremental(self, obj, changed):
		"""Process an incremental property update for a single object."""
		otype = obj.get('_type')
		if otype == 'ctrl:temperature_sensor' and 'temperature' in changed:
			try:
				self._temperature = float(changed['temperature'])
			except (ValueError, TypeError):
				pass
		elif otype == 'ctrl:phantom' and 'active' in changed:
			slot = obj.get('_module_slot', 0) - 1
			ch = obj.get('_idx_on_slot', 1) - 1
			self._phantom[(slot, ch)] = changed['active']
		elif otype == 'ctrl:dante_streaming_status':
			if 'streaming_status' in changed:
				self._rebuild_connection_state()
		elif otype in ('ctrl:aes67_transmitter', 'ctrl:aes67_receiver'):
			if 'status' in changed:
				self._rebuild_connection_state()
		elif otype == 'ctrl:aes3_receiver' and 'status_bits' in changed:
			slot = obj.get('_module_slot', 0) - 1
			idx = obj.get('_idx_on_slot', 1) - 1
			bits = changed['status_bits']
			self._aesebu_status[(slot, idx * 2)] = bits
			self._aesebu_status[(slot, idx * 2 + 1)] = bits
		elif otype == 'ctrl:tuner' and 'band' in changed:
			slot = obj.get('_module_slot', 0) - 1
			pair_idx = obj.get('_idx_on_slot', 1) - 1
			self._tuner_band[(slot, pair_idx)] = changed['band']
		elif otype in ('ctrl:gpi', 'ctrl:gpo') and 'bits_state' in changed:
			slot = self._gpio_slot_for_obj(obj)
			if slot is not None:
				if otype == 'ctrl:gpi':
					self._gpi_bits[slot] = changed['bits_state']
				else:
					self._gpo_bits[slot] = changed['bits_state']
		elif otype in ('ctrl:sample_clock', 'ctrl:dante_settings'):
			if 'current_sample_rate' in changed or 'sample_rate' in changed:
				self._update_dep_sample_rate_from_obj(obj)
		elif otype == 'ctrl:icy_stream_tx':
			if 'enabled' in changed or 'streaming' in changed or 'errors' in changed:
				self._icy_stream_state[_stream_idx_of(obj)] = _stream_state_of(obj)
		elif otype == 'device:operating_mode' and 'profile' in changed:
			if changed['profile'] in (OPERATING_MODE_ICECAST, OPERATING_MODE_DANTE):
				self._operating_mode = changed['profile']
		elif otype == 'ctrl:mac_interface' and 'mac_address' in changed:
			# The DEP pushes this once it comes up, which can be well after our
			# checkpoint arrived.
			mac = changed['mac_address']
			if mac:
				self.mac_str[0] = mac.upper()
		elif otype == 'ctrl:ipv4_interface' and 'address' in changed:
			self._store_ipv4_address(obj)

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

	def _ipv4_index_of(self, obj):
		"""Return 0 or 1 for the first two ctrl:ipv4_interface objects, else None."""
		idx = 0
		target_id = obj.get('_id')
		for item in self._objects:
			if item is None or item.get('_type') != 'ctrl:ipv4_interface':
				continue
			if item is obj or item.get('_id') == target_id:
				return idx if idx < 2 else None
			idx += 1
		return None

	def _store_ipv4_address(self, obj):
		"""Cache a non-empty ctrl:ipv4_interface.address.  Leave the previous
		value in place when the property is null."""
		idx = self._ipv4_index_of(obj)
		if idx is None:
			return
		addr = obj.get('address')
		if isinstance(addr, str) and addr:
			self.ip_str[idx] = addr

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

	# ---- Public API (synchronous, returns cached values) ----

	def ip_address(self, index):
		"""Cached IPv4 address from ctrl:ipv4_interface, or ``0.0.0.0``."""
		index = max(0, min(1, index))
		return self.ip_str[index]

	def mac_address(self, index):
		index = max(0, min(1, index))
		return self.mac_str[index]

	def serial_num(self):
		return self.serial

	def hw_revision(self):
		return self.hw_rev

	def fw_revision(self):
		return self.fw_rev

	def full_model_name(self):
		return self.ext_model_name

	def full_model_number(self):
		return self.ext_model_number

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

	def temperature(self):
		return self._temperature

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

	def icy_stream_state(self, stream_idx):
		"""DISPLAY_STREAM_STATE_* for one Icecast stereo stream."""
		return self._icy_stream_state.get(stream_idx, DISPLAY_STREAM_STATE_DISABLED)

	def tuner_band(self, slot, pair_idx):
		"""Return the current band string (e.g. "AM", "FM", "WB", "DAB") for a
		tuner pair on the given module slot, or "" if no ctrl:tuner object has
		been received for that (slot, pair_idx) yet."""
		return self._tuner_band.get((slot, pair_idx), "")

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


async def _print_summary(host, port):
	"""Connect, wait for the first parsed checkpoint, and print a short summary."""
	dev = RemoteASI2900()
	parsed = {'done': False}
	parse_all = dev._parse_all

	def _parse_and_flag():
		parse_all()
		parsed['done'] = True

	dev._parse_all = _parse_and_flag
	await dev.init(host, port)
	while not parsed['done']:
		await asyncio.sleep(0.1)
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
	if len(argv) < 2:
		print('Usage: remote_asi2900.py <host> [port]')
		return 1
	host = argv[1]
	port = int(argv[2]) if len(argv) > 2 else 80
	asyncio.run(_print_summary(host, port))
	return 0


if __name__ == '__main__':
	import sys
	raise SystemExit(main(sys.argv))
