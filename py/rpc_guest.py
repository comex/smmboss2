import guest_access
import smmboss
import struct
import websockets.sync.client
import websockets.asyncio.client
import io
import sys
import shell
import threading
import asyncio

def must_read(fp, n):
    ret = fp.read(n)
    assert len(ret) == n, 'short read'
    return ret

def read64(fp):
    return struct.unpack('<Q', fp.read(8))[0]

class RPCGuest(smmboss.Guest):
    def __init__(self, base_url, lifeboat={}):
        self.base_url = base_url

        self.hose_data = lifeboat.get('hose_data', [])
        self.async_loop = asyncio.new_event_loop()
        self.async_thread = threading.Thread(target=self.async_thread_func, daemon=True)
        self.async_thread.start()

        hello = self.connect()
        self.parse_hello(hello)
        super().__init__()

    def kill(self):
        self.ws.close()
        self.async_loop.call_soon_threadsafe(self.kill_async)
        #self.async_thread.join() <-- seems like websockets is broken and only cancels after quite a while, yay
        return {'hose_data': self.hose_data[:]}

    def kill_async(self):
        print('cancelling', file=sys.stderr)
        self.hose_future.cancel()

    def async_thread_func(self):
        asyncio.set_event_loop(self.async_loop)
        self.hose_future = self.async_loop.create_task(self.hose())
        try:
            self.async_loop.run_until_complete(self.hose_future)
        except asyncio.exceptions.CancelledError:
            print('cancelled', file=sys.stderr)

    def parse_hello(self, hello):
        assert isinstance(hello, bytes)
        fp = io.BytesIO(hello)
        image_infos = []
        while fp.read(1):
            fp.seek(-1, 1)
            info = {}
            for prefix in ['image', 'text', 'rodata', 'data']:
                info[f'{prefix}_start'] = read64(fp)
                info[f'{prefix}_size'] = read64(fp)
                info[f'{prefix}_end'] = info[f'{prefix}_start'] + info[f'{prefix}_size']
            info['build_id'] = must_read(fp, 16)
            image_infos.append(info)
        self.image_infos = image_infos

    def extract_image_info(self):
        return self.image_infos

    def connect(self):
        self.ws = websockets.sync.client.connect(f'{self.base_url}/ws/rpc')
        hello = self.ws.recv()
        return hello

    async def hose(self):
        while True:
            async with websockets.asyncio.client.connect(f'{self.base_url}/ws/hose') as hose_socket:
                try:
                    print('hose connected', file=sys.stderr)
                    while True:
                        try:
                            resp = await hose_socket.recv()
                        except websockets.ConnectionClosedError:
                            print('hose disconnected', file=sys.stderr)
                            break
                        else:
                            self.hose_data.append(resp)
                finally:
                    await hose_socket.close() # why do I have to do this?
            print('<<')

    def send_with_reconnect(self, data):
        try:
            self.ws.send(data)
        except websockets.ConnectionClosedError:
            self.connect() # ignore hello
            self.ws.send(data)

    def try_read(self, addr, size):
        self.send_with_reconnect(struct.pack('<BQQ',
            1, # RPC_REQ_READ
            addr,
            size
        ))
        resp = self.ws.recv()
        if isinstance(resp, str):
            raise Exception(f"read error: {resp!r}")
        assert isinstance(resp, bytes)
        assert len(resp) <= size
        return resp

    def try_write(self, addr, data):
        self.send_with_reconnect(struct.pack('<BQQ',
            2, # RPC_REQ_WRITE
            addr,
            len(data)
        ) + data)
        resp = self.ws.recv()
        if isinstance(resp, str):
            raise Exception(f"write error: {resp!r}")
        assert isinstance(resp, bytes)
        assert len(resp) == 8
        actual = struct.unpack('<Q', resp)[0]
        assert actual <= len(data)
        return actual

    def set_monitor_config(self, addr_lens):
        data = struct.pack('<BQQ',
            5, # RPC_REQ_SET_MONITOR_CONFIG,
            1234, # uniqid
            len(addr_lens), # entry_count
        )
        for addr, length in addr_lens:
            data += struct.pack('<QQ', addr, length)
        self.send_with_reconnect(data)
        resp = self.ws.recv()
        if isinstance(resp, str):
            raise Exception(f"error: {resp!r}")
        assert len(resp) == 0

import random
foo = random.randint(1, 1000)
print('Hello', foo)

if shell.started_shell_with == 'rpc_guest':
    import __main__
    import smmboss
    if guest := getattr(__main__, 'guest', None):
        lifeboat = guest.kill()
    else:
        lifeboat = {}
    __main__.guest = RPCGuest(sys.argv[1], lifeboat=lifeboat)
    __main__.mm = smmboss.MM.with_guest(__main__.guest)

if __name__ == '__main__':
    shell.main('rpc_guest')
