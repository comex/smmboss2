
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
        gdb.write(f'{idx:5}: 0x{addr:016x}{extra}\n')
MyBT()
