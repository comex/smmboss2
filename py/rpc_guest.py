import guest_access
import smmboss
import struct
import websockets.sync.client
import io
import sys
import shell

def must_read(fp, n):
    ret = fp.read(n)
    assert len(ret) == n, 'short read'
    return ret

def read64(fp):
    return struct.unpack('<Q', fp.read(8))[0]

class RPCGuest(smmboss.Guest):
    def __init__(self, base_url):
        self.base_url = base_url
        hello = self.connect()
        self.parse_hello(hello)
        super().__init__()

    def kill(self):
        self.ws.close()

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

import random
foo = random.randint(1, 1000)
print('Hello', foo)

if shell.started_shell_with == 'rpc_guest':
    import __main__
    import smmboss
    if guest := getattr(__main__, 'guest', None):
        guest.kill()
    __main__.guest = RPCGuest(sys.argv[1])
    __main__.mm = smmboss.MM.with_guest(__main__.guest)

if __name__ == '__main__':
    shell.main('rpc_guest')
