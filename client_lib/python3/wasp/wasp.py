#!/usr/bin/python3
# coding: utf-8

from __future__ import print_function

example_usage = """
> python3 wasp.py BKLYN-II-109f08.local analog,in,4,phantom
[200, {"_self": "/controls/132", "_type": "/meta/controls/phantom", "active": true, "loc": ["/io/analog/in/4"]}]

> python3 wasp.py --pretty BKLYN-II-109f08.local analog,in,4,phantom
[
    200,
    {
        "_self": "/controls/132",
        "_type": "/meta/controls/phantom",
        "active": true,
        "loc": [
            "/io/analog/in/4"
        ]
    }
]

> python3 wasp.py --tree BKLYN-II-109f08.local analog,in,4,phantom
Status: 200

├── active: True
├── loc
│   └── 0: /io/analog/in/4
├── _type: /meta/controls/phantom
└── _self: /controls/132

> python3 wasp.py BKLYN-II-109f08.local analog,in,4,phantom "{\"active\":true}"
[202, null]

> python3 wasp.py BKLYN-II-109f08.local --jsonptr-lookup --tree /adapter
Status: 200

├── info
│   ├── model_id: ASI2703
│   ├── serial: 2018
│   ├── hw_rev: Z9
│   └── product_name: Iyo Dante 32.32M
└── software
    ├── AudioScience
    │   └── firmware: 0.0.0.1
    └── XMOS
        └── firmware: 9.8.7.6
"""

import sys
import json
import argparse
try:
    import urllib2
    urllib_request = urllib2
    urllib_error = urllib2
except ImportError:
    from urllib import error as urllib_error
    from urllib import request as urllib_request


def json_ptr_lookup(obj, ptr, default=None):
    try:
        for tok in ptr.split('/'):
            if not tok:
                continue
            try:
                tok = int(tok)
            except ValueError:
                pass
            obj = obj[tok]
    except KeyError:
        return default
    else:
        return obj


def obj_iter(obj):
    if isinstance(obj, str) or isinstance(obj, bytes):
        return None, None

    try:
        return len(obj), obj.items()
    except (AttributeError, TypeError):
        pass

    try:
        return len(obj), enumerate(obj)
    except TypeError:
        pass

    return None, None


def dump_tree(obj, prefix=''):
    attr_count, obj_iterator = obj_iter(obj)
    if not attr_count:
        print(':', obj)
        return

    print('')

    attr_index = 0
    for k, v in obj_iterator:
        s1 = '├── ' if attr_index != attr_count-1 else '└── '
        s2 = '│   ' if attr_index != attr_count-1 else '    '
        print(prefix+s1+str(k), end='')
        dump_tree(v, prefix=prefix+s2)
        attr_index += 1


def wasp_auth(host, port):
    headers = {'Authorization': 'Hawk'}
    request = urllib_request.Request('http://{}:{}/wasp/r2/device/auth'.format(host, port), headers=headers)
    request.get_method = lambda: 'POST'
    resp = urllib_request.urlopen(request)
    return resp.getcode(), json.load(resp)


def wasp_get(host, port, path, auth_id=None):
    if auth_id is not None:
        headers = {'Authorization': 'Hawk id="' + str(auth_id) + '"'}
    else:
        headers = {}
    request = urllib_request.Request('http://{}:{}/wasp/r2{}'.format(host, port, path), headers=headers)
    #request.get_method = lambda: 'PATCH'
    resp = urllib_request.urlopen(request)
    return resp.getcode(), json.load(resp)


def wasp_patch(host, port, path, update_dict, auth_id=None):
    headers = {'Content-Type':'application/json'}
    if auth_id is not None:
        headers['Authorization'] = 'Hawk id="' + str(auth_id) + '"'
    request = urllib_request.Request(
            'http://{}:{}/wasp/r2{}'.format(host, port, path),
            json.dumps(update_dict).encode(),
            headers=headers
        )
    request.get_method = lambda: 'PATCH'
    resp = urllib_request.urlopen(request)
    return resp.getcode(), json.load(resp)


class WaspDevice:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.objects = None
        self.schemas = None
        self.loc_map = None
        self.ctrl_map = None
        self.auth_id = None
        self.asi_fw_ver = None
        self.serial_number = None
        self.hardware_rev = None
        self.hardware_errors = None
        self.line_input_chs = []
        self.mic_input_chs = []
        self._get_dom()
        self._build_maps()


    def auth_wrapper(self, func, *args, **kwargs):
        while True:
            kwargs['auth_id'] = self.auth_id
            try:
                status_code, resp = func(*args, **kwargs)
            except urllib_error.HTTPError as e:
                if e.code == 401:
                    s, r = wasp_auth(self.host, self.port)
                    self.auth_id = r.get('id', None)
                    if s == 200 and self.auth_id is not None:
                        continue
                raise
            break
        return status_code, resp


    def _get_dom(self):
        try:
            # GET on a WASP 1.0 endpoint ('/asi/r1/adapter') to determine whether the Iyo is running release 1.x or 2.x firmware.
            # If the Iyo is running release 1.x, store the firmware version and return.
            request = urllib_request.Request('http://{}:{}/asi/r1{}'.format(self.host, self.port, '/adapter'), headers={})
            resp = urllib_request.urlopen(request)
            adapter_info = json.load(resp)
            self.asi_fw_ver = adapter_info[u'software'][u'AudioScience'][u'firmware']
        except urllib_error.HTTPError as e:
            if self.objects is None:
                status, self.objects = self.auth_wrapper(wasp_get, self.host, self.port, '/objects')
                assert status == 200
            if self.schemas is None:
                status, self.schemas = self.auth_wrapper(wasp_get, self.host, self.port, '/schemas')
                assert status == 200
        return self.objects, self.schemas

    def new_io_location_id_tuple(self, io_type, io_dir, io_idx):
        assert io_dir in [u'in', u'out'], str(io_dir)
        assert io_type in [u'analog'], str(io_type)
        return io_type, io_dir, int(io_idx)

    def new_adapter_location_id_tuple(self):
        return (u'/meta/adapter',)

    def _build_maps(self):
        objects, schemas = self._get_dom()
        if objects == None or schemas == None:
                        return

        lmap = dict()
        omap = dict()
        for i, b in enumerate(objects):
            # Store the ASI fw version
            if b['_type'] == 'device:sw_desc' and b['name'] == 'Iyo Dante Firmware':
                self.asi_fw_ver = b['version']
            elif b['_type'] == 'device:hw_desc':
                self.serial_number = b['serial_number']
                self.hardware_rev = b['revision']
                self.model_id = b['part_number']
            elif b['_type'] == 'ctrl:hw_status':
                self.hardware_errors = b['error_log']
            if b['_type'] != 'block:io':
                continue
            if b['io_type'] == 'analog' and b['io_dir'] == 'in':
                if 'Mic' in b['label']:
                    self.mic_input_chs.append(b['io_idx'])
                if 'Line' in b['label']:
                    self.line_input_chs.append(b['io_idx'])
            lkey = b['io_type'], b['io_dir'], b['io_idx']
            lmap[lkey] = b
            for obj_id in b['members']:
                o = objects[obj_id]
                okey = lkey, o['_type']
                omap[okey] = o
        self.loc_map = lmap
        self.ctrl_map = omap

    def ctrl_lookup(self, loc_id_tuple, ctrl_type):
        return self.ctrl_map[(loc_id_tuple, ctrl_type)]

    def _path_op(self, func, obj_path, *args):
        #obj_path = u'/objects/'+str(ctrl['_id'])
        return self.auth_wrapper(func, self.host, self.port, obj_path, *args)

    def _control_op(self, func, loc_id_tuple, ctrl_type, *args):
        ctrl = self.ctrl_lookup(loc_id_tuple, ctrl_type)
        obj_path = u'/objects/'+str(ctrl['_id'])
        return self.auth_wrapper(func, self.host, self.port, obj_path, *args)

    def get_asi_model_number(self):
            return self.model_id

    def get_asi_fw_ver(self):
        return self.asi_fw_ver

    def get_sn(self):
        return self.serial_number

    def get_hw_rev(self):
        return self.hardware_rev

    def get_hw_errs(self):
        return self.hardware_errors

    def get_mic_input_chs(self):
        return self.mic_input_chs

    def get_line_input_chs(self):
        return self.line_input_chs

    def get_control_attrs(self, loc_id_tuple, ctrl_type):
        return self._control_op(wasp_get, loc_id_tuple, ctrl_type)

    def set_control_attrs(self, loc_id_tuple, ctrl_type, new_value):
        return self._control_op(wasp_patch, loc_id_tuple, ctrl_type, new_value)

    def set_multiple_control_attrs(self, target_control_and_value_list):
        update_list = []
        for loc_id_tuple, ctrl_type, new_value in target_control_and_value_list:
            control_id_and_value = {}
            ctrl = self.ctrl_lookup(loc_id_tuple, ctrl_type)
            control_id_and_value['_id'] = ctrl['_id']
            control_id_and_value.update(new_value)
            update_list.append(control_id_and_value)
        return self.auth_wrapper(wasp_patch, self.host, self.port, '/objects', update_list)

def main(args):
    update_dict = json.loads(args.json_update_obj)
    dev = WaspDevice(args.host, args.port)
    if args.jsonptr_lookup:
        if update_dict:
            status, payload = dev._path_op(wasp_patch, args.resource_id, update_dict)
        else:
            status, payload = dev._path_op(wasp_get, args.resource_id)
    else:
        io_type, io_dir, io_idx, ctrl_type = [s.strip() for s in args.resource_id.split(',')]
        loc_id = dev.new_io_location_id_tuple(io_type, io_dir, int(io_idx))
        ctrl_args = [loc_id, ctrl_type]
        if update_dict:
            ctrl_args.append(update_dict)
            status, payload = dev.set_control_attrs(*ctrl_args)
        else:
            status, payload = dev.get_control_attrs(*ctrl_args)

    if args.tree:
        print('Status:', status)
        dump_tree(payload)
    else:
        kwargs = {'sort_keys': True}
        if args.pretty:
            kwargs.update(indent=4, separators=(',', ': '))
        output = json.dumps((status, payload), **kwargs)
        if not args.quiet:
            print(output)


def parse_args():
    parser = argparse.ArgumentParser(description='Iyo Dante controls utility')
    parser.add_argument('--port', help='Port to connect to', default=80)
    parser.add_argument('--tree', action='store_true', help='Display reply using a tree-like output', default=False)
    parser.add_argument('--pretty', action='store_true', help='Format output to be more readable', default=False)
    parser.add_argument('--quiet', action='store_true', help='Do not output anything', default=False)
    parser.add_argument('--jsonptr-lookup', action='store_true', help='Resource ID argument is a JSON pointer', default=False)
    parser.add_argument('host', help='hostname or address to connect to', default=None)
    parser.add_argument('resource_id', help='ID for the resource to get/patch. Format: I/O type,I/O direction,I/O index,Control type', default=None)
    parser.add_argument('json_update_obj', nargs='?', help='JSON mapping object with {"attribute_name":value} to update', default='{}')
    args = parser.parse_args()
    return args


if __name__ == '__main__':
    try:
        args = parse_args()
        sys.exit(main(args))
    except Exception as a:
        print('\033[31m' + str(a) + '\033[0m')
        #raise
        sys.exit(1)
