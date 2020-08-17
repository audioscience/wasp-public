"""
wasp_update_stream_examples.py

A test module for interfacing with a WASP 3.x update stream server.

The server can be either:
a) a test server running on a local machine
b) an AudioScience hardware device that implements the WASP protocol

(c) AudioScience, Inc., 2020
"""

import requests
import json
import time

# set up the base url to use to access device endpoints
# Test server options
#url_ip_addr = 'localhost'
#url_port = '8080'
# Real world unit options
url_ip_addr = '192.168.1.195'
url_port = '80'
url_base_path = 'http://' + url_ip_addr + ':' + url_port + '/wasp/r2'
url_update_path = 'http://' + url_ip_addr + ':' + url_port + '/wasp/u2'

auth_hdr = {}

def update_auth_hdr():
	global auth_hdr
	# read the device session ID (note POST instead of GET)
	resp = requests.post(url_base_path + '/device/auth', headers={'Authorization': 'Hawk'})
	if resp.status_code != 200:
		print 'GET /device/auth error ' + str(resp.status_code)
	else:
		jdump = json.dumps(resp.json(), indent=4)
		print '\n--- Device authorization ID returned from /device/auth'
		auth_id = resp.json()['id']
	auth_hdr = {'Authorization': 'Hawk id=' + '\"' + str(auth_id) + '\"'}

def update_stream_test(headers={}):
	try:
		hdrs = auth_hdr.copy()
		hdrs.update(headers)
		resp = requests.get(url_update_path + '/objects', headers=hdrs, stream=True)
		if resp.status_code != 202:
			print 'GET stream objects error ' + str(resp.status_code)
		else:
			for raw_rsvp in resp.iter_lines():
				if raw_rsvp:
					if raw_rsvp != "---":
						json_rsvp = json.loads(raw_rsvp)
						print json.dumps(json_rsvp, indent=4)
	except KeyboardInterrupt:
		print '\n---Break...'

# get authorization ID
update_auth_hdr()

print '\n--- Default update stream.  Press Ctrl + C to move on to next test...'
time.sleep(2)
update_stream_test()

print '\n--- Meters updates limited to 1 per second.  Press Ctrl + C to move on to next test...'
time.sleep(2)
update_stream_test(headers={'X-Wasp-Stream-Min-Update-Period': '1000,100 ctrl:meter'})

print '\n--- Meters excluded from update stream.  Press Ctrl + C to conclude tests.'
time.sleep(2)
update_stream_test(headers={'X-Wasp-Stream-Exclude-Obj-Type': 'ctrl:meter'})
