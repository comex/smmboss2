import guest_access
import smmboss
import gdb
import re, struct

class GDBGuest(smmboss.Guest):
    def __init__(self):
        self.inf = gdb.selected_inferior()

        super().__init__()

    def try_read(self, addr, size):
        #print(f'try_read({addr:#x}, {size:#x})')
        return self.inf.read_memory(addr, size)

    def try_write(self, addr, data):
        try:
            self.inf.write_memory(addr, data)
            return len(data)
        except gdb.MemoryError:
            return 0

    def extract_image_info(self):
        stuff = gdb.execute('maint packet qXfer:libraries:read::0,10000', to_string=True)
        m = re.search(r'received: "l(.*)"', stuff)
        if not m:
            raise Exception("Strange response to qXfer:libraries:read: {stuff!r}")
        content = m.group(1)
        if not content:
            raise Exception("Empty response to qXfer:libraries:read")
        import xml.etree.ElementTree as ET
        root = ET.fromstring(content)
        for library in root:
            if library.tag == 'library':
                segs = [child for child in library if child.tag == 'segment' and 'address' in child.attrib]
                if not segs:
                    print(f'Warning: Found library tag without segment: {ET.tostring(library)!r} in {stuff!r}')
                yield {
                    'name': library.attrib.get('name'),
                    'text_start': int(segs[0].attrib['address'], 16),
                }


def reg(name):
    return int(gdb.parse_and_eval(name))

class MyBT(gdb.Command):
    def __init__(self, mm):
        super().__init__('my_bt', gdb.COMMAND_USER)
        self.mm = mm
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
            self.print_frame(f'f{i}', fpc, f' [{fpc:#x}; {f:#x}]')
            f = new_f
    def print_frame(self, idx, addr, extra):
        image_info, addr_rel = self.mm.unslide_ex(addr)
        name = str(image_info.get('name')) if image_info else 'None'
        gdb.write(f'{idx:5}: {name} + 0x{addr_rel:016x}{extra}\n')

class SomeCommand(gdb.Command):
    def __init__(self, name, func, guest):
        super().__init__(name, gdb.COMMAND_USER)
        self.func = func
        self.guest = guest
    def invoke(self, arg, from_tty):
        with self.guest:
            self.func()

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

def add_niceties(mm):
    MyBT(mm)
    gdb.parse_and_eval(f'$slide = {mm._slide:#x}')
    gdb.parse_and_eval(f'$gslide = {mm._gslide:#x}')
    for name, val in mm.world.__dict__.items():
        if getattr(val, 'commandlike', False):
            SomeCommand(name, val, mm.guest)

