# MODULE_IDS.PY
#
# Numeric ASI2900 module ids.  No device imports: the on-device drivers and
# the standalone WASP client both use this class.  Ship this file next to
# remote_asi2900.py in a client example.

class MODULE_IDS:
	ASI1931_LINEOUT = 0x1931
	ASI1932_MICLINEIN = 0x1932
	ASI1933_UBMICLINEIN = 0x1933

	ASI1941_AESEBUOUT = 0x1941
	ASI1942_AESEBUIN = 0x1942

	ASI1951_GPIO_8X8 = 0x1951
	ASI1952_GPIO_5X5 = 0x1952

	ASI1971_TUNER_AMFMWB = 0x1971
	ASI1972_TUNER_AMFMHDDAB = 0x1972
