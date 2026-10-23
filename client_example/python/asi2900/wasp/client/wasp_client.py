"""WASP (Web AudioScience Protocol) async client.

Compatible with CPython 3.7+ and MicroPython 1.27+ (Unix port).
Implements WASP API v3.2.0.

Library usage::

    import asyncio
    from wasp.client import WaspClient

    def on_update(event_type, path, body):
        print(f'Update: {event_type} {path}')

    client = WaspClient('192.168.1.100', on_update=on_update)

    async def main():
        asyncio.create_task(client.run())
        objects = await client.get_objects()
        await client.patch_object(9, {'level': 7})

    asyncio.run(main())

CLI usage (CPython only)::

    python wasp_client.py <host> <command> [options]
    python wasp_client.py 192.168.1.100 monitor
    python wasp_client.py 192.168.1.100 get 34 level

Run with ``--help`` for full CLI usage.
"""

try:
    import ujson as json
except ImportError:
    import json
import asyncio

_API = '/wasp/r2'
_UPD = '/wasp/u2'
_MAX_BACKOFF_S = 30


class WaspClient:
    """Async WASP protocol client with automatic reconnection.

    Provides methods to query and update WASP objects on a remote device
    and maintains a persistent update stream connection that invokes a
    user-supplied callback when object values change.

    Args:
        host: Device hostname or IP address.
        port: HTTP port (default 80).
        on_update: Callback ``f(event_type, path, body)`` for object updates.

            *event_type* is ``'update:checkpoint'`` or ``'update:obj'``.

            *path* is ``'/objects'`` for checkpoints,
            ``'/objects/{id}'`` for individual object updates.

            *body* is a list of all objects for checkpoints or a dict of
            updated properties for object updates.

        on_connected: No-arg callback invoked when the update stream connects.
        on_disconnected: No-arg callback invoked when the update stream
            disconnects.
        exclude_types: List of WASP object type IDs to exclude from the
            update stream (e.g. ``['ctrl:meter']``).
        checkpoint_mode: Optional checkpoint transfer mode.  Pass
            ``'chunks'`` to receive ``update:checkpoint_begin``,
            ``update:checkpoint_chunk``, and ``update:checkpoint_end``
            events instead of one full ``update:checkpoint`` event.
        log: Logging function accepting a single message string.
    """

    def __init__(self, host, port=80, on_update=None, on_connected=None,
                 on_disconnected=None, exclude_types=None, log=None,
                 checkpoint_mode=None):
        self.host = host
        self.port = port
        self._on_update = on_update
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._auth_id = None
        self._connected = False
        self._running = False
        self._exclude_types = exclude_types or []
        self._checkpoint_mode = checkpoint_mode
        self._log = log if log else lambda msg: None
        self._backoff_s = 1

    @property
    def connected(self):
        """True when the update stream is connected and receiving events."""
        return self._connected

    # ---- I/O helpers ----

    async def _write(self, writer, data):
        if isinstance(data, str):
            data = data.encode()
        writer.write(data)
        await writer.drain()

    async def _close_writer(self, writer):
        writer.close()
        await writer.wait_closed()

    async def _read_exactly(self, reader, n):
        """Read exactly *n* bytes, raising OSError on premature close."""
        try:
            return await reader.readexactly(n)
        except EOFError:
            raise OSError('Connection closed')

    # ---- HTTP transport ----

    async def _http_request(self, method, path, body=None, extra_headers=None):
        """Low-level HTTP/1.1 request.

        Returns:
            Tuple of (status_code, headers_dict, body_str).
        """
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            req = f'{method} {path} HTTP/1.1\r\nHost: {self.host}\r\n'
            if self._auth_id:
                req += f'Authorization: Hawk id="{self._auth_id}"\r\n'
            if extra_headers:
                for k, v in extra_headers.items():
                    req += f'{k}: {v}\r\n'
            body_enc = None
            if body is not None:
                body_enc = (
                    json.dumps(body).encode()
                    if not isinstance(body, bytes) else body
                )
                req += (
                    'Content-Type: application/json\r\n'
                    f'Content-Length: {len(body_enc)}\r\n'
                )
            req += 'Connection: close\r\n\r\n'

            await self._write(writer, req)
            if body_enc:
                await self._write(writer, body_enc)

            status_line = await reader.readline()
            if not status_line:
                raise OSError('Empty response')
            status = int(status_line.split(None, 2)[1])

            resp_hdrs = {}
            content_length = -1
            chunked = False
            while True:
                line = await reader.readline()
                if not line or line == b'\r\n':
                    break
                text = line.decode().strip()
                sep = text.find(':')
                if sep > 0:
                    k = text[:sep].strip()
                    v = text[sep + 1:].strip()
                    resp_hdrs[k] = v
                    kl = k.lower()
                    if kl == 'content-length':
                        content_length = int(v)
                    elif kl == 'transfer-encoding' and 'chunked' in v.lower():
                        chunked = True

            if chunked:
                resp_body = b''
                while True:
                    sz_line = await reader.readline()
                    if not sz_line or not sz_line.strip():
                        break
                    sz = int(sz_line.strip(), 16)
                    if sz == 0:
                        await reader.readline()
                        break
                    resp_body += await self._read_exactly(reader, sz)
                    await reader.readline()
            elif content_length > 0:
                resp_body = await self._read_exactly(reader, content_length)
            elif content_length == 0:
                resp_body = b''
            else:
                resp_body = await reader.read(-1)

            return status, resp_hdrs, resp_body.decode()
        finally:
            await self._close_writer(writer)

    async def _authed_request(self, method, path, body=None,
                              extra_headers=None):
        """HTTP request with transparent re-authentication on 401."""
        status, hdrs, resp = await self._http_request(
            method, path, body, extra_headers,
        )
        if status == 401:
            await self.authenticate()
            status, hdrs, resp = await self._http_request(
                method, path, body, extra_headers,
            )
        return status, hdrs, resp

    # ---- Authentication ----

    async def authenticate(self):
        """Obtain an authentication token from the device.

        Called automatically when API requests receive a 401 response.

        Returns:
            The authentication token string.

        Raises:
            OSError: If the device rejects the authentication request.
        """
        self._auth_id = None
        status, _, body = await self._http_request(
            'POST', f'{_API}/device/auth',
            extra_headers={'Authorization': 'Hawk'},
        )
        if status == 200 and body:
            self._auth_id = json.loads(body).get('id')
            if self._auth_id:
                return self._auth_id
        raise OSError(f'WASP auth failed (status {status})')

    # ---- Public WASP API ----

    async def ping(self):
        """Check if the device is reachable and responsive.

        Returns:
            True if the device responds, False otherwise.
        """
        try:
            status, _, _ = await self._authed_request(
                'GET', f'{_API}/device/ping',
            )
            return status == 200
        except OSError:
            return False

    async def get_device_info(self):
        """Retrieve device information.

        Returns:
            List of device info objects (device:user_data, device:hw_desc,
            device:sw_desc).

        Raises:
            OSError: On request failure.
        """
        status, _, body = await self._authed_request(
            'GET', f'{_API}/device/info',
        )
        if status != 200:
            raise OSError(f'GET device/info failed (status {status})')
        return json.loads(body) if body else None

    async def get_objects(self, range_start=None, range_end=None):
        """Retrieve device objects.

        Args:
            range_start: Optional first object index to retrieve.
            range_end: Optional last object index to retrieve (inclusive).

        Returns:
            List of object dicts.

        Raises:
            OSError: On request failure.
        """
        extra = None
        if range_start is not None:
            r = f'items={range_start}-'
            if range_end is not None:
                r += str(range_end)
            extra = {'Range': r}
        status, _, body = await self._authed_request(
            'GET', f'{_API}/objects', extra_headers=extra,
        )
        if status != 200:
            raise OSError(f'GET objects failed (status {status})')
        return json.loads(body) if body else []

    async def get_object(self, obj_id):
        """Retrieve a single object by its ID.

        Args:
            obj_id: Integer object ID.

        Returns:
            Object dict.

        Raises:
            OSError: On request failure.
        """
        status, _, body = await self._authed_request(
            'GET', f'{_API}/objects/{obj_id}',
        )
        if status != 200:
            raise OSError(f'GET object {obj_id} failed (status {status})')
        return json.loads(body) if body else None

    async def patch_object(self, obj_id, updates):
        """Update one or more properties on an object.

        Args:
            obj_id: Integer object ID.
            updates: Dict mapping property names to new values.
                Example: ``{'level': 7}``

        Returns:
            HTTP status code (200 for immediate completion,
            202 for accepted/async).

        Raises:
            OSError: On request failure.
        """
        status, _, _ = await self._authed_request(
            'PATCH', f'{_API}/objects/{obj_id}', updates,
        )
        if status not in (200, 202):
            raise OSError(f'PATCH object {obj_id} failed (status {status})')
        return status

    async def patch_objects(self, updates_list):
        """Bulk-update properties on multiple objects in a single request.

        Args:
            updates_list: List of dicts, each containing ``'_id'`` and the
                properties to update.
                Example: ``[{'_id': 34, 'level': 3}, {'_id': 38, 'level': 7}]``

        Returns:
            HTTP status code (200 or 202).

        Raises:
            OSError: On request failure.
        """
        status, _, _ = await self._authed_request(
            'PATCH', f'{_API}/objects', updates_list,
        )
        if status not in (200, 202):
            raise OSError(f'PATCH objects failed (status {status})')
        return status

    async def get_property(self, obj_id, prop):
        """Retrieve a single property value from an object.

        Args:
            obj_id: Integer object ID.
            prop: Property name string.

        Returns:
            The property value (parsed from JSON).

        Raises:
            OSError: On request failure.
        """
        status, _, body = await self._authed_request(
            'GET', f'{_API}/objects/{obj_id}/{prop}',
        )
        if status != 200:
            raise OSError(
                f'GET {prop} on object {obj_id} failed (status {status})'
            )
        return json.loads(body) if body else None

    async def patch_property(self, obj_id, prop, value):
        """Update a single property on an object.

        The value is sent as a naked JSON value, not wrapped in a dict.

        Args:
            obj_id: Integer object ID.
            prop: Property name string.
            value: New property value.

        Returns:
            HTTP status code (200 or 202).

        Raises:
            OSError: On request failure.
        """
        status, _, _ = await self._authed_request(
            'PATCH', f'{_API}/objects/{obj_id}/{prop}', value,
        )
        if status not in (200, 202):
            raise OSError(
                f'PATCH {prop} on object {obj_id} failed (status {status})'
            )
        return status

    async def get_schemas(self):
        """Retrieve all object schemas.

        Returns:
            Schemas dict mapping schema IDs to schema definitions.

        Raises:
            OSError: On request failure.
        """
        status, _, body = await self._authed_request(
            'GET', f'{_API}/schemas',
        )
        if status != 200:
            raise OSError(f'GET schemas failed (status {status})')
        return json.loads(body) if body else {}

    async def get_schema(self, schema_id):
        """Retrieve a specific schema by its ID.

        Args:
            schema_id: Schema identifier string.

        Returns:
            Schema definition dict.

        Raises:
            OSError: On request failure.
        """
        status, _, body = await self._authed_request(
            'GET', f'{_API}/schemas/{schema_id}',
        )
        if status != 200:
            raise OSError(
                f'GET schema {schema_id} failed (status {status})'
            )
        return json.loads(body) if body else None

    # ---- Update stream ----

    async def _process_update_stream(self):
        """Connect to /wasp/u2/objects and process events until disconnect."""
        if not self._auth_id:
            await self.authenticate()

        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            req = f'GET {_UPD}/objects HTTP/1.1\r\nHost: {self.host}\r\n'
            req += f'Authorization: Hawk id="{self._auth_id}"\r\n'
            if self._exclude_types:
                req += (
                    'X-Wasp-Stream-Exclude-Obj-Type: '
                    f'{";".join(self._exclude_types)}\r\n'
                )
            if self._checkpoint_mode:
                req += (
                    'X-Wasp-Checkpoint-Mode: '
                    f'{self._checkpoint_mode}\r\n'
                )
            req += 'Connection: keep-alive\r\n\r\n'

            await self._write(writer, req)

            status_line = await reader.readline()
            if not status_line:
                raise OSError('No update stream response')
            status = int(status_line.split(None, 2)[1])

            if status == 401:
                self._auth_id = None
                raise OSError('Update stream auth expired')
            if status != 202:
                raise OSError(f'Update stream status {status}')

            chunked = False
            while True:
                line = await reader.readline()
                if not line or line == b'\r\n':
                    break
                if b'chunked' in line.lower():
                    chunked = True
            if not chunked:
                raise OSError('Expected chunked transfer encoding')

            self._connected = True
            self._backoff_s = 1
            if self._on_connected:
                try:
                    self._on_connected()
                except Exception as e:
                    self._log(f'on_connected callback error: {e}')

            # Accumulate chunks into complete WASP records delimited
            # by '---\n'.  A single record may span multiple HTTP
            # chunks (e.g. the batched checkpoint writer in
            # http_server.py).
            record = bytearray()
            while self._running:
                size_line = await reader.readline()
                if not size_line or not size_line.strip():
                    break
                chunk_size = int(size_line.strip(), 16)
                if chunk_size == 0:
                    break

                chunk = await self._read_exactly(reader, chunk_size)
                await reader.readline()

                if chunk[:4] == b'---\n':
                    if record:
                        self._parse_and_dispatch(record)
                    record = bytearray(chunk[4:])
                else:
                    record.extend(chunk)

                if record and record[-1:] == b'\n':
                    if self._try_parse_and_dispatch(record):
                        record = bytearray()

            if record:
                self._parse_and_dispatch(record)
        finally:
            await self._close_writer(writer)

    def _try_parse_and_dispatch(self, record):
        """Dispatch *record* if it already contains a complete JSON event."""
        try:
            text = bytes(record).decode()
            if text.endswith('\n'):
                text = text[:-1]
            if text:
                self._dispatch_event(json.loads(text))
            return True
        except (ValueError, KeyError):
            return False

    def _parse_and_dispatch(self, record):
        """Decode a complete WASP record (bytearray) and dispatch it."""
        text = bytes(record).decode()
        if text.endswith('\n'):
            text = text[:-1]
        if text:
            try:
                self._dispatch_event(json.loads(text))
            except (ValueError, KeyError) as e:
                self._log(f'Malformed event: {e}')

    def _dispatch_event(self, event):
        """Route an update stream event to the user callback."""
        if not self._on_update:
            return
        etype = event.get('_type')
        try:
            if etype == 'update:checkpoint':
                self._on_update(
                    etype,
                    event.get('path', '/objects'),
                    event.get('body'),
                )
            elif etype in (
                'update:checkpoint_begin',
                'update:checkpoint_chunk',
                'update:checkpoint_end',
            ):
                self._on_update(
                    etype,
                    event.get('path', '/objects'),
                    event.get('body'),
                )
            elif etype == 'update:obj':
                self._on_update(
                    etype, event.get('path'), event.get('body'),
                )
            elif etype == 'update:group_prefix':
                self._on_update(
                    etype,
                    event.get('prefix', '/objects/'),
                    event,
                )
        except Exception as e:
            self._log(f'on_update callback error: {e}')

    # ---- Main task ----

    async def run(self):
        """Main task entry point: maintain the update stream with auto-reconnect.

        This coroutine runs indefinitely, connecting to the WASP update
        stream, processing events, and automatically reconnecting with
        exponential backoff on failure.

        Add to the event loop with::

            asyncio.create_task(client.run())

        Stop cleanly with::

            client.stop()
        """
        self._running = True
        while self._running:
            try:
                await self._process_update_stream()
            except OSError as e:
                self._log(f'WASP stream error: {e}')
            except Exception as e:
                self._log(f'WASP unexpected error: {e}')

            if self._connected:
                self._connected = False
                if self._on_disconnected:
                    try:
                        self._on_disconnected()
                    except Exception:
                        pass

            if not self._running:
                break

            self._log(f'WASP reconnecting in {self._backoff_s}s')
            await asyncio.sleep(self._backoff_s)
            self._backoff_s = min(self._backoff_s * 2, _MAX_BACKOFF_S)

    def stop(self):
        """Request the run() task to stop.

        The task will exit after the current sleep or connection attempt
        completes.  For immediate cancellation, cancel the task directly.
        """
        self._running = False


# ---- Public helpers ----

def iter_group_prefix(event):
    """Iterate over individual updates in an ``update:group_prefix`` event.

    Yields ``(path, body)`` tuples where *path* is the full object path
    (e.g. ``'/objects/5'``) and *body* is a single-key dict mapping the
    property name to its new value (e.g. ``{'peak': -37}``).

    Usage inside an ``on_update`` callback::

        def on_update(event_type, path, body):
            if event_type == 'update:group_prefix':
                for obj_path, obj_body in iter_group_prefix(body):
                    process(obj_path, obj_body)
    """
    prefix = event.get('prefix', '/objects/')
    prop = event.get('prop')
    for obj_id_str, value in event.get('body', {}).items():
        yield f'{prefix}{obj_id_str}', {prop: value}


# ---- CLI ----

_CLI_USAGE = """\
Usage: wasp_client [OPTIONS] HOST COMMAND [ARGS ...]

Commands:
  ping                           Ping the device
  info                           Get device information
  objects                        List all objects
  object <id>                    Get a single object
  get <id> <property>            Get a property value
  set <id> <property> <value>    Set a property value
  patch <id> key=val [key=val]   Update multiple properties
  schemas                        List all schemas
  schema <schema_id>             Get a specific schema
  monitor                        Monitor the update stream

Options:
  --port PORT                    HTTP port (default: 80)
  --exclude TYPE [TYPE ...]      Exclude types from monitor stream
  --range-start N                First object index (objects cmd)
  --range-end N                  Last object index (objects cmd)
"""


def _parse_json_value(s):
    """Parse a string as JSON, falling back to plain string."""
    try:
        return json.loads(s)
    except ValueError:
        return s


def _json_dumps_pretty(obj):
    """Pretty JSON on CPython, compact JSON on MicroPython builds without indent."""
    try:
        return json.dumps(obj, indent=2)
    except TypeError:
        return json.dumps(obj)


def _parse_args(argv=None):
    """Parse CLI arguments.  Returns a namespace with *host*, *command*,
    *args* (list), *port*, *exclude*, *range_start*, *range_end*."""
    import argparse
    parser = argparse.ArgumentParser(description=_CLI_USAGE)
    parser.add_argument('host')
    parser.add_argument('command')
    parser.add_argument('args', nargs='*')
    parser.add_argument('--port', type=int, default=80)
    parser.add_argument('--exclude', nargs='*')
    parser.add_argument('--range-start', type=int, default=None)
    parser.add_argument('--range-end', type=int, default=None)
    return parser.parse_args(argv)


async def _cli_main(args, out=None):
    """Execute a CLI command.  *out* is the output stream (defaults to
    sys.stdout).  Passing an explicit stream avoids the need to reassign
    sys.stdout, which is not supported on MicroPython."""
    import sys
    if out is None:
        out = sys.stdout
    log = lambda msg: print(msg, file=sys.stderr)
    cmd = args.command
    cmd_args = args.args if hasattr(args, 'args') and args.args else []

    if cmd == 'monitor':
        def on_update(event_type, path, body):
            print(_json_dumps_pretty(
                {'type': event_type, 'path': path, 'body': body},
            ), file=out, flush=True)

        client = WaspClient(
            args.host, port=args.port,
            on_update=on_update,
            on_connected=lambda: print('--- Connected ---', file=out, flush=True),
            on_disconnected=lambda: print('--- Disconnected ---', file=out, flush=True),
            exclude_types=args.exclude,
            log=log,
        )
        await client.run()
        return

    client = WaspClient(args.host, port=args.port, log=log)
    try:
        if cmd == 'ping':
            ok = await client.ping()
            print('OK' if ok else 'FAILED', file=out)
            raise SystemExit(0 if ok else 1)

        elif cmd == 'info':
            result = await client.get_device_info()
            print(_json_dumps_pretty(result), file=out)

        elif cmd == 'objects':
            result = await client.get_objects(
                getattr(args, 'range_start', None),
                getattr(args, 'range_end', None),
            )
            print(_json_dumps_pretty(result), file=out)

        elif cmd == 'object':
            if not cmd_args:
                raise SystemExit('Usage: object <id>')
            result = await client.get_object(int(cmd_args[0]))
            print(_json_dumps_pretty(result), file=out)

        elif cmd == 'get':
            if len(cmd_args) < 2:
                raise SystemExit('Usage: get <id> <property>')
            result = await client.get_property(
                int(cmd_args[0]), cmd_args[1],
            )
            print(_json_dumps_pretty(result), file=out)

        elif cmd == 'set':
            if len(cmd_args) < 3:
                raise SystemExit('Usage: set <id> <property> <value>')
            value = _parse_json_value(cmd_args[2])
            status = await client.patch_property(
                int(cmd_args[0]), cmd_args[1], value,
            )
            print(f'Status: {status}', file=out)

        elif cmd == 'patch':
            if len(cmd_args) < 2:
                raise SystemExit('Usage: patch <id> key=value ...')
            obj_id = int(cmd_args[0])
            updates = {}
            for kv in cmd_args[1:]:
                k, sep, v = kv.partition('=')
                if not sep:
                    raise SystemExit(f'Invalid KEY=VALUE pair: {kv}')
                updates[k] = _parse_json_value(v)
            status = await client.patch_object(obj_id, updates)
            print(f'Status: {status}', file=out)

        elif cmd == 'schemas':
            result = await client.get_schemas()
            print(_json_dumps_pretty(result), file=out)

        elif cmd == 'schema':
            if not cmd_args:
                raise SystemExit('Usage: schema <schema_id>')
            result = await client.get_schema(cmd_args[0])
            print(_json_dumps_pretty(result), file=out)

        else:
            raise SystemExit(f'Unknown command: {cmd}')

    except OSError as e:
        raise SystemExit(f'Error: {e}')


def main():
    """CLI entry point."""
    args = _parse_args()
    try:
        asyncio.run(_cli_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
