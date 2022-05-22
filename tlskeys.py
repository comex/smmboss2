import gdb
import struct, re

def get_base():
    stuff = gdb.execute('monitor get info', to_string=True)
    if stuff == 'Not attached.\n':
        raise Exception("not attached!")
    m = re.search(r'\n  (0x[0-9a-f]+) - 0x[0-9a-f]+ ssl.elf', stuff)
    assert m
    return int(m.group(1), 16)
base = get_base()

class MasterSecretBP(gdb.Breakpoint):
    def stop(self):
        do_msbp()
        return False

msbp = MasterSecretBP(f'*{base+0x156e90:#x}') # after call to TLS_PRF in NSC_DeriveKey
msbp.silent = True

def do_msbp():
    frame = gdb.selected_frame()
    inf = gdb.selected_inferior()
    ssl3_master = int(frame.read_register('x19'))
    sp = int(frame.read_register('sp'))
    secret = inf.read_memory(sp + 0xe8, 48)
    pClientRandom, ulClientRandomLen = struct.unpack('<QQ', inf.read_memory(ssl3_master, 16))
    assert ulClientRandomLen == 32
    client_random = inf.read_memory(pClientRandom, ulClientRandomLen)
    print('CLIENT_RANDOM {} {}'.format(client_random.hex(), secret.hex()))
#do_msbp()
