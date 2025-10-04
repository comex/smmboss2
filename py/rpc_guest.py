# /// script
# dependencies = [
#     "pyyaml",
#     "ipython",
#     "websockets",
# ]
# ///
import guest_access
import smmboss
import struct
import websockets.sync.client
import io
import sys
import shell
import threading
import random
import traceback
import faulthandler
import signal
import queue
from concurrent.futures import ThreadPoolExecutor, Future
from enum import Flag

if hasattr(signal, 'SIGINFO'):
    faulthandler.register(signal.SIGINFO)

def must_read(fp, n):
    ret = fp.read(n)
    assert len(ret) == n, 'short read'
    return ret

def read64(fp):
    return struct.unpack('<Q', fp.read(8))[0]


class RPCFlags(Flag):
    BACKPRESSURE = 1
    SEND_COLLS = 2
    SEND_BG_EVENTS = 4
    PAUSE = 8

class RPCConn:
    def __init__(self, base_url):
        self.response_queue = queue.Queue()
        self.lock = threading.Lock()

        self.ws = websockets.sync.client.connect(f'{base_url}/ws/rpc')
        self.hello = self.ws.recv()

        threading.Thread(target=self.recv_thread_func, daemon=True).start()

    def shutdown(self):
        self.ws.close()

    def send_and_recv(self, data):
        with self.lock:
            f = Future()
            self.ws.send(data)
            self.response_queue.put(f)
        return f.result()

    def recv_thread_func(self):
        try:
            while True:
                try:
                    resp = self.ws.recv()
                except websockets.ConnectionClosedError as e:
                    the_exc = e
                f = self.response_queue.get_nowait()
                f.set_result(resp)
        finally:
            self.response_queue.shutdown()
            while not self.response_queue.empty():
                f = self.response_queue.get_nowait()
                f.set_exception(the_exc)

class HoseConn:
    def __init__(self, base_url):
        self.ws = websockets.sync.client.connect(f'{base_url}/ws/hose')
        self.queue = queue.Queue()
        threading.Thread(target=self.recv_thread_func, daemon=True).start()

    def recv_thread_func(self):
        while True:
            try:
                resp = self.ws.recv()
            except websockets.ConnectionClosedError as e:
                self.shutdown()
                raise
            try:
                self.queue.put(resp)
            except queue.ShutDown:
                break

    def shutdown(self):
        self.queue.shutdown()
        threading.Thread(target=self.ws.close, daemon=True).start()

    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.shutdown()

class RPCGuest(smmboss.Guest):
    def __init__(self, base_url, lifeboat={}):
        self.base_url = base_url

        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor()
        self.rpc_resp_queue = []

        self.conn = None
        self.connect(if_conn_is=None)
        self.parse_hello(self.conn.hello)
        super().__init__()

    def kill(self):
        self.ws.close()
        self.async_loop.call_soon_threadsafe(self.kill_async)
        #self.async_thread.join() <-- seems like websockets is broken and only cancels after quite a while, yay
        self.executor.shutdown(wait=False)
        return {'hose_data': self.hose_data}

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

    def connect_hose(self):
        return HoseConn(self.base_url)

    def connect(self, if_conn_is):
        with self.lock:
            if self.conn is if_conn_is:
                if self.conn is not None:
                    self.conn.shutdown()
                self.conn = RPCConn(self.base_url)

    def send_and_recv(self, data):
        try:
            conn = self.conn
            resp = conn.send_and_recv(data)
        except websockets.ConnectionClosedError:
            # reconnect, unless someone else did
            self.connect(if_conn_is=conn)
            # retry
            resp = self.conn.send_and_recv(data)

        if isinstance(resp, str):
            raise Exception(f"error: {resp!r}")
        assert isinstance(resp, bytes)
        return resp

    def try_read(self, addr, size):
        resp = self.send_and_recv(struct.pack('<BQQ',
            1, # RPC_REQ_READ
            addr,
            size
        ))
        assert len(resp) <= size
        return resp

    def try_write(self, addr, data):
        resp = self.send_and_recv(struct.pack('<BQ',
            2, # RPC_REQ_WRITE
            addr,
        ) + data)
        assert len(resp) == 8
        actual = struct.unpack('<Q', resp)[0]
        assert actual <= len(data), (resp, data, actual, len(data))
        return actual

    def par_map(self, func, iterable):
        return self.executor.map(func, iterable)

    def set_monitor_config(self, addr_lens, uniqid=1234):
        data = struct.pack('<BQQ',
            5, # RPC_REQ_SET_MONITOR_CONFIG,
            uniqid if addr_lens is not None else 0, # uniqid
            len(addr_lens) if addr_lens is not None else 0, # entry_count
        )
        if addr_lens:
            for addr, length in addr_lens:
                data += struct.pack('<QQ', addr, length)
        resp = self.send_and_recv(data)
        assert len(resp) == 0

    def set_flags_impl(self, set=RPCFlags(0), clear=RPCFlags(0)):
        assert isinstance(set, RPCFlags)
        assert isinstance(clear, RPCFlags)
        resp = self.send_and_recv(struct.pack('<BQQ',
            4, # RPC_REQ_SET_FLAGS,
            clear.value,
            set.value
        ))
        assert len(resp) == 8
        return RPCFlags(struct.unpack('<Q', resp)[0])

    def set_flags(self, backpressure=None, send_colls=None, send_bg_events=None, pause=None):
        set = clear = RPCFlags(0)
        for (flag, val) in [
            (RPCFlags.BACKPRESSURE, backpressure),
            (RPCFlags.SEND_COLLS, send_colls),
            (RPCFlags.SEND_BG_EVENTS, send_bg_events),
            (RPCFlags.PAUSE, pause),
        ]:
            if val is True:
                set |= flag
            elif val is False:
                clear |= flag
            elif val is not None:
                raise Exception(f'unexpected value {val!r}')
        return self.set_flags_impl(set=set, clear=clear)

    def monitor(self, guest_ptrs):
        was_paused = bool(self.set_flags_impl() & RPCFlags.PAUSE)
        addr_lens = [(p.addr, p.sizeof_star) for p in guest_ptrs]
        uniqid = random.randint(1, (1 << 64) - 1)
        try:
            with self.connect_hose() as hose_conn:
                self.set_monitor_config(addr_lens, uniqid)
                # ok, now get ready for new data
                if was_paused:
                    self.set_flags(pause=False)
                while True:
                    data = hose_conn.queue.get()
                    assert data.startswith(b'memmon\0\0')
                    fp = io.BytesIO(data)
                    magic = must_read(fp, 8)
                    if magic != b'memmon\0\0':
                        print('ignoring non-memmon:', data, file=sys.stderr)
                        continue
                    seen_uniqid = read64(fp)
                    if seen_uniqid != uniqid:
                        print('ignoring stale memmon:', data, file=sys.stderr)
                        continue
                    vals = []
                    for ((_, length), p) in zip(addr_lens, guest_ptrs):
                        my_data = must_read(fp, length)
                        vals.append(p.decode_data(my_data))
                    assert fp.read() == b''
                    print(vals)
        finally:
            self.set_monitor_config(None)
            if was_paused:
                self.set_flags(pause=True)

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
