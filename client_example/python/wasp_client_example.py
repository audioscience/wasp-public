"""
A test module for interfacing with a WASP 3.x server.

The server can be either:
a) a test server runnig on a local machine
b) an AudioScience hardware device that implements the WASP protocol

(c) AudioScience, Inc., 2019
"""

import argparse
import requests
import json
import time

# set up the base url to use to access device endpoints
# Test server options
url_ip_addr = 'localhost'
url_port = '8080'
# Real world unit options
#url_ip_addr = '192.168.1.146'
#url_port = '80'
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
		print auth_id
	auth_hdr = {'Authorization': 'Hawk id=' + '\"' + str(auth_id) + '\"'}


def wasp_request(func, *args, **kwargs):
	# Try the request using the current auth token (it may be empty)
	resp = func(*args, headers = auth_hdr, **kwargs)
	# If the server returns 401 then authorization is required for this request
	# and the current token is invalid or expired so a new one is requested
	# and then the original request is retried.
	if resp.status_code == 401:
		update_auth_hdr()
		resp = func(*args, headers = auth_hdr, **kwargs)
	return resp


# read the device information
resp = wasp_request(requests.get, url_base_path + '/device/info')
if resp.status_code != 200:
	print 'GET /device/info error'
else:
	jdump = json.dumps(resp.json(), indent=4)
	print '\n--- Device Information returned from /device/info'
	print jdump


# read all device objects
resp = wasp_request(requests.get, url_base_path + '/objects')
if resp.status_code != 200:
	print 'GET /objects error ' + str(resp.status_code)
else:
	print '\n--- Objects returned from /objects'
	print 'Response Header Content-Range: ' + resp.headers['Content-Range']
	for obj in resp.json():
		print 'Object Index ' + str(obj['_id']) + ' Type ' + obj['_type']


# read the available schemas
schemas = wasp_request(requests.get, url_base_path + '/schemas')
if schemas.status_code != 200:
	print 'GET /schemas error ' + str(resp.status_code)
else:
	print '\n--- Names of schemas returned from /schemas'
	for s in schemas.json():
		print s


# Go through the object list looking for an io block that is an analog input block
# for analog input number 1
members = []
object_dic = {}
resp = wasp_request(requests.get, url_base_path + '/objects')
if resp.status_code != 200:
	print 'GET /objects error ' + str(resp.status_code)
else:
	print '\n--- Block lookup'
	print 'Searching io blocks for a block that matches all of'
	print '_type == block:io'
	print 'io_dir == in'
	print 'io_type == analog'
	print 'io_idx == 0'
	index = 0	# we are looking for analog in 1, which is index 0
	object_dic = resp.json()
	for obj in object_dic:
		#print obj
		if (obj['_type'] == 'block:io')          and \
		   (obj['io_dir'] == 'in')               and \
		   (obj['io_type'] == 'analog')          and \
		   (obj['io_idx'] == index):
			print 'Block found at object index ' + str(obj['_id'])
			members = obj['members']	# record the members in the io block
			break
	print 'Analog in 1 block contains the following members ' + str(members)

# now look for the level control in the members list
level_idx = 0
for obj in members:
	if object_dic[obj]['_type'] == 'ctrl:trim_level':
		level_idx = obj
		print 'Found ctrl:trim_level in blocks members list'
		break

# read the level
print '\n--- Read the level control'
url = url_base_path + '/objects/' + str(level_idx)
level_resp = wasp_request(requests.get, url)
if level_resp.status_code != 200:
	print 'GET ' + url + ' error'
else:
	print 'The level is ' + str(level_resp.json()['level'])

# write the level
print '\n--- Set the level control to +7 dBu'
resp = wasp_request(requests.patch, url, json = {'level' : 7})
if resp.status_code != 200 and resp.status_code != 202:
	print 'PATCH ' + url + ' error'
else:
	print 'success'

# lookup level schema information
print '\n--- Raw level schema'
print json.dumps(schemas.json()[level_resp.json()['_schema']], indent=4)

print '\n--- Level parameters from the level schema'
level_properties = schemas.json()[level_resp.json()['_schema']]['properties']['level']
print 'Min ' + str(level_properties['minimum']) +    \
	' Max ' + str(level_properties['maximum']) + \
	' Units ' + level_properties['wasp-unit']

# next look for the phantom power control in the members list
print '\n--- Toggle the phantom power'
phantom_idx = 0
for obj in members:
	if object_dic[obj]['_type'] == 'ctrl:phantom':
		phantom_idx = obj
		print 'Found ctrl:phantom in blocks members list'
	if object_dic[obj]['_type'] == 'ctrl:level':
		level_idx = obj
		print 'Found ctrl:phantom in blocks members list'

# toggle the phantom power setting
url = url_base_path + '/objects/' + str(phantom_idx)

resp = wasp_request(requests.patch, url, json = {'active' : True})
if resp.status_code != 200 and resp.status_code != 202:
	print 'PATCH ' + url + ' error code ' + str(resp.status_code)
else:
	print 'success enabling phantom power'

resp = wasp_request(requests.patch, url, json = {'active' : False})
if resp.status_code != 200 and resp.status_code != 202:
	print 'PATCH ' + url + ' error code ' + str(resp.status_code)
else:
	print 'success disabling phantom power'

# Set the level and phantom in one request
url = url_base_path + '/objects'
resp = wasp_request(requests.patch, url, json = [{'_id':phantom_idx , 'active' : False},{'_id':level_idx , 'level' : 24}])
if resp.status_code != 200 and resp.status_code != 202:
	print 'PATCH ' + url + ' error code ' + str(resp.status_code)
else:
	print 'success setting phantom power and level'

# now look for the mute control in the members list
mute_idx = 0
for obj in members:
	if object_dic[obj]['_type'] == 'ctrl:mute':
		mute_idx = obj
		print 'Found ctrl:mute in blocks members list'
		break

# read the mute control properties
print '\n--- Read the mute control'
url = url_base_path + '/objects/' + str(mute_idx)
mute_resp = wasp_request(requests.get, url)
if mute_resp.status_code != 200:
	print 'GET ' + url + ' error'
else:
	active = mute_resp.json()['active']
	try:
		button_enabled = mute_resp.json()['button_enabled']
		button_mode = mute_resp.json()['button_mode']
		button_state = mute_resp.json()['button_state']
		print 'Mute properties - Active: ' + str(active) + ', Button Enabled: ' + str(button_enabled) \
			+ ', Button Mode: ' + str(button_mode) + ', Button State: ' + str(button_state)

		# write mute control properties
		print '\n--- Disable the button'
		resp = wasp_request(requests.patch, url, json = {'button_enabled' : False})
		if resp.status_code != 200 and resp.status_code != 202:
			print 'PATCH ' + url + ' error'
		else:
			print 'success'

		print '\n--- Change the button mode to "momentary"'
		resp = wasp_request(requests.patch, url, json = {'button_mode' : 'momentary'})
		if resp.status_code != 200 and resp.status_code != 202:
			print 'PATCH ' + url + ' error'
		else:
			print 'success'
	except KeyError as e:
		print 'No mute button properties, skipping...'

# test update stream
print '\n--- Update stream'
resp = wasp_request(requests.get, url_update_path + '/objects', stream=True)
count = 0
if resp.status_code != 202:
	print 'GET stream objects error ' + str(resp.status_code)
else:
	for raw_rsvp in resp.iter_lines():
		if count == 10:
			break
		count = count + 1
		if raw_rsvp:
			#rsvp = json.loads(raw_rsvp)
			if raw_rsvp != "---":
				print json.dumps(json.loads(raw_rsvp), indent=4)

# Go through the object list looking for an io block that is a gpio output block number 1
gpo_members = []
print '\n--- GPO Block lookup'
print 'Searching io blocks for a block that matches all of'
print '_type == block:io'
print 'io_dir == out'
print 'io_type == gpio'
print 'io_idx == 1'
index = 1	# we are looking for GPO port 2 which is index 1
for obj in object_dic:
	if (obj['_type'] == 'block:io')          and \
	   (obj['io_dir'] == 'out')               and \
	   (obj['io_type'] == 'gpio')          and \
	   (obj['io_idx'] == index):
		print 'Block found at object index ' + str(obj['_id'])
		gpo_members = obj['members']	# record the members in the io block
		break

if not len(gpo_members):
	print 'Skipping GPIO, no GPO blocks found...'
else:
	print 'GPO 1 block contains the following members ' + str(gpo_members)

	# now look for a GPIO Output control in the members list
	for obj in gpo_members:
		if object_dic[obj]['_type'] == 'ctrl:gpo':
			gpo_obj_idx = obj
			print 'Found ctrl:gpo_port in blocks members list'
			break

	# set the state of all GPIO Output bits
	print '\n--- Set all bits'
	url = url_base_path + '/objects/' + str(gpo_obj_idx)
	resp = wasp_request(requests.patch, url, json = {'bits_state' : '11111'})
	if resp.status_code != 200 and resp.status_code != 202:
		print 'PATCH ' + url + ' error'
	else:
		print 'success'

	# read bits
	print '\n--- Read the gpo control'
	gpo_resp = wasp_request(requests.get, url)
	if gpo_resp.status_code != 200:
		print 'GET ' + url + ' error'
	else:
		bits_state = gpo_resp.json()['bits_state']
		print 'GPO bits state is ' + bits_state

	# clear bits
	print '\n--- Clear all bits using the write-only property `clear_bits`'
	resp = wasp_request(requests.patch, url, json = {'clear_bits' : '11111'})
	if resp.status_code != 200 and resp.status_code != 202:
		print 'PATCH ' + url + ' error'
	else:
		print 'success'

	# read bits to confirm previous clear operation was successful
	gpo_resp = wasp_request(requests.get, url)
	if gpo_resp.status_code != 200:
		print 'GET ' + url + ' error'
	else:
		bits_state = gpo_resp.json()['bits_state']
		assert bits_state == '00000'

	# set a single output bit
	print '\n--- Set MSB using the special write-only property `set_bits`'
	resp = wasp_request(requests.patch, url, json = {'set_bits' : '10000'})
	if resp.status_code != 200 and resp.status_code != 202:
		print 'PATCH ' + url + ' error'
	else:
		print 'success'

	# read bits to confirm previous set operation was successful
	gpo_resp = wasp_request(requests.get, url)
	if gpo_resp.status_code != 200:
		print 'GET ' + url + ' error'
	else:
		bits_state = gpo_resp.json()['bits_state']
		assert bits_state == '10000', bits_state

# Go through the object list looking for an io block that is an avb talker block index 1
avb_talker_obj = None
print '\n--- AVB Talker Block lookup'
print 'Searching io blocks for a block that matches all of'
print '_type == block:io'
print 'io_dir == out'
print 'io_type == avb'
print 'io_idx == 1'
index = 1
for obj in object_dic:
	if (obj['_type'] == 'block:io')          and \
	   (obj['io_dir'] == 'out')               and \
	   (obj['io_type'] == 'avb')          and \
	   (obj['io_idx'] == index):
		print 'Block found at object index ' + str(obj['_id'])
		labels = ['Temp Label', 'AVB Talker 1']
		for label in labels:
			# set the label
			url = url_base_path + '/objects/' + str(obj['_id'])
			print '\n--- Set label to ' + label 
			resp = wasp_request(requests.patch, url, json = {'label' : label})
			if resp.status_code != 200 and resp.status_code != 202:
				print 'PATCH ' + url + ' error'
			else:
				print 'success'

		avb_talker_obj = obj['members'][0]
		break

if not avb_talker_obj:
	print 'Skipping AVB Talker, object not found...'
else:
	url = url_base_path + '/objects/' + str(avb_talker_obj)
	avb_talker_resp = wasp_request(requests.get, url)
	if avb_talker_resp.status_code != 200:
		print 'GET ' + url + ' error'
	else:
		print '\n--- Get properties'
		active = avb_talker_resp.json()['active']
		print 'Active: ' + str(active)

# Go through the object list looking for an io block that is an avb listener block index 1
avb_listener_obj = None
print '\n--- AVB Listener Block lookup'
print 'Searching io blocks for a block that matches all of'
print '_type == block:io'
print 'io_dir == in'
print 'io_type == avb'
print 'io_idx == 1'
index = 1
for obj in object_dic:
	if (obj['_type'] == 'block:io')          and \
	   (obj['io_dir'] == 'in')               and \
	   (obj['io_type'] == 'avb')          and \
	   (obj['io_idx'] == index):
		print 'Block found at object index ' + str(obj['_id'])
		labels = ['Temp Label', 'AVB Listener 1']
		for label in labels:
			# set the label
			url = url_base_path + '/objects/' + str(obj['_id'])
			print '\n--- Set label to ' + label 
			resp = wasp_request(requests.patch, url, json = {'label' : label})
			if resp.status_code != 200 and resp.status_code != 202:
				print 'PATCH ' + url + ' error'
			else:
				print 'success'

		avb_listener_obj = obj['members'][0]
		break

if not avb_listener_obj:
	print 'Skipping AVB Listener, object not found...'
else:
	url = url_base_path + '/objects/' + str(avb_listener_obj)
	avb_listener_resp = wasp_request(requests.get, url)
	if avb_listener_resp.status_code != 200:
		print 'GET ' + url + ' error'
	else:
		print '\n--- Get properties'
		streaming = avb_listener_resp.json()['streaming']
		media_locked = avb_listener_resp.json()['media_locked']
		missed_packets = avb_listener_resp.json()['missed_packet_count']
		connected = avb_listener_resp.json()['connected']
		reconnect_count = avb_listener_resp.json()['reconnect_count']
		print 'Streaming: ' + str(streaming) + \
			', Media Locked: ' + str(media_locked) + \
			', Missed Packet Count: ' + str(missed_packets) + \
			', Connected: ' + str(connected) + \
			', Reconnect Count: ' + str(reconnect_count)

print '\n--- Device User Data lookup'
for obj in object_dic:
	if obj['_type'] == 'device:user_data':
		print 'User Data object found at index ' + str(obj['_id'])
		# set the label
		label = 'Default'
		url = url_base_path + '/objects/' + str(obj['_id'])
		print '\n--- Set label to ' + label 
		resp = wasp_request(requests.patch, url, json = {'label' : label})
		if resp.status_code != 200 and resp.status_code != 202:
			print 'PATCH ' + url + ' error'
		else:
			print 'success'
		break

identify_obj = None
print '\n--- Identify object lookup'
for obj in object_dic:
	if obj['_type'] == 'device:identify':
		print 'Identify object found at index ' + str(obj['_id'])
		identify_obj = obj
		break

if identify_obj:
	# Toggle Identify On and Off
	print '\n--- Toggle Identify On'
	url = url_base_path + '/objects/' + str(identify_obj['_id'])
	resp = wasp_request(requests.patch, url, json = {'identify' : True})
	if resp.status_code != 200 and resp.status_code != 202:
		print 'PATCH ' + url + ' error'
	else:
		print 'success'

	print '\n--- Sleep for 3s...'
	time.sleep(3)

	print '\n--- Toggle Identify Off'
	url = url_base_path + '/objects/' + str(identify_obj['_id'])
	resp = wasp_request(requests.patch, url, json = {'identify' : False})
	if resp.status_code != 200 and resp.status_code != 202:
		print 'PATCH ' + url + ' error'
	else:
		print 'success'
	
else:
	print 'Skipping Identify, object not found...'
