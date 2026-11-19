#!/usr/bin/env python3
"""Find every reference to candidate code-cave ranges in fifo-audio-app.
usage: refscan.py BIN ADDR:SIZE [ADDR:SIZE ...]
Checks direct branches/jumps, $gp-relative GOT loads (+ addiu), lui/ori materialisations,
and data words in every allocated non-.text, non-.eh_frame section."""
import sys,struct,re
from capstone import *
from elftools.elf.elffile import ELFFile
B=sys.argv[1]; data=open(B,'rb').read(); e=ELFFile(open(B,'rb'))
GP=0x5d9530
segs=[(s['p_vaddr'],s['p_offset'],s['p_filesz']) for s in e.iter_segments() if s['p_type']=='PT_LOAD']
def rd(va):
  for v,o,sz in segs:
    if v<=va<v+sz: return struct.unpack_from('<I',data,o+va-v)[0]
t=e.get_section_by_name('.text'); T0=t['sh_addr']
cands=[(int(a,16),int(s)) for a,s in (x.split(':') for x in sys.argv[2:])]
md=Cs(CS_ARCH_MIPS,CS_MODE_MIPS32|CS_MODE_LITTLE_ENDIAN); md.skipdata=True
regs={}; hits={c:[] for c,_ in cands}
def chk(val,addr):
  for c,sz in cands:
    if c<=val<c+sz and not (c<=addr<c+sz): hits[c].append(hex(addr))
for i in md.disasm(data[t['sh_offset']:t['sh_offset']+t['sh_size']],T0):
  ops=i.op_str
  m=re.match(r'\$(\w+), (-?0x[0-9a-f]+|-?\d+)\(\$gp\)$',ops)
  if i.mnemonic=='lw' and m:
    v=rd(GP+int(m.group(2),0)); regs[m.group(1)]=v
    if v is not None: chk(v,i.address)
    continue
  m=re.match(r'\$(\w+), \$(\w+), (-?0x[0-9a-f]+|-?\d+)$',ops)
  if i.mnemonic=='addiu' and m and m.group(2) in regs and regs[m.group(2)] is not None:
    v=(regs[m.group(2)]+int(m.group(3),0))&0xffffffff; chk(v,i.address); regs[m.group(1)]=v; continue
  m=re.match(r'\$(\w+), (0x[0-9a-f]+|\d+)$',ops)
  if i.mnemonic=='lui' and m: regs[m.group(1)]=int(m.group(2),0)<<16; continue
  m=re.match(r'\$(\w+), \$(\w+), (0x[0-9a-f]+|\d+)$',ops)
  if i.mnemonic=='ori' and m and m.group(2) in regs and regs[m.group(2)] is not None:
    v=regs[m.group(2)]|int(m.group(3),0); chk(v,i.address); regs[m.group(1)]=v; continue
  if i.mnemonic in('jal','j','bal','b','beq','bne','beqz','bnez','blez','bgtz','bltz','bgez'):
    try: chk(int(ops.split(', ')[-1],0),i.address)
    except: pass
# data words in all non-text non-ehframe sections
for sec in e.iter_sections():
  if sec['sh_flags']&2 and sec.name not in ('.text','.eh_frame') and sec['sh_type']!='SHT_NOBITS':
    d=sec.data()
    for k in range(0,len(d)-3,4):
      chk(struct.unpack_from('<I',d,k)[0], sec['sh_addr']+k)
for c,_ in cands: print(hex(c), len(hits[c]), hits[c][:8])
