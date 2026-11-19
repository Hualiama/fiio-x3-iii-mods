#!/usr/bin/env python3
"""Off-device tests for the Album/Artist "Random" row patch (build_random.py).
usage: emu_random.py PATCHED_APP
Runs the patched wrappers/caves in Unicorn with the stock bodies replaced by Python models."""
import sys, struct, collections
from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_HOOK_CODE
from unicorn.mips_const import *
from elftools.elf.elffile import ELFFile

APP = sys.argv[1]
GP = 0x5d9530
STUB, HEAP, STACK = 0x10000000, 0x30000000, 0x7f000000
LASTA, LASTS, RNG = 0x5d86a0, 0x5d86a4, 0x5d86a8
RET = 0x0ff00000                     # sentinel return address

elf = ELFFile(open(APP, 'rb'))
raw = open(APP, 'rb').read()
SYM = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols() if s['st_value']}
dtags = {t.entry.d_tag: t.entry.d_val for t in elf.get_section_by_name('.dynamic').iter_tags()}


class Emu:
    def __init__(self):
        uc = self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        for s in elf.iter_segments():
            if s['p_type'] != 'PT_LOAD':
                continue
            lo = s['p_vaddr'] & ~0xfff
            hi = (s['p_vaddr'] + s['p_memsz'] + 0xfff) & ~0xfff
            uc.mem_map(lo, hi - lo)
            uc.mem_write(s['p_vaddr'], raw[s['p_offset']: s['p_offset'] + s['p_filesz']])
        uc.mem_map(STUB, 0x10000)
        uc.mem_map(HEAP, 0x400000)
        uc.mem_map(STACK - 0x100000, 0x100000)
        uc.mem_map(RET, 0x1000)
        self.brk = HEAP
        self.calls = collections.Counter()
        self.gid_args = []
        self.tick = 1000
        self.txt405 = True
        self.stubs = {}
        for i, (name, off) in enumerate([('malloc', -0x71bc), ('strlen', -0x75a0), ('strcpy', -0x7f54),
                                         ('GetIndexData', -0x6e34), ('mtimer', -0x6b74)]):
            a = STUB + 16 * i
            self.w32(GP + off, a)
            uc.mem_write(a, struct.pack('<II', 0x03e00008, 0))
            self.stubs[a] = name
        self.bodies = {}
        uc.hook_add(UC_HOOK_CODE, self.hook)
        # string resources
        self.s_random = self.cstr(b'Random')
        self.txt_entry = self.alloc(16)
        self.w32(self.txt_entry + 4, self.s_random)
        self.pic_entry = self.alloc(16)

    # --- memory helpers
    def r32(self, a): return struct.unpack('<I', self.uc.mem_read(a, 4))[0]
    def s32(self, a): return struct.unpack('<i', self.uc.mem_read(a, 4))[0]
    def w32(self, a, v): self.uc.mem_write(a, struct.pack('<I', v & 0xffffffff))
    def alloc(self, n):
        a = self.brk; self.brk += (n + 15) & ~15
        self.uc.mem_write(a, b'\0' * n); return a
    def cstr(self, b):
        a = self.alloc(len(b) + 1); self.uc.mem_write(a, b + b'\0'); return a
    def rstr(self, a): return bytes(self.uc.mem_read(a, 64)).split(b'\0')[0].decode()
    def gvar(self, off): return self.r32(GP + off)          # address of a global via its GOT slot

    # --- std::deque<MPlayList*> (128 ptrs/node): map,map_size,start{cur,first,last,node},finish{...}
    def deque_build(self, d, ptrs, front_room=True):
        k = 5 if front_room else 0           # front_room: start.cur > start.first (fast push_front)
        nn = (k + len(ptrs)) // 128 + 1
        mp = self.alloc(4 * (nn + 8))
        nodes = [self.alloc(512) for _ in range(nn)]
        for i, n in enumerate(nodes):
            self.w32(mp + 4 * (4 + i), n)
        for i, p in enumerate(ptrs):
            j = k + i
            self.w32(nodes[j // 128] + 4 * (j % 128), p)
        j = k + len(ptrs)
        fnode, fcur = j // 128, nodes[j // 128] + 4 * (j % 128)
        self.w32(d + 0, mp); self.w32(d + 4, nn + 8)
        for off, v in ((8, nodes[0] + 4 * k), (0xc, nodes[0]), (0x10, nodes[0] + 512), (0x14, mp + 16),
                       (0x18, fcur), (0x1c, nodes[fnode]), (0x20, nodes[fnode] + 512), (0x24, mp + 16 + 4 * fnode)):
            self.w32(d + off, v)
    def deque_list(self, d):
        cur, last, node = self.r32(d + 8), self.r32(d + 0x10), self.r32(d + 0x14)
        fcur, fnode = self.r32(d + 0x18), self.r32(d + 0x24)
        out = []
        while True:
            end = fcur if node == fnode else last
            while cur != end:
                out.append(self.r32(cur)); cur += 4
            if node == fnode:
                return out
            node += 4; cur = self.r32(node); last = cur + 512
    def entry(self, ident, name, typ=4):
        e = self.alloc(0x3c)
        self.w32(e, ident); self.w32(e + 4, self.cstr(name.encode())); self.w32(e + 0x1c, typ)
        return e

    # --- hooks
    def hook(self, uc, addr, size, _):
        if addr in self.stubs:
            n = self.stubs[addr]; self.calls[n] += 1
            a0, a1 = uc.reg_read(UC_MIPS_REG_A0), uc.reg_read(UC_MIPS_REG_A1)
            v0 = 0
            if n == 'malloc':
                v0 = self.alloc(a0)
            elif n == 'strlen':
                v0 = len(self.rstr(a0).encode())
            elif n == 'strcpy':
                uc.mem_write(a0, self.rstr(a1).encode() + b'\0'); v0 = a0
            elif n == 'GetIndexData':
                self.gid_args.append((a0, a1))
                if a0 == self.gvar(-0x7328):
                    v0 = self.txt_entry if (a1 == 405 and self.txt405) else 0
                else:
                    v0 = self.pic_entry
            elif n == 'mtimer':
                v0 = self.tick
            uc.reg_write(UC_MIPS_REG_V0, v0)
        elif addr in self.bodies:
            self.calls[hex(addr)] += 1
            v0 = self.bodies[addr]() or 0
            uc.reg_write(UC_MIPS_REG_V0, v0 & 0xffffffff)
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))
        elif addr == 0x4515b8:                    # _M_push_front_aux(this, &x)
            self.calls['push_front_aux'] += 1
            d, px = uc.reg_read(UC_MIPS_REG_A0), uc.reg_read(UC_MIPS_REG_A1)
            node = self.r32(d + 0x14) - 4
            nb = self.alloc(512); self.w32(node, nb)
            self.w32(d + 0x14, node); self.w32(d + 0xc, nb); self.w32(d + 0x10, nb + 512)
            self.w32(d + 8, nb + 508); self.w32(nb + 508, self.r32(px))
            uc.reg_write(UC_MIPS_REG_PC, uc.reg_read(UC_MIPS_REG_RA))

    def call(self, fn, max_insn=200000):
        uc = self.uc
        uc.reg_write(UC_MIPS_REG_SP, STACK - 0x2000)
        uc.reg_write(UC_MIPS_REG_RA, RET)
        uc.reg_write(UC_MIPS_REG_T9, fn)
        uc.reg_write(UC_MIPS_REG_GP, 0x12345678)       # wrappers must not trust the caller's gp
        for r in (UC_MIPS_REG_S0, UC_MIPS_REG_S1, UC_MIPS_REG_S2):
            uc.reg_write(r, 0xabc00000 + r)
        uc.emu_start(fn, RET, count=max_insn)
        assert uc.reg_read(UC_MIPS_REG_PC) == RET, hex(uc.reg_read(UC_MIPS_REG_PC))
        assert uc.reg_read(UC_MIPS_REG_SP) == STACK - 0x2000
        for r in (UC_MIPS_REG_S0, UC_MIPS_REG_S1, UC_MIPS_REG_S2):
            assert uc.reg_read(r) == 0xabc00000 + r, 'callee-saved reg clobbered'
        return uc.reg_read(UC_MIPS_REG_V0)


V = dict(cRoot=-0x7b2c, layer=-0x78b4, cStart=-0x70a4, cSele=-0x6b7c, deq_Dir=-0x6ab0,
         style=-0x72b4, singer=-0x7870, otheridx=-0x7c78, singeridx=-0x79a4)
LOT, EC, T1, S1 = SYM['_Z13LoadOtherTypev'], SYM['_Z10EnterClassv'], SYM['_Z16LoadMusicByType1v'], SYM['_Z18LoadAlbumBySonger1v']


def lot_case(root, n, front_room=True, txt=True, preload_fake=False):
    e = Emu(); e.txt405 = txt
    d = e.gvar(V['deq_Dir'])
    ents = [e.entry(i, 'Album %d' % i) for i in range(max(n, 0))]
    if preload_fake:
        ents = [e.entry(-2, 'Random')] + ents
    e.w32(e.gvar(V['cRoot']), root)

    def body():
        e.deque_build(d, ents, front_room)
        return n if n >= 0 else -1
    e.bodies[LOT + 12] = body
    ret = e.call(LOT)
    items = e.deque_list(d)
    return e, ret if ret < 1 << 31 else ret - (1 << 32), items


def test_load_other_type():
    for room in (True, False):
        for root in (1, 2):
            e, ret, items = lot_case(root, 5, room)
            assert ret == 6 and len(items) == 6, (ret, len(items))
            f = items[0]
            assert e.s32(f) == -2 and e.r32(f + 0x1c) == 4 and e.rstr(e.r32(f + 4)) == 'Random'
            assert all(e.r32(f + o) == 0 for o in range(8, 0x1c, 4)) and e.r32(f + 0x38) == 0
            assert [e.s32(x) for x in items[1:]] == [0, 1, 2, 3, 4]
            assert e.calls['push_front_aux'] == (0 if room else 1)
            assert e.r32(e.r32(f + 4)) != e.r32(e.s_random) or e.r32(f + 4) != e.s_random   # own copy (freed later)
    for root in (0, 3, 4, 5, 6):
        e, ret, items = lot_case(root, 5)
        assert ret == 5 and len(items) == 5 and e.calls['malloc'] == 0, root
    for n in (0, -1):
        e, ret, items = lot_case(1, n)
        assert ret == n and len(items) == 0
    e, ret, items = lot_case(1, 3, txt=False)                 # string 405 missing -> built-in "Random"
    assert ret == 4 and e.rstr(e.r32(items[0] + 4)) == 'Random'
    e, ret, items = lot_case(2, 3, preload_fake=True)          # never two Random rows
    assert ret == 3 and len(items) == 4 and sum(e.s32(x) == -2 for x in items) == 1
    e, ret, items = lot_case(1, 1)                             # only "Unknown album" -> still gets Random
    assert ret == 2
    print('LoadOtherType wrapper: OK')


def ec_case(n, layer=1, root=1, start=0, sele=0, fake=True, tick=1000, seed=0, other=7, singer=9):
    e = Emu(); e.tick = tick
    e.w32(RNG, seed)
    d = e.gvar(V['deq_Dir'])
    ents = ([e.entry(-2, 'Random')] if fake else []) + [e.entry(i, 'A%d' % i) for i in range(n - (1 if fake else 0))]
    e.deque_build(d, ents)
    for k, v in (('layer', layer), ('cRoot', root), ('cStart', start), ('cSele', sele),
                 ('otheridx', other), ('singeridx', singer)):
        e.w32(e.gvar(V[k]), v)
    seen = {}

    def body():
        seen['pos'] = (e.r32(e.gvar(V['cStart'])), e.r32(e.gvar(V['cSele'])))
        return 1
    e.bodies[EC + 12] = body
    ret = e.call(EC)
    assert ret == 1 and e.calls[hex(EC + 12)] == 1
    assert (e.r32(LASTA), e.r32(LASTS)) == (other, singer)
    return e, seen['pos'], e.r32(RNG)


def test_enter_class():
    for n in (2, 3, 7, 8, 9, 30, 500):
        hits = collections.Counter()
        seed = 0
        for k in range(1500 if n <= 30 else 300):
            e, (cs, sl), seed = ec_case(n, tick=1000 + 37 * k, seed=seed, root=1 + (k & 1))
            idx = cs + sl
            assert 1 <= idx <= n - 1, (n, cs, sl)
            assert 0 <= sl <= 6 and 0 <= cs <= max(0, n - 7), (n, cs, sl)
            if n > 7:
                assert cs == min(idx, n - 7)
            else:
                assert cs == 0
            hits[idx] += 1
        if n <= 30:
            assert set(hits) == set(range(1, n)), (n, sorted(hits))
        print('  n=%-3d  picks spread over %d/%d rows (min %d, max %d hits)' % (n, len(hits), n - 1, min(hits.values()), max(hits.values())))
    # same ms tick, consecutive presses still differ (LCG state carries over)
    picks = []
    seed = 0
    for _ in range(20):
        e, (cs, sl), seed = ec_case(50, tick=5000, seed=seed); picks.append(cs + sl)
    assert len(set(picks)) > 10, picks
    # untouched cases
    for kw in (dict(layer=0), dict(layer=2), dict(root=3), dict(root=0), dict(sele=3), dict(start=1),
               dict(fake=False), dict(n=1)):
        args = dict(n=10); args.update(kw)
        e, pos, _ = ec_case(**args)
        assert pos == (args.get('start', 0), args.get('sele', 0)), (kw, pos)
    print('EnterClass wrapper: OK')


def jump_case(fn, n, idx, last, fake=True):
    e = Emu()
    lst, var, lastv = (V['style'], V['otheridx'], LASTA) if fn == T1 else (V['singer'], V['singeridx'], LASTS)
    d = e.gvar(lst)
    ents = ([e.entry(-2, 'Random')] if fake else []) + [e.entry(i, 'X%d' % i) for i in range(n - (1 if fake else 0))]
    e.deque_build(d, ents)
    e.w32(e.gvar(var), idx); e.w32(lastv, last)
    seen = {}

    def body():
        seen['idx'] = e.r32(e.gvar(var)); return 3
    e.bodies[fn + 12] = body
    assert e.call(fn) == 3
    assert e.r32(lastv) == seen['idx']
    return seen['idx']


def test_jump_list():
    for fn, nm in ((T1, 'album'), (S1, 'artist')):
        assert jump_case(fn, 10, 0, 5) == 1        # forward wrap past the last item -> first real
        assert jump_case(fn, 10, 0, 9) == 1
        assert jump_case(fn, 10, 0, 1) == 9        # stepping back from the first real -> last
        assert jump_case(fn, 2, 0, 1) == 1         # single real item either way
        assert jump_case(fn, 2, 0, 0) == 1
        for i in (1, 4, 9):
            assert jump_case(fn, 10, i, 3) == i    # normal steps untouched
        assert jump_case(fn, 10, 0, 1, fake=False) == 0   # list without Random row (genre / artist's albums)
        assert jump_case(fn, 0, 0, 1, fake=False) == 0    # empty list
        print('Jump List guard (%s): OK' % nm)


def cave_case(entry_site, ident, typ):
    e = Emu()
    uc = e.uc
    sp = STACK - 0x3000
    uc.reg_write(UC_MIPS_REG_SP, sp); e.w32(sp + 0x20, GP)
    uc.reg_write(UC_MIPS_REG_GP, GP)
    ent = e.entry(ident, 'Random' if ident == -2 else 'Some Album', typ)
    uc.reg_write(UC_MIPS_REG_V1, ent)
    uc.reg_write(UC_MIPS_REG_V0, 0x77)
    pic = e.gvar(-0x737c)
    uc.reg_write(UC_MIPS_REG_S7, pic)
    for r in (UC_MIPS_REG_S1, UC_MIPS_REG_S2, UC_MIPS_REG_S3, UC_MIPS_REG_S5):
        uc.reg_write(r, 0xdead0000 + r)
    ends = {0x4a0f3c, 0x4a1094, 0x4a1510, 0x4a196c}
    out = {}

    def stop(uc, addr, size, _):
        if addr in ends:
            out['at'] = addr; uc.emu_stop()
    for a in ends:
        uc.hook_add(UC_HOOK_CODE, stop, begin=a, end=a)
    e.w32(pic, 0x1111)
    uc.emu_start(entry_site, 0, count=1000)
    regs = {k: uc.reg_read(r) for k, r in (('v0', UC_MIPS_REG_V0), ('v1', UC_MIPS_REG_V1), ('s1', UC_MIPS_REG_S1),
                                         ('s2', UC_MIPS_REG_S2), ('s3', UC_MIPS_REG_S3), ('s5', UC_MIPS_REG_S5),
                                         ('gp', UC_MIPS_REG_GP))}
    return out.get('at'), regs, e.r32(pic), [a for a in e.gid_args], e


def test_caves():
    # normal rows
    at, r, pic, gid, e = cave_case(0x4a0f34, 3, 4)
    assert at == 0x4a0f3c and r['v0'] == 4 and r['v1'] == 4 and pic == 0x1111 and not gid
    at, r, pic, gid, e = cave_case(0x4a0f34, 3, 8)
    assert at == 0x4a0f3c and r['v1'] == 8
    at, r, pic, gid, e = cave_case(0x4a0f34, -1, 4)                  # "Unknown album" keeps its icon
    assert at == 0x4a0f3c
    at, r, pic, gid, e = cave_case(0x4a0f34, -2, 4)
    assert at == 0x4a1094 and pic == e.pic_entry and gid == [(e.gvar(-0x7cb8), 0x9f)]
    assert r['s1'] == e.gvar(V['cStart']) and r['gp'] == GP
    # highlighted row
    at, r, pic, gid, e = cave_case(0x4a1508, 3, 4)
    assert at == 0x4a1510 and r['v0'] == 4 and r['s1'] == 4 and not gid
    at, r, pic, gid, e = cave_case(0x4a1508, -2, 4)
    assert at == 0x4a196c and pic == e.pic_entry and gid == [(e.gvar(-0x7cb8), 0x9f)]
    assert (r['s1'], r['s5'], r['s3'], r['s2']) == (4, e.gvar(-0x737c), e.gvar(V['cSele']), e.gvar(V['cStart']))
    print('ShowAllMusic icon caves: OK')


if __name__ == '__main__':
    test_load_other_type()
    test_enter_class()
    test_jump_list()
    test_caves()
    print('ALL OK')
