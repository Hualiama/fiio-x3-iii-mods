#!/usr/bin/env python3
"""Annotated MIPS disassembler for fifo-audio-app.
usage: mipsdis.py BIN (ADDR|SYMBOL) [NBYTES]
Annotates $gp-relative GOT loads with symbol names / strings."""
import sys, struct, re
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
from elftools.elf.elffile import ELFFile

GP = 0x5d9530
BIN = sys.argv[1]
data = open(BIN, 'rb').read()
elf = ELFFile(open(BIN, 'rb'))
segs = [(s['p_vaddr'], s['p_offset'], s['p_filesz']) for s in elf.iter_segments() if s['p_type'] == 'PT_LOAD']
byaddr, byname = {}, {}
for sec in ('.symtab', '.dynsym'):
    s = elf.get_section_by_name(sec)
    if not s:
        continue
    for x in s.iter_symbols():
        if x['st_value'] and x.name:
            byaddr.setdefault(x['st_value'], x.name)
            byname.setdefault(x.name, (x['st_value'], x['st_size']))
# GOT: map got slot -> dynsym name for externals
got = elf.get_section_by_name('.got')
gotaddr = got['sh_addr']
dyn = elf.get_section_by_name('.dynsym')
dynsyms = list(dyn.iter_symbols())
# MIPS: local_gotno and gotsym from .dynamic
local_gotno = gotsym = None
for t in elf.get_section_by_name('.dynamic').iter_tags():
    if t.entry.d_tag == 'DT_MIPS_LOCAL_GOTNO':
        local_gotno = t.entry.d_val
    if t.entry.d_tag == 'DT_MIPS_GOTSYM':
        gotsym = t.entry.d_val


def rd32(va):
    for v, o, sz in segs:
        if v <= va < v + sz:
            return struct.unpack_from('<I', data, o + va - v)[0]
    return None


def cstr(va, n=60):
    for v, o, sz in segs:
        if v <= va < v + sz:
            b = data[o + va - v: o + va - v + n].split(b'\0', 1)[0]
            try:
                t = b.decode('utf-8')
                if len(t) >= 2 and all(c.isprintable() for c in t):
                    return t
            except Exception:
                pass
    return None


def got_name(slot_addr):
    idx = (slot_addr - gotaddr) // 4
    if gotsym is not None and idx >= local_gotno:
        k = gotsym + idx - local_gotno
        if k < len(dynsyms):
            return dynsyms[k].name
    val = rd32(slot_addr)
    if val is None:
        return None
    return byaddr.get(val, '=' + hex(val))


def name2addr(s):
    if s.startswith('0x'):
        return int(s, 16), None
    for k, v in byname.items():
        if k == s or s in k:
            return v
    raise SystemExit('no sym ' + s)


def main():
    a, sz = name2addr(sys.argv[2])
    n = int(sys.argv[3], 0) if len(sys.argv) > 3 else (sz or 0x100)
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    md.skipdata = True
    off = a - 0x400000
    regs = {}
    for i in md.disasm(data[off: off + n], a):
        ann = ''
        m = re.match(r'\$(\w+), (-?0x[0-9a-f]+|-?\d+)\(\$gp\)', i.op_str)
        if i.mnemonic == 'lw' and m:
            slot = GP + int(m.group(2), 0)
            nm = got_name(slot)
            regs[m.group(1)] = rd32(slot)
            ann = f'  ; GOT[{hex(slot)}] {nm}'
        m2 = re.match(r'\$(\w+), \$(\w+), (-?0x[0-9a-f]+|-?\d+)$', i.op_str)
        if i.mnemonic == 'addiu' and m2 and m2.group(2) in regs and regs[m2.group(2)]:
            t = (regs[m2.group(2)] + int(m2.group(3), 0)) & 0xffffffff
            s = cstr(t)
            ann = f'  ; ={hex(t)}' + (f' "{s}"' if s else '') + (f' <{byaddr[t]}>' if t in byaddr else '')
        if i.mnemonic in ('bal', 'jal', 'j') :
            t = int(i.op_str, 0)
            if t in byaddr:
                ann = '  ; ' + byaddr[t]
        lab = ('\n<' + byaddr[i.address] + '>:\n') if i.address in byaddr and i.address != a else ''
        print(f'{lab}{i.address:x}: {i.mnemonic:8s} {i.op_str}{ann}')


main()
