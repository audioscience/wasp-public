# REMOTE_ASI2800.PY
#
# Standalone ASI2800 client for a WASP server.  Works on CPython 3 and
# MicroPython.  Depends on the stdlib, the sibling remote_common.py, and
# wasp.client.WaspClient.
#
# Caches tuner modules (part-number strings, not numeric module ids), tuner
# band, and Icecast stream state.  ASI2800 topologies do not publish
# device:fw_update_status today; the shared cache still records it when a
# server sends one.
#
# Run from a directory that contains this file, remote_common.py, and the
# vendored wasp package:
#   python3 remote_asi2800.py <host> [port]

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
DISPLAY_STREAM_STATE_DISABLED = remote_common.DISPLAY_STREAM_STATE_DISABLED
DISPLAY_STREAM_STATE_PENDING = remote_common.DISPLAY_STREAM_STATE_PENDING
DISPLAY_STREAM_STATE_STREAMING = remote_common.DISPLAY_STREAM_STATE_STREAMING
DISPLAY_STREAM_STATE_ERROR = remote_common.DISPLAY_STREAM_STATE_ERROR

_TYPES_OF_INTEREST = (
	'device:hw_desc',
	'device:sw_desc',
	'ctrl:mac_interface',
	'ctrl:ipv4_interface',
	'ctrl:temperature_sensor',
	'device:fw_update_status',
	'block:module',
	'block:io',
	'ctrl:tuner',
	'ctrl:icy_stream_tx',
)


class RemoteASI2800(RemoteWaspDevice):
	"""Cached ASI2800 view of a WASP device."""

	def __init__(self):
		RemoteWaspDevice.__init__(self)
		self.occupied_module_slots = 0
		self.module_parts = [''] * 8

	def types_of_interest(self):
		return _TYPES_OF_INTEREST

	def _reset_product(self):
		self.occupied_module_slots = 0
		self.module_parts = [''] * 8

	def _apply_object(self, obj):
		if RemoteWaspDevice._apply_object(self, obj):
			return True
		t = obj.get('_type')
		if t == 'block:module':
			slot = obj.get('slot', 0) - 1
			if 0 <= slot < 8:
				self.module_parts[slot] = obj.get('part_number', '')
				if slot + 1 > self.occupied_module_slots:
					self.occupied_module_slots = slot + 1
			return True
		if t == 'block:io' and obj.get('io_type') == 'icy':
			self._icy_channels += 1
			return True
		return False

	def occupied_slots(self):
		return self.occupied_module_slots

	def module_part(self, slot):
		"""Part-number string for a tuner module slot, or '' when empty.

		`slot` is 0-based.  WASP `block:module.slot` is 1-based.
		"""
		slot = max(0, min(7, slot))
		return self.module_parts[slot]


def _summarize(dev):
	name = dev.full_model_name() or dev.model_number
	print('model: ' + name)
	print('slots: %d' % dev.occupied_slots())
	print('icy: %d' % dev.icy_channels())
	slot = 0
	while slot < dev.occupied_slots():
		print('  %d %s' % (slot, dev.module_part(slot)))
		slot += 1


def main(argv):
	"""CLI entry: ``remote_asi2800.py <host> [port]``."""
	return remote_common.client_main(
		argv, 'remote_asi2800.py <host> [port]', RemoteASI2800, _summarize)


if __name__ == '__main__':
	import sys
	raise SystemExit(main(sys.argv))
