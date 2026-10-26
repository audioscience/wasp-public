# REMOTE_ASI2700.PY
#
# Standalone ASI2700 client for a WASP server.  Works on CPython 3 and
# MicroPython.  Depends on the stdlib, the sibling remote_common.py, and
# wasp.client.WaspClient.
#
# Caches the fixed AES67 adapter: identity, two MACs, two IPv4 addresses,
# temperature, firmware-update status when the server sends it, and AES67
# channel status.  ASI2700 topologies do not publish device:fw_update_status
# today.
#
# Run from a directory that contains this file, remote_common.py, and the
# vendored wasp package:
#   python3 remote_asi2700.py <host> [port]

import sys

def _load_remote_common():
	path = __file__
	slash = path.rfind('/')
	directory = path[:slash] if slash >= 0 else ''
	if directory and directory not in sys.path:
		sys.path.append(directory)
	import remote_common
	return remote_common


remote_common = _load_remote_common()
RemoteWaspDevice = remote_common.RemoteWaspDevice

_TYPES_OF_INTEREST = (
	'device:hw_desc',
	'device:sw_desc',
	'ctrl:mac_interface',
	'ctrl:ipv4_interface',
	'ctrl:temperature_sensor',
	'device:fw_update_status',
	'block:io',
	'ctrl:aes67_transmitter',
	'ctrl:aes67_receiver',
)


class RemoteASI2700(RemoteWaspDevice):
	"""Cached ASI2700 view of a WASP device."""

	def __init__(self):
		RemoteWaspDevice.__init__(self)
		self._tx_channels = 0
		self._rx_channels = 0
		self._tx_aes67 = {}
		self._rx_aes67 = {}

	def types_of_interest(self):
		return _TYPES_OF_INTEREST

	def _reset_product(self):
		self._tx_channels = 0
		self._rx_channels = 0
		self._tx_aes67 = {}
		self._rx_aes67 = {}

	def _after_parse(self):
		self._rebuild_aes67()

	def _apply_object(self, obj):
		if RemoteWaspDevice._apply_object(self, obj):
			return True
		if obj.get('_type') != 'block:io' or obj.get('io_type') != 'aes67':
			return False
		direction = obj.get('io_dir')
		if direction == 'out':
			self._tx_channels += 1
		elif direction == 'in':
			self._rx_channels += 1
		return True

	def _parent_channel(self, obj):
		"""Return ('tx'|'rx', io_idx) for an AES67 control, or (None, None)."""
		parent_id = obj.get('_parent')
		if parent_id is None or parent_id >= len(self._objects):
			return None, None
		parent = self._objects[parent_id]
		if not parent or parent.get('_type') != 'block:io':
			return None, None
		direction = 'tx' if parent.get('io_dir') == 'out' else 'rx'
		return direction, parent.get('io_idx', -1)

	def _rebuild_aes67(self):
		tx = {}
		rx = {}
		for obj in self._objects:
			if obj is None:
				continue
			otype = obj.get('_type')
			if otype not in ('ctrl:aes67_transmitter', 'ctrl:aes67_receiver'):
				continue
			direction, channel = self._parent_channel(obj)
			if direction is None:
				continue
			status = obj.get('status', '')
			if direction == 'tx':
				tx[channel] = status
			else:
				rx[channel] = status
		self._tx_aes67 = tx
		self._rx_aes67 = rx

	def _on_group_property(self, obj, prop, value):
		if RemoteWaspDevice._on_group_property(self, obj, prop, value):
			return True
		otype = obj.get('_type')
		if prop == 'status' and otype in (
			'ctrl:aes67_transmitter', 'ctrl:aes67_receiver',
		):
			self._rebuild_aes67()
			return True
		return False

	def _handle_incremental(self, obj, changed):
		if RemoteWaspDevice._handle_incremental(self, obj, changed):
			return True
		otype = obj.get('_type')
		if otype in ('ctrl:aes67_transmitter', 'ctrl:aes67_receiver'):
			if 'status' in changed:
				self._rebuild_aes67()
			return True
		return False

	def tx_channels(self):
		"""Count of block:io objects with io_type 'aes67' and io_dir 'out'."""
		return self._tx_channels

	def rx_channels(self):
		"""Count of block:io objects with io_type 'aes67' and io_dir 'in'."""
		return self._rx_channels

	def aes67_status(self, direction, channel):
		"""Status string for one AES67 channel, or '' when that channel is absent.

		Transmitters report 'started' or 'stopped'.  Receivers report
		'connected' or 'not-connected'.  `channel` is the parent block's
		io_idx.
		"""
		if direction == 'tx':
			return self._tx_aes67.get(channel, '')
		return self._rx_aes67.get(channel, '')


def _summarize(dev):
	name = dev.full_model_name() or dev.model_number
	print('model: ' + name)
	print('aes67 tx: %d' % dev.tx_channels())
	print('aes67 rx: %d' % dev.rx_channels())
	print('ip: ' + dev.ip_address(0))


def main(argv):
	"""CLI entry: ``remote_asi2700.py <host> [port]``."""
	return remote_common.client_main(
		argv, 'remote_asi2700.py <host> [port]', RemoteASI2700, _summarize)


if __name__ == '__main__':
	import sys
	raise SystemExit(main(sys.argv))
