#!/usr/bin/env python3
"""Emulate the Date & time cave (SysSet_Fun entry 26 -> dt_screen) with stubbed app calls.
usage: emu_test.py PATCHED_APP"""
import sys, struct, time as _t, calendar, re
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE, UC_HOOK_INTR
from unicorn.mips_const import *
from elftools.elf.elffile import ELFFile

APP = sys.argv[1]
GP = 0x5d9530
STUB = 0x10000000
STACK = 0x7f000000
JT26 = 0x5588d0 + 26 * 4

G = dict(time=-0x7468, localtime_r=-0x7878, usleep=-0x6b10, sprintf=-0x7e08,
         system=-0x7108, presskey=-0x723c, key_clear=-0x73b0, get_key=-0x7d0c,
         Input=None, Gui=None, draw_text=-0x7e20, display_update=-0x74d4,
         set_text_color=-0x6c6c, set_font=-0x790c, draw_sprite=-0x6f98,
         blit_sprite=-0x70c8, GetIndexData=-0x6e34, PicList=None, Pic=None,
         ShowSettingFace=-0x6fc0)


def run(start_epoch, keys, entry_key=4, burst=False):
    # 0 = empty key queue (idle poll). Real use: queue is empty on entry and between presses.
    if burst:
        keys = [0] + list(keys)
    else:
        keys = [0] + [x for k in keys for x in (k, 0)]
    elf = ELFFile(open(APP, 'rb'))
    raw = open(APP, 'rb').read()
    uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    for s in elf.iter_segments():
        if s['p_type'] != 'PT_LOAD':
            continue
        lo = s['p_vaddr'] & ~0xfff
        hi = (s['p_vaddr'] + s['p_memsz'] + 0xfff) & ~0xfff
        uc.mem_map(lo, hi - lo)
        uc.mem_write(s['p_vaddr'], raw[s['p_offset']: s['p_offset'] + s['p_filesz']])
    uc.mem_map(STUB, 0x10000)
    uc.mem_map(STACK - 0x100000, 0x100000)
    uc.mem_map(0x20000000, 0x10000)   # fake objects (Pic entry etc.)
    stubs = {}
    for i, (name, off) in enumerate((k, v) for k, v in G.items() if v is not None):
        addr = STUB + i * 16
        uc.mem_write(GP + off, struct.pack('<I', addr))
        uc.mem_write(addr, struct.pack('<II', 0x03e00008, 0))   # jr ra; nop
        stubs[addr] = name
    # fake PicList entry for GetIndexData: {?, path, x, y}
    uc.mem_write(0x20000000, struct.pack('<IIII', 0, 0x20000100, 7, 9))
    # data objects reached via GOT (Pic must be writable)
    uc.mem_write(GP - 0x737c, struct.pack('<I', 0x20000200))

    st = dict(keys=list(keys), texts=[], frames=[], tv=None, cmd=None, sysret=0, title=None, sprites=[])

    def rd_str(a):
        b = uc.mem_read(a, 256)
        return bytes(b).split(b'\0', 1)[0].decode()

    def c_sprintf(fmt, args):
        out, ai = '', 0
        for m in re.finditer(r'%(\d*)([sdu%])|[^%]+', fmt):
            if m.group(0).startswith('%'):
                w, c = m.group(1), m.group(2)
                if c == '%':
                    out += '%'; continue
                v = args[ai]; ai += 1
                if c == 's':
                    out += rd_str(v)
                elif c in 'du':
                    if c == 'd' and v >= 1 << 31:
                        v -= 1 << 32
                    out += (('%0' + w + 'd') % v) if w else str(v)
            else:
                out += m.group(0)
        return out

    def hook(uc, addr, size, _):
        if addr not in stubs:
            return
        n = stubs[addr]
        a = [uc.reg_read(r) for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2, UC_MIPS_REG_A3)]
        sp = uc.reg_read(UC_MIPS_REG_SP)
        v0 = 0
        if n == 'time':
            v0 = start_epoch
        elif n == 'localtime_r':
            t = struct.unpack('<i', uc.mem_read(a[0], 4))[0]
            tm = _t.gmtime(t)
            uc.mem_write(a[1], struct.pack('<9i', tm.tm_sec, tm.tm_min, tm.tm_hour, tm.tm_mday,
                                           tm.tm_mon - 1, tm.tm_year - 1900, 0, 0, 0))
            v0 = a[1]
        elif n == 'get_key':
            v0 = st['keys'].pop(0) if st['keys'] else 0xfb      # bail out if script runs dry
        elif n == 'sprintf':
            fmt = rd_str(a[1])
            nargs = fmt.count('%') - 2 * fmt.count('%%')
            args = a[2:] + [struct.unpack('<I', uc.mem_read(sp + 0x10 + 4 * i, 4))[0] for i in range(max(0, nargs - 2))]
            s = c_sprintf(fmt, args)
            uc.mem_write(a[0], s.encode() + b'\0')
            v0 = len(s)
        elif n == 'draw_text':
            align = struct.unpack('<I', uc.mem_read(sp + 0x10, 4))[0]
            st['texts'].append((rd_str(a[1]), a[2], a[3], align))
        elif n == 'display_update':
            st['frames'].append(st['texts']); st['texts'] = []
        elif n == 'system':
            st['cmd'] = rd_str(a[0]); v0 = st['sysret']
        elif n == 'GetIndexData':
            st['sprites'].append(a[1]); v0 = 0x20000000
        elif n == 'ShowSettingFace':
            st['title'] = (a[0], a[1])
        uc.reg_write(UC_MIPS_REG_V0, v0)

    def intr(uc, intno, _):
        v0 = uc.reg_read(UC_MIPS_REG_V0)
        a0 = uc.reg_read(UC_MIPS_REG_A0)
        assert v0 == 4079, v0
        st['tv'] = struct.unpack('<ii', uc.mem_read(a0, 8))
        uc.reg_write(UC_MIPS_REG_A3, 0); uc.reg_write(UC_MIPS_REG_V0, 0)

    uc.hook_add(UC_HOOK_CODE, hook)
    uc.hook_add(UC_HOOK_INTR, intr)
    # emulate SysSet_Fun's jump: fetch entry 26 from the real table
    off = struct.unpack('<i', uc.mem_read(JT26, 4))[0]
    target = (off + GP) & 0xffffffff
    # SysSet_Fun context: s0 = key, a frame with 0x160 bytes; returning lands at 0x499a54/68
    sp = STACK - 0x1000
    uc.reg_write(UC_MIPS_REG_SP, sp)
    uc.reg_write(UC_MIPS_REG_GP, GP)
    uc.reg_write(UC_MIPS_REG_S0, entry_key)
    ends = {0x499a54: 'redraw', 0x499a68: 'return'}
    exit_at = {}

    def stop(uc, addr, size, _):
        if addr in ends:
            exit_at['where'] = ends[addr]; exit_at['v0'] = uc.reg_read(UC_MIPS_REG_V0)
            uc.emu_stop()
    for a in ends:
        uc.hook_add(UC_HOOK_CODE, stop, begin=a, end=a)
    uc.emu_start(target, 0, count=2_000_000)
    return target, st, exit_at


def show(title, res):
    target, st, ex = res
    print(f'== {title}: entry {target:#x} exit={ex}')
    print('   title/face:', st['title'], 'sprites:', sorted(set(st['sprites'])))
    for f in st['frames'][:1] + st['frames'][-2:]:
        print('   frame:', [t[0] for t in f], [(t[1], t[2], t[3]) for t in f][:1])
    if st['tv']:
        print('   settimeofday:', st['tv'], _t.strftime('%Y-%m-%d %H:%M:%S', _t.gmtime(st['tv'][0])))
    print('   system:', st['cmd'])


if __name__ == '__main__':
    UP, DOWN, BACK, OK, HOME = 1, 2, 3, 4, 0xfb
    t0 = calendar.timegm((2010, 10, 1, 12, 0, 5))          # fresh-boot clock
    # 1) set 2026-11-18 20:43 from the 2010 boot clock: year 2020 -> +6 ; month 10 -> +1 ; day 1 -> +17 ;
    #    hour 12 -> +8 ; minute 0 -> +43 ; OK each, OK saves
    seq = [DOWN] * 6 + [OK] + [DOWN] + [OK] + [DOWN] * 17 + [OK] + [DOWN] * 8 + [OK] + [DOWN] * 43 + [OK]
    r = run(t0, seq); show('set 2026-11-18 20:43', r)
    want = calendar.timegm((2026, 11, 18, 20, 43, 0))
    assert r[1]['tv'][0] == want, (r[1]['tv'], want)
    assert r[2]['where'] == 'redraw' and r[2]['v0'] == 0
    assert r[1]['cmd'].startswith(f'echo {want - t0} > /tmp/.clockset;') and f'echo {want} > /mnt/mmc' in r[1]['cmd'], r[1]['cmd']
    # 2) wrap: minute 59 +1 -> 0, month 1 -1 -> 12, day clamp 31 Mar -> Feb 2028 (leap) = 29
    t1 = calendar.timegm((2028, 3, 31, 23, 59, 0))
    r = run(t1, [OK, UP, UP, OK, OK, OK, DOWN, OK])        # month 3->1, day stays clamp?, minute 59->0
    show('wrap/clamp', r)
    got = _t.gmtime(r[1]['tv'][0])
    assert (got.tm_year, got.tm_mon, got.tm_mday, got.tm_hour, got.tm_min) == (2028, 1, 29, 23, 0), got  # day clamped passing Feb; set after month anyway
    r = run(t1, [OK, UP, OK, OK, OK, OK])                   # March 31 -> Feb => 29 (2028 leap)
    got = _t.gmtime(r[1]['tv'][0]); show('feb clamp leap', r)
    assert (got.tm_mon, got.tm_mday) == (2, 29), got
    r = run(calendar.timegm((2027, 3, 31, 1, 2, 0)), [OK, UP, OK, OK, OK, OK])
    got = _t.gmtime(r[1]['tv'][0]); assert (got.tm_mon, got.tm_mday) == (2, 28), got
    r = run(calendar.timegm((2037, 6, 1, 0, 0, 0)), [DOWN, OK, OK, OK, OK, OK])   # year wraps 2037 -> 2020
    got = _t.gmtime(r[1]['tv'][0]); assert got.tm_year == 2020, got
    r = run(calendar.timegm((2025, 1, 1, 0, 0, 0)), [UP, OK, UP, OK, UP, OK, UP, OK, UP, OK])
    got = _t.gmtime(r[1]['tv'][0]); show('all down-wraps', r)
    assert (got.tm_year, got.tm_mon, got.tm_mday, got.tm_hour, got.tm_min) == (2024, 12, 31, 23, 59), got
    # 3) cancel with Back on first field: no clock change, back to list
    r = run(t0, [DOWN, BACK]); show('cancel', r)
    assert r[1]['tv'] is None and r[2] == {'where': 'redraw', 'v0': 0}
    # 4) back moves to previous field; home key propagates 0xfb
    r = run(t0, [OK, OK, BACK, HOME]); show('home', r)
    assert r[2] == {'where': 'return', 'v0': 0xfb} and r[1]['tv'] is None
    # 5) non-OK key into entry 26 must not open the screen
    r = run(t0, [], entry_key=2); assert r[2]['where'] == 'redraw' and not r[1]['frames']
    # 6) bracket markers follow the field
    r = run(calendar.timegm((2026, 11, 18, 20, 43, 0)), [OK, OK, OK, OK, HOME])
    fr = [[t[0] for t in f] for f in r[1]['frames']]
    print('   field frames:', fr)
    assert fr[0][:2] == ['[2026]-11-18', '20:43'] and fr[-1][:2] == ['2026-11-18', '20:[43]']
    assert fr[-1][2].endswith('OK: save') and fr[0][2].endswith('OK: next')
    # 7) fast wheel burst (e.g. 128 queued codes) redraws once, not 128 times
    r = run(t0, [DOWN] * 128 + [0, HOME], burst=True)
    print('   frames after 128-key burst:', len(r[1]['frames']))
    assert len(r[1]['frames']) == 2
    print('ALL OK')
