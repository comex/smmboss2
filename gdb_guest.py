import guest_access
import smmboss
import gdb
import re, struct

class GDBGuest(smmboss.MMGuest):
    def __init__(self):
        self.inf = gdb.selected_inferior()
        self.build_id = None

        kind = 'yuzu'
        if kind == 'yuzu':
            stuff = gdb.execute('maint packet qXfer:libraries:read::0,10000', to_string=True)
            m = re.search(r'received: "l(<\?xml.*</library-list>)"', stuff)
            assert m, "invalid response to libraries:read"
            xml = m.group(1)
            m = re.search('<library name="(?:main|Slope.nss)"><segment address="(.*?)"', xml)
            assert m, "couldn't find main by hackily running a regex on the xml"
            self._slide = int(m.group(1), 16)
        elif kind == 'yuzu-info-shared':
            # note(2023-3-4): why does this not work?
            stuff = gdb.execute('info shared', to_string=True)
            m = re.search(r'^(0x\w+)\s+0x\w+\s+.*\bmain$', stuff, re.M)
            if not m:
                raise Exception("couldn't find slide using `info shared`")
            self._slide = int(m.group(1), 16)
        elif kind == 'twili':
            stuff = gdb.execute('maint packet qOffsets', to_string=True)
            m = re.search(r'received: "TextSeg=([^"]+)"', stuff)
            if not m:
                raise Exception("couldn't find slide using qOffsets")
            self._slide = int(m.group(1), 16)
            pid = self.inf.pid
            stuff = gdb.execute(f'maint packet qXfer:exec-file:read:{pid:x}:0,999', to_string=True)
            m = re.search(r'received: "l([^"]+)"', stuff)
            if not m:
                raise Exception("couldn't find build id using qXfer:exec-file")
            self.build_id = m.group(1)
        else:
            assert kind == 'atmosphere'
            # Atmosphere GDB stub
            stuff = gdb.execute('monitor get info', to_string=True)
            if stuff == 'Not attached.\n':
                raise Exception("not attached!")
            m = re.search(r'\n  (0x[0-9a-f]+) - 0x[0-9a-f]+ Slope\.nss', stuff)
            if not m:
                raise Exception("couldn't find slide using monitor get info")
            self._slide = int(m.group(1), 16)

        if self.build_id is not None and len(self.build_id) != 64:
            raise Exception(f"build_id is {self.build_id!r}, which is not length 64")

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
        addr = guest.unslide(addr) if addr else addr
        gdb.write(f'{idx:5}: 0x{addr:016x}{extra}\n')

class SomeCommand(gdb.Command):
    def __init__(self, name, func):
        super().__init__(name, gdb.COMMAND_USER)
        self.func = func
    def invoke(self, arg, from_tty):
        self.func()
def add_niceties():
    global guest
    guest = guest_access.CachingGuest(GDBGuest())
    MyBT()
    gdb.parse_and_eval(f'$gslide = {guest._gslide:#x}')
    gdb.parse_and_eval(f'$slide = {guest._slide:#x}')
    for name in ['print_exported_types', 'print_idees', 'print_ent', 'print_timer', 'print_block_kind_info', 'print_bg']:
        SomeCommand(name, getattr(guest.world, name))

class MemDump:
    def __init__(self):
        stuff = gdb.execute('mon get mappings', to_string=True)
        heap_regions_matches = re.findall(r'(0x\w+) - (0x\w+) rw- Normal', stuff)
        heap_regions = []
        for start, last in heap_regions_matches:
            start = int(start, 16)
            last = int(last, 16)
            heap_regions.append((start, last))
        merged_regions = []
        i = 0
        while i < len(heap_regions):
            start, last = heap_regions[i]
            while i + 1 < len(heap_regions) and \
                heap_regions[i+1][0] == last + 1:
                last = heap_regions[i+1][1]
                i += 1
            merged_regions.append((start, last))
            i += 1
        self.dumps = {}
        inf = gdb.selected_inferior()
        for start, last in merged_regions:
            size = last - start + 1
            print(f'reading {size} bytes @ {start:#x}...')
            if size < 100 * 1024:
                self.dumps[start] = inf.read_memory(start, size)
            else:
                # This depends on a Yuzu patch I'm not going to upstream
                # because it's extremely ugly, and it also hardcodes the name
                # of the computer running Yuzu.  It's just a hack.
                stuff = gdb.execute(f'mon dump mem {start:#x} {size:#x}', to_string=True)
                assert stuff.startswith('OK'), stuff
                import subprocess
                self.dumps[start] = subprocess.check_output(['ssh', 'solidus', 'cat', '/tmp/yuzu-dump.bin'])

    def find(self, regex, flags=0, exact=False):
        if exact:
            regex = re.escape(regex)
        r = re.compile(regex, flags=flags)
        for addr, dump in self.dumps.items():
            for m in r.finditer(dump):
                yield (addr + m.start(), m)

    def find_print_addrs(self, *args, **kwargs):
        for addr, m in self.find(*args, **kwargs):
            print(hex(addr))
