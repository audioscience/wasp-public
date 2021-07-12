#!/usr/bin/python3
# coding: utf-8

import sys
import argparse
import requests

upload_url = sys.argv[1]
file_path = sys.argv[2]

files = [
	('file', ('file_path', open(sys.argv[2], 'rb'), 'application/binary'))
	]
r = requests.post(upload_url, files=files)
print(r.text)
