source /usr/src/nx/smmboss2/so.py
define break_set_state
    hb *($slide+0x710122f040)
    commands
    silent
    set $_state = *(int*)($x0+0x8)
    set $_oldstate = *(int*)($x0+0xc)
    set $_states = *(char **)($x0+0x40)
    printf "smgr=%p vt=%p old=%d", $x0, *(void **)$x0, $_oldstate
    #if $_oldstate != -1
    #    printf " (%s)", *(char **)($_states + 0x10 * $_oldstate + 8)
    #end
    printf " new=%d", $_state
    if $_state != -1
        printf " (%s)", *(char **)($_states + 0x10 * $_state + 8)
    end
    printf "\n"
    c
    end

end

define break_rng
    del
    hb *($slide+0x71007d5cb0)
    commands
        print_timer
        printf "note\n"
        c
    end
    hb *($slide+0x710089ebe4)
    commands
        print_timer
        printf "COINBOUNCE\n"
        c
    end
    hb *($slide+0x71003e4d30)
    commands
        print_timer
        printf "%d/%d\n", $w8, $w22
        c
    end
end

define display_timer
    py gdb.execute('display *(int*)%#x' % (addrof(OtherTimerRelated.get(guest), 'frames').addr,))
end

define watch_rng
    py gdb.execute(f'watch *(int*){ActorMgr.get(guest).cur_world.area_sys.rngplus.rng.addr:#x}')
end
