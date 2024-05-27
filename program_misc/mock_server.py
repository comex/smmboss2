#!/usr/bin/env python3

import asyncio
import websockets.server
import struct

async def serve_rpc(websocket):
    async for message in websocket:
        assert isinstance(message, bytes)
        header_fmt = '<BQQ'
        header_len = struct.calcsize(header_fmt)
        assert len(message) >= header_len
        ty, rw_addr, rw_len = struct.unpack(header_fmt, message)
        if ty == 1: # RPC_REQ_READ
            assert len(message) == header_len
            print(f'...read addr={rw_addr:#x} len={rw_len:#x}')
            if rw_addr == 0x1234:
                await websocket.send('example error')
            elif rw_addr == 0x1235:
                await websocket.close()
            else:
                await websocket.send(b'a' * rw_len);
        elif ty == 2: # RPC_REQ_WRITE
            assert len(message) == header_len + rw_len
            body = message[header_len:]
            print(f'...write addr={rw_addr:#x} content={body}')
            await websocket.send(b'');

async def serve_hose(websocket):
    while True:
        await websocket.send(b'fake hose data')
        await asyncio.sleep(1)

async def server(websocket):
    if websocket.path == '/ws/rpc':
        return await serve_rpc(websocket)
    elif websocket.path == '/ws/hose':
        return await serve_hose(websocket)
    else:
        raise Exception('unexpected path')

async def main():
    async with websockets.server.serve(server, "localhost", 8002):
        await asyncio.Future()  # run forever

asyncio.run(main())
