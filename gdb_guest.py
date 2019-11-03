import guest_access
import smmboss
import gdb
import re, struct

class GDBGuest(smmboss.MMGuest):
    def __init__(self):
        self.inf = gdb.selected_inferior()
        progspace = self.inf.progspace
        if progspace.filename is None:
            raise Exception("gdb didn't load main executable")

        info_targ = gdb.execute('info target', to_string=True)
        m = re.search(r'(0x\w+) - .* is \.text\n', info_targ)
        if m:
            self._slide = int(m.group(1), 0)
            print(f'Slide is {self._slide:#x}')
        else:
            raise Exception("couldn't find slide from `info target`")

        for objfile in progspace.objfiles():
            if objfile.filename == progspace.filename:
                self.build_id = objfile.build_id.ljust(64, '0')
                break
        else:
            raise Exception("couldn't find objfile")

        super().__init__()


    def try_read(self, addr, size):
        return self.inf.read_memory(addr, size)
    def try_write(self, addr, data):
        return self.inf.write_memory(addr, data)

def reg(name):
    return int(gdb.parse_and_eval(name))

class MyBT(gdb.Command):
    def __init__(self):
        super().__init__('my_bt', gdb.COMMAND_USER)
    def invoke(self, arg, from_tty):
        limit = 20
        self.print_frame('pc', reg('$pc'), '')
        self.print_frame('lr', reg('$lr'), '')
        f = reg('$x29')
        for i in range(limit):
            if not f:
                break
            # TODO make this nicer
            new_f, fpc = struct.unpack('<QQ', gdb.selected_inferior().read_memory(f, 16))
            self.print_frame(f'f{i}', fpc, f' [{f:#x}]')
            f = new_f
    def print_frame(self, idx, addr, extra):
        addr = guest.gunslide(addr) if addr else addr
        gdb.write(f'{idx:5}: 0x{addr:016x}{extra}\n')
class SomeCommand(gdb.Command):
    def __init__(self, name, func):
        super().__init__(name, gdb.COMMAND_USER)
        self.func = func
    def invoke(self, arg, from_tty):
        self.func(guest)
def add_niceties():
    global guest
    guest = guest_access.CachingGuest(GDBGuest())
    MyBT()
    gdb.parse_and_eval(f'$slide = {guest._gslide:#x}')
    for name in ['print_exported_types', 'print_idees', 'print_ent', 'print_timer']:
        SomeCommand(name, getattr(smmboss, name))

