# REMOTE_COMMON.PY
#
# WASP update-stream cache shared by the ASI2700, ASI2800, and ASI2900
# clients.  No device imports.  Ship this file next to those clients.

import asyncio
import gc
import sys

try:
	import logging
	logger = logging.getLogger()
except ImportError:
	class _NullLogger:
		def info(self, *args, **kwargs):
			return None
	logger = _NullLogger()


# Icecast (ICY) per-stream display state.  Mirrors statusOf() in the web UI
# so a front panel and the browser agree: errors win, then disabled, then
# enabled-but-not-yet-live.
DISPLAY_STREAM_STATE_DISABLED = 0
DISPLAY_STREAM_STATE_PENDING = 1
DISPLAY_STREAM_STATE_STREAMING = 2
DISPLAY_STREAM_STATE_ERROR = 3

IDENTITY_TYPES = (
	'device:hw_desc',
	'device:sw_desc',
	'ctrl:mac_interface',
	'ctrl:ipv4_interface',
	'ctrl:temperature_sensor',
	'device:fw_update_status',
)


def load_sibling(module_file, name):
	"""Import `name` from the directory that contains `module_file`.

	Running a client script puts that directory on sys.path.  Importing the
	client as part of the device package does not, and these clients must
	not import the device package to find a sibling.
	"""
	slash = module_file.rfind('/')
	directory = module_file[:slash] if slash >= 0 else ''
	if directory and directory not in sys.path:
		sys.path.append(directory)
	return __import__(name)


def stream_state_of(obj):
	"""Map a ctrl:icy_stream_tx object to a DISPLAY_STREAM_STATE_* value."""
	if obj.get('errors'):
		return DISPLAY_STREAM_STATE_ERROR
	if not obj.get('enabled'):
		return DISPLAY_STREAM_STATE_DISABLED
	if not obj.get('streaming'):
		return DISPLAY_STREAM_STATE_PENDING
	return DISPLAY_STREAM_STATE_STREAMING


def stream_idx_of(obj):
	"""Stereo-stream index for a ctrl:icy_stream_tx object."""
	idx = obj.get('_icy_stream_idx')
	if idx is None:
		idx = obj.get('_idx_on_slot', 0)
	return idx


class RemoteWaspDevice:
	"""Cached view of the WASP objects every ASI product publishes.

	Subclasses keep product objects by extending `types_of_interest` and
	`_apply_object` / `_on_group_property` / `_handle_incremental`.
	"""

	def __init__(self):
		self.ip_str = ["0.0.0.0", "0.0.0.0"]
		self.model_number = ""
		self.hw_rev = ""
		self.serial = ""
		self.fw_rev = ""
		self.ext_model_name = ""
		self.ext_model_number = ""
		self.mac_str = ["00:00:00:00:00:00", "00:00:00:00:00:00"]
		self._temperature = 0.0
		self._fw_update_state = 'idle'
		self._fw_update_progress = 0
		self._tuner_band = {}
		self._icy_channels = 0
		self._icy_stream_state = {}
		self._wasp = None
		self._objects = []
		self._checkpoint_receiving = False

	def types_of_interest(self):
		return IDENTITY_TYPES

	def _default_part_number(self):
		return ''

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

	def _prepare_for_wasp_checkpoint(self):
		"""Release the previous checkpoint before WaspClient reads a new one."""
		if self._objects:
			self._objects = []
		self._checkpoint_receiving = False
		gc.collect()

	def _keep_checkpoint_obj(self, obj):
		if obj.get('_type') not in self.types_of_interest():
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
		"""Populate cached fields from the object list in a single pass."""
		self._reset_common()
		self._reset_product()
		for obj in self._objects:
			if obj is not None:
				self._apply_object(obj)
		self._after_parse()

	def _reset_common(self):
		"""Clear caches that a missing object must not leave stale.

		Addresses and temperature stay until a new non-empty value arrives.
		A checkpoint with no firmware-update object means the device is idle.
		"""
		self._fw_update_state = 'idle'
		self._fw_update_progress = 0
		self._tuner_band = {}
		self._icy_channels = 0
		self._icy_stream_state = {}

	def _reset_product(self):
		return None

	def _after_parse(self):
		return None

	def _apply_object(self, obj):
		"""Cache one object.  Return True when this class accepted it."""
		t = obj.get('_type')
		if t == 'device:hw_desc':
			self._apply_hw_desc(obj)
			return True
		if t == 'device:sw_desc':
			self.fw_rev = obj.get('version', '')
			return True
		if t == 'ctrl:mac_interface':
			self._store_mac_address(obj)
			return True
		if t == 'ctrl:ipv4_interface':
			self._store_ipv4_address(obj)
			return True
		if t == 'ctrl:temperature_sensor':
			self._store_temperature(obj.get('temperature', 0))
			return True
		if t == 'device:fw_update_status':
			self._fw_update_state = obj.get('state', 'idle')
			self._fw_update_progress = obj.get('progress', 0)
			return True
		if t == 'ctrl:tuner':
			self._store_tuner_band(obj)
			return True
		if t == 'ctrl:icy_stream_tx':
			self._icy_stream_state[stream_idx_of(obj)] = stream_state_of(obj)
			return True
		return False

	def _apply_hw_desc(self, obj):
		self.model_number = obj.get('part_number', self._default_part_number())
		self.hw_rev = obj.get('revision', '')
		self.serial = obj.get('serial_number', '')
		self.ext_model_name = obj.get('name', '')
		parts = self.ext_model_name.split()
		if len(parts) > 2:
			self.ext_model_number = self.model_number + '-' + '-'.join(parts[2:])
		else:
			self.ext_model_number = self.model_number

	def _index_of_type(self, obj, type_name):
		"""Return 0 or 1 for the first two objects of `type_name`, else None."""
		idx = 0
		target_id = obj.get('_id')
		for item in self._objects:
			if item is None or item.get('_type') != type_name:
				continue
			if item is obj or item.get('_id') == target_id:
				return idx if idx < 2 else None
			idx += 1
		return None

	def _ipv4_index_of(self, obj):
		"""Return 0 or 1 for the first two ctrl:ipv4_interface objects, else None."""
		return self._index_of_type(obj, 'ctrl:ipv4_interface')

	def _store_ipv4_address(self, obj):
		"""Cache a non-empty ctrl:ipv4_interface.address.  Leave the previous
		value in place when the property is null."""
		idx = self._ipv4_index_of(obj)
		if idx is None:
			return
		addr = obj.get('address')
		if isinstance(addr, str) and addr:
			self.ip_str[idx] = addr

	def _store_mac_address(self, obj, value=None):
		"""Cache a non-empty MAC on the first two ctrl:mac_interface objects.

		A missing address does not clear a value already cached.  ASI2900
		overrides this and writes only mac_str[0]; the secondary address
		comes from the device filesystem.
		"""
		if value is None:
			value = obj.get('mac_address')
		if not value:
			return
		idx = self._index_of_type(obj, 'ctrl:mac_interface')
		if idx is None:
			return
		self.mac_str[idx] = value.upper()

	def _store_temperature(self, value):
		try:
			self._temperature = float(value)
		except (ValueError, TypeError):
			return None

	def _store_tuner_band(self, obj, band=None):
		if band is None:
			band = obj.get('band', '')
		slot = obj.get('_module_slot', 0) - 1
		pair_idx = obj.get('_idx_on_slot', 1) - 1
		self._tuner_band[(slot, pair_idx)] = band

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
						self._on_group_property(obj, prop, value)

	def _on_group_property(self, obj, prop, value):
		"""Apply one group_prefix property.  Return True when handled."""
		otype = obj.get('_type')
		if prop == 'temperature' and otype == 'ctrl:temperature_sensor':
			self._store_temperature(value)
			return True
		if prop == 'address' and otype == 'ctrl:ipv4_interface':
			self._store_ipv4_address(obj)
			return True
		if prop == 'mac_address' and otype == 'ctrl:mac_interface':
			self._store_mac_address(obj, value)
			return True
		if prop == 'state' and otype == 'device:fw_update_status':
			self._fw_update_state = value
			return True
		if prop == 'progress' and otype == 'device:fw_update_status':
			self._fw_update_progress = value
			return True
		if prop == 'band' and otype == 'ctrl:tuner':
			self._store_tuner_band(obj, value)
			return True
		if prop in ('enabled', 'streaming', 'errors') and otype == 'ctrl:icy_stream_tx':
			self._icy_stream_state[stream_idx_of(obj)] = stream_state_of(obj)
			return True
		return False

	def _handle_incremental(self, obj, changed):
		"""Apply an update:obj body.  Return True when this class owns the type."""
		otype = obj.get('_type')
		if otype == 'ctrl:temperature_sensor':
			if 'temperature' in changed:
				self._store_temperature(changed['temperature'])
			return True
		if otype == 'ctrl:ipv4_interface':
			if 'address' in changed:
				self._store_ipv4_address(obj)
			return True
		if otype == 'ctrl:mac_interface':
			if 'mac_address' in changed:
				self._store_mac_address(obj, changed['mac_address'])
			return True
		if otype == 'device:fw_update_status':
			self._fw_update_state = obj.get('state', 'idle')
			self._fw_update_progress = obj.get('progress', 0)
			return True
		if otype == 'ctrl:tuner':
			if 'band' in changed:
				self._store_tuner_band(obj, changed['band'])
			return True
		if otype == 'ctrl:icy_stream_tx':
			if 'enabled' in changed or 'streaming' in changed or 'errors' in changed:
				self._icy_stream_state[stream_idx_of(obj)] = stream_state_of(obj)
			return True
		return False

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

	def temperature(self):
		return self._temperature

	def fw_update_status(self):
		"""(state, progress) of device:fw_update_status: state is 'idle',
		'uploading', 'installing' or 'error'; progress is 0..100 %.

		ASI2700 and ASI2800 topologies do not publish this object today.
		The cache still records it when a server sends one.
		"""
		return self._fw_update_state, self._fw_update_progress

	def tuner_band(self, slot, index):
		"""Band string for a ctrl:tuner, or "" when that pair has not arrived.

		`slot` and `index` are 0-based.  The WASP fields `_module_slot` and
		`_idx_on_slot` are 1-based.
		"""
		return self._tuner_band.get((slot, index), "")

	def icy_channels(self):
		"""Count of block:io objects with io_type 'icy' seen in the last parse."""
		return self._icy_channels

	def icy_stream_state(self, stream_idx):
		"""DISPLAY_STREAM_STATE_* for one Icecast stereo stream."""
		return self._icy_stream_state.get(stream_idx, DISPLAY_STREAM_STATE_DISABLED)


async def run_until_checkpoint(dev, host, port):
	"""Connect and return once the first checkpoint has been parsed."""
	parsed = {'done': False}
	parse_all = dev._parse_all

	def _parse_and_flag():
		parse_all()
		parsed['done'] = True

	dev._parse_all = _parse_and_flag
	await dev.init(host, port)
	while not parsed['done']:
		await asyncio.sleep(0.1)
	return dev


def client_main(argv, usage, factory, summarize):
	"""CLI entry shared by the product clients."""
	if len(argv) < 2:
		print('Usage: ' + usage)
		return 1
	host = argv[1]
	port = int(argv[2]) if len(argv) > 2 else 80

	async def _run():
		dev = await run_until_checkpoint(factory(), host, port)
		summarize(dev)

	asyncio.run(_run())
	return 0
