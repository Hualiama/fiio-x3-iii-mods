#!/usr/bin/env python3
"""Add a "Random" row to the top of the Album and Artist lists (FiiO X3 III, FW1.2.4-based app).

usage: build_random.py APP_IN APP_OUT

Design (all new code lives in the dead function class_lesssecond @0x49cf24, 2884 B).
Each hooked function's entry (its 3-insn $gp setup) is replaced by `j wrapper; nop`; the wrapper
sets $gp itself and calls the original body at entry+12.  (GOT redirection is NOT used: these are
global GOT entries that ld.so may rewrite.)
  * LoadOtherType      -> wLOT : after the stock loader, push_front a fake MPlayList
                                     (id=-2, type=4, name=TxtList[405]) when cRoot is 1 (Album) / 2 (Artist).
  * EnterClass         -> wEC  : at layer 1 of Album/Artist, if the selected row is the fake one,
                                     move the cursor to a random real row, then run the stock EnterClass
                                     (Album -> opens that album's track list, Artist -> opens that artist).
  * LoadMusicByType1   -> wT1  : "Jump List" album-to-album continuation never lands on the fake row.
  * LoadAlbumBySonger1 -> wS1  : same for artist-to-artist continuation.
  * ShowAllMusic 0x4a0f34 / 0x4a1508 -> caves A/B: draw PicList[159] (shuffle icon) for the fake row.
Scratch RAM (zeroed .bss tail): 0x5d86a0 LASTA, 0x5d86a4 LASTS, 0x5d86a8 RNG.
"""
import sys, struct
from keystone import Ks, KS_ARCH_MIPS, KS_MODE_MIPS32, KS_MODE_LITTLE_ENDIAN
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
from elftools.elf.elffile import ELFFile

GP = 0x5d9530
CAVE, CAVE_SZ = 0x49cf24, 2884
TXT_ID, PIC_ID = 405, 0x9f

# GOT offsets (from $gp) and the value each must hold in the input binary
GOT = {
    'LoadOtherType': (-0x7c04, 0x4a04c0),
    'EnterClass': (-0x72fc, 0x4af8fc),
    'LoadMusicByType1': (-0x6f34, 0x4a426c),
    'LoadAlbumBySonger1': (-0x6ff4, 0x4a7dfc),
}
SYMS = {  # GOT slots we read, checked against symbol names
    -0x7b2c: 'cRoot_pos', -0x78b4: 'c_nLayer', -0x70a4: 'cStart', -0x6b7c: 'cSele',
    -0x6ab0: 'deq_Dir', -0x72b4: 'deq_style_album', -0x7870: 'deq_singer',
    -0x7c78: 'otherindex', -0x79a4: 'singerindex', -0x71bc: 'malloc', -0x75a0: 'strlen',
    -0x7f54: 'strcpy', -0x7328: 'TxtList', -0x7cb8: 'PicList', -0x737c: 'Pic',
    -0x6e34: '_ZN14profile_module12GetIndexDataEi', -0x6aa8: '_ZNSt5dequeIP9MPlayListSaIS1_EE2atEj',
    -0x6b74: '_ZN12tools_module6mtimerEv', -0x6ca8: 'miTools',
}
PUSH_FRONT_AUX = 0x4515b8   # deque<MPlayList*>::_M_push_front_aux(const T&)
SET_GP = 'lui $gp, 0x5d\nori $gp, $gp, 0x9530\n'

ASM = f"""
# ---------------- shared: v0 = size of deque a0 -------------
dsize:
  lw $t0, 0x24($a0)
  lw $t1, 0x14($a0)
  subu $t0, $t0, $t1
  sra $t0, $t0, 2
  addiu $t0, $t0, -1
  sll $v0, $t0, 7
  lw $t0, 0x18($a0)
  lw $t1, 0x1c($a0)
  subu $t0, $t0, $t1
  sra $t0, $t0, 2
  addu $v0, $v0, $t0
  lw $t0, 0x10($a0)
  lw $t1, 0x8($a0)
  subu $t0, $t0, $t1
  sra $t0, $t0, 2
  addu $v0, $v0, $t0
  jr $ra

# ---------------- shared: v0 = 1 if deque a0 non-empty and a0[0] is the fake row -------------
isfake0:
  addiu $sp, $sp, -32
  sw $ra, 28($sp)
  sw $s0, 24($sp)
  move $s0, $a0
  bal dsize
  move $t2, $v0
  move $v0, $zero
  beqz $t2, isf_out
  lw $t0, 8($s0)
  lw $t0, 0($t0)
  lw $t0, 0($t0)
  addiu $t1, $zero, -2
  bne $t0, $t1, isf_out
  addiu $v0, $zero, 1
isf_out:
  lw $s0, 24($sp)
  lw $ra, 28($sp)
  addiu $sp, $sp, 32
  jr $ra

# ---------------- shared: v0 = pseudo-random 24-bit value (LCG mixed with ms timer) -------------
rnd:
  addiu $sp, $sp, -32
  sw $ra, 28($sp)
  {SET_GP}
  lw $t9, -0x6b74($gp)
  lw $a0, -0x6ca8($gp)
  jalr $t9
  {SET_GP}
  lui $t0, 0x5d
  ori $t0, $t0, 0x86a8
  lw $t1, 0($t0)
  lui $t2, 0x41c6
  ori $t2, $t2, 0x4e6d
  multu $t1, $t2
  mflo $t1
  addu $t1, $t1, $v0
  addiu $t1, $t1, 12345
  sw $t1, 0($t0)
  srl $v0, $t1, 8
  lw $ra, 28($sp)
  addiu $sp, $sp, 32
  jr $ra

# ---------------- wrapper: LoadOtherType -------------
wLOT:
  addiu $sp, $sp, -48
  sw $ra, 44($sp)
  sw $s0, 40($sp)
  sw $s1, 36($sp)
  sw $s2, 32($sp)
  {SET_GP}
  lui $t9, 0x4a
  ori $t9, $t9, 0x04cc
  jalr $t9
  {SET_GP}
  move $s0, $v0
  blez $s0, lot_out
  lw $t0, -0x7b2c($gp)
  lw $t0, 0($t0)
  addiu $t0, $t0, -1
  sltiu $t0, $t0, 2
  beqz $t0, lot_out
  lw $a0, -0x6ab0($gp)
  bal isfake0
  bnez $v0, lot_out
  lw $t9, -0x71bc($gp)
  addiu $a0, $zero, 0x3c
  jalr $t9
  {SET_GP}
  move $s1, $v0
  beqz $s1, lot_out
  addiu $t0, $zero, 0
lot_clr:
  addu $t1, $s1, $t0
  sw $zero, 0($t1)
  addiu $t0, $t0, 4
  slti $t1, $t0, 0x3c
  bnez $t1, lot_clr
  addiu $t0, $zero, -2
  sw $t0, 0($s1)
  addiu $t0, $zero, 4
  sw $t0, 0x1c($s1)
  lw $t9, -0x6e34($gp)
  lw $a0, -0x7328($gp)
  addiu $a1, $zero, {TXT_ID}
  jalr $t9
  {SET_GP}
  lui $s2, FBHI
  ori $s2, $s2, FBLO
  beqz $v0, lot_name
  lw $s2, 4($v0)
lot_name:
  lw $t9, -0x75a0($gp)
  move $a0, $s2
  jalr $t9
  {SET_GP}
  lw $t9, -0x71bc($gp)
  addiu $a0, $v0, 1
  jalr $t9
  {SET_GP}
  sw $v0, 4($s1)
  beqz $v0, lot_push
  lw $t9, -0x7f54($gp)
  move $a0, $v0
  move $a1, $s2
  jalr $t9
  {SET_GP}
lot_push:
  lw $a0, -0x6ab0($gp)
  lw $t0, 8($a0)
  lw $t1, 0xc($a0)
  beq $t0, $t1, lot_slow
  addiu $t0, $t0, -4
  sw $s1, 0($t0)
  sw $t0, 8($a0)
  b lot_inc
lot_slow:
  sw $s1, 16($sp)
  addiu $a1, $sp, 16
  lui $t9, 0x45
  ori $t9, $t9, 0x15b8
  jalr $t9
  {SET_GP}
lot_inc:
  addiu $s0, $s0, 1
lot_out:
  move $v0, $s0
  lw $s2, 32($sp)
  lw $s1, 36($sp)
  lw $s0, 40($sp)
  lw $ra, 44($sp)
  addiu $sp, $sp, 48
  jr $ra


# ---------------- wrapper: EnterClass -------------
wEC:
  addiu $sp, $sp, -48
  sw $ra, 44($sp)
  sw $s0, 40($sp)
  sw $s1, 36($sp)
  sw $s2, 32($sp)
  {SET_GP}
  lw $t0, -0x78b4($gp)
  lw $t0, 0($t0)
  addiu $t1, $zero, 1
  bne $t0, $t1, ec_call
  lw $t0, -0x7b2c($gp)
  lw $t0, 0($t0)
  addiu $t0, $t0, -1
  sltiu $t0, $t0, 2
  beqz $t0, ec_call
  lw $a0, -0x6ab0($gp)
  bal dsize
  move $s0, $v0
  sltiu $t0, $s0, 2
  bnez $t0, ec_call
  lw $t0, -0x70a4($gp)
  lw $s1, 0($t0)
  lw $t0, -0x6b7c($gp)
  lw $t0, 0($t0)
  addu $s1, $s1, $t0
  bnez $s1, ec_call
  lw $a0, -0x6ab0($gp)
  bal isfake0
  beqz $v0, ec_call
  bal rnd
  addiu $t0, $s0, -1
  divu $zero, $v0, $t0
  mfhi $t1
  addiu $s2, $t1, 1
  move $t3, $zero
  sltiu $t0, $s0, 8
  bnez $t0, ec_set
  addiu $t1, $s0, -7
  move $t3, $s2
  sltu $t2, $t1, $s2
  movn $t3, $t1, $t2
ec_set:
  subu $t4, $s2, $t3
  lw $t0, -0x70a4($gp)
  sw $t3, 0($t0)
  lw $t0, -0x6b7c($gp)
  sw $t4, 0($t0)
ec_call:
  lui $t9, 0x4a
  ori $t9, $t9, 0xf908
  jalr $t9
  {SET_GP}
  lui $t2, 0x5d
  ori $t2, $t2, 0x86a0
  lw $t0, -0x7c78($gp)
  lw $t0, 0($t0)
  sw $t0, 0($t2)
  lw $t0, -0x79a4($gp)
  lw $t0, 0($t0)
  sw $t0, 4($t2)
  lw $s2, 32($sp)
  lw $s1, 36($sp)
  lw $s0, 40($sp)
  lw $ra, 44($sp)
  addiu $sp, $sp, 48
  jr $ra

# ---------------- shared: never let index *a1 (into deque a0) rest on the fake row 0.
# a2 = &last index used.  last==1 -> we were stepping backwards -> wrap to n-1, else go to 1.
fixidx:
  addiu $sp, $sp, -40
  sw $ra, 36($sp)
  sw $s0, 32($sp)
  sw $s1, 28($sp)
  sw $s2, 24($sp)
  move $s0, $a0
  move $s1, $a1
  move $s2, $a2
  lw $t0, 0($s1)
  bnez $t0, fx_out
  move $a0, $s0
  bal isfake0
  beqz $v0, fx_out
  move $a0, $s0
  bal dsize
  sltiu $t0, $v0, 2
  bnez $t0, fx_out
  addiu $t1, $zero, 1
  lw $t0, 0($s2)
  addiu $t2, $v0, -1
  bne $t0, $t1, fx_fwd
  sw $t2, 0($s1)
  b fx_out
fx_fwd:
  sw $t1, 0($s1)
fx_out:
  lw $s2, 24($sp)
  lw $s1, 28($sp)
  lw $s0, 32($sp)
  lw $ra, 36($sp)
  addiu $sp, $sp, 40
  jr $ra

# ---------------- wrapper: LoadMusicByType1 (album-to-album Jump List) -------------
wT1:
  addiu $sp, $sp, -32
  sw $ra, 28($sp)
  sw $s0, 24($sp)
  {SET_GP}
  lw $a0, -0x72b4($gp)
  lw $a1, -0x7c78($gp)
  lui $a2, 0x5d
  ori $a2, $a2, 0x86a0
  bal fixidx
  {SET_GP}
  lui $t9, 0x4a
  ori $t9, $t9, 0x4278
  jalr $t9
  {SET_GP}
  move $s0, $v0
  lw $t0, -0x7c78($gp)
  lw $t0, 0($t0)
  lui $t2, 0x5d
  ori $t2, $t2, 0x86a0
  sw $t0, 0($t2)
  move $v0, $s0
  lw $s0, 24($sp)
  lw $ra, 28($sp)
  addiu $sp, $sp, 32
  jr $ra

# ---------------- wrapper: LoadAlbumBySonger1 (artist-to-artist Jump List) -------------
wS1:
  addiu $sp, $sp, -32
  sw $ra, 28($sp)
  sw $s0, 24($sp)
  {SET_GP}
  lw $a0, -0x7870($gp)
  lw $a1, -0x79a4($gp)
  lui $a2, 0x5d
  ori $a2, $a2, 0x86a4
  bal fixidx
  {SET_GP}
  lui $t9, 0x4a
  ori $t9, $t9, 0x7e08
  jalr $t9
  {SET_GP}
  move $s0, $v0
  lw $t0, -0x79a4($gp)
  lw $t0, 0($t0)
  lui $t2, 0x5d
  ori $t2, $t2, 0x86a4
  sw $t0, 0($t2)
  move $v0, $s0
  lw $s0, 24($sp)
  lw $ra, 28($sp)
  addiu $sp, $sp, 32
  jr $ra

# ---------------- ShowAllMusic, normal rows (entered from 0x4a0f34; v1=entry, v0=4 already) -------------
caveA:
  lw $t0, 0($v1)
  lw $v1, 0x1c($v1)
  addiu $t1, $zero, -2
  beq $t0, $t1, fakeA
  j 0x4a0f3c
fakeA:
  lw $s1, -0x70a4($gp)
  lw $t9, -0x6e34($gp)
  lw $a0, -0x7cb8($gp)
  addiu $a1, $zero, {PIC_ID}
  jalr $t9
  lw $gp, 0x20($sp)
  sw $v0, 0($s7)
  j 0x4a1094

# ---------------- ShowAllMusic, highlighted row (entered from 0x4a1508; v1=entry, v0=4 already) -------------
caveB:
  lw $t0, 0($v1)
  lw $s1, 0x1c($v1)
  addiu $t1, $zero, -2
  beq $t0, $t1, fakeB
  j 0x4a1510
fakeB:
  lw $s5, -0x737c($gp)
  lw $s3, -0x6b7c($gp)
  lw $s2, -0x70a4($gp)
  lw $t9, -0x6e34($gp)
  lw $a0, -0x7cb8($gp)
  addiu $a1, $zero, {PIC_ID}
  jalr $t9
  lw $gp, 0x20($sp)
  sw $v0, 0($s5)
  j 0x4a196c
"""

FALLBACK = b'Random\0\0'   # 8 bytes, word aligned


def assemble():
    ks = Ks(KS_ARCH_MIPS, KS_MODE_MIPS32 | KS_MODE_LITTLE_ENDIAN)
    fb = 0
    for _ in range(2):                       # pass 1 sizes the code, pass 2 fixes the string address
        src = ASM.replace('FBHI', hex(fb >> 16)).replace('FBLO', hex(fb & 0xffff))
        enc, _ = ks.asm(src, CAVE)
        fb = CAVE + len(enc)
    code = bytes(enc) + FALLBACK
    labels = {'fallback': fb}
    for lab in ('dsize', 'isfake0', 'rnd', 'wLOT', 'wEC', 'fixidx', 'wT1', 'wS1', 'caveA', 'caveB'):
        e2, _ = ks.asm(src.split('\n' + lab + ':')[0], CAVE)
        labels[lab] = CAVE + len(e2)
    return code, labels


def main():
    app = bytearray(open(sys.argv[1], 'rb').read())
    elf = ELFFile(open(sys.argv[1], 'rb'))
    byaddr = {s['st_value']: s.name for s in elf.get_section_by_name('.symtab').iter_symbols() if s['st_value']}
    gotsec = elf.get_section_by_name('.got')
    dtags = {t.entry.d_tag: t.entry.d_val for t in elf.get_section_by_name('.dynamic').iter_tags()}
    local_gotno, gotsym = dtags['DT_MIPS_LOCAL_GOTNO'], dtags['DT_MIPS_GOTSYM']
    dynsyms = list(elf.get_section_by_name('.dynsym').iter_symbols())
    got_off = lambda o: GP + o - 0x440000          # RW segment: file = vaddr - 0x440000
    rd = lambda off: struct.unpack_from('<I', app, off)[0]

    # sanity: GOT slots we read hold what we think
    for o, name in SYMS.items():
        v = rd(got_off(o))
        idx = (GP + o - gotsec['sh_addr']) // 4
        nm = byaddr.get(v)
        if idx >= local_gotno:
            nm = dynsyms[gotsym + idx - local_gotno].name
        assert nm == name, (hex(o), name, nm, hex(v))
    # sanity: the cave host is the untouched dead comparator
    assert byaddr.get(CAVE) == '_Z16class_lesssecondPK9MPlayListS1_'
    # sanity: patch sites are what we expect
    expect = {0x4a0f34: 0x8c63001c, 0x4a0f38: 0x24020004, 0x4a1508: 0x8c71001c, 0x4a150c: 0x24020004}
    for a, w in expect.items():
        assert rd(a - 0x400000) == w, (hex(a), hex(rd(a - 0x400000)))

    code, L = assemble()
    assert len(code) <= CAVE_SZ, len(code)
    app[CAVE - 0x400000: CAVE - 0x400000 + len(code)] = code

    j = lambda t: 0x08000000 | ((t >> 2) & 0x3ffffff)
    # entry detours: the stock gp-setup (+0,+4,+8) becomes `j wrapper; nop; <unused>`;
    # wrappers set $gp themselves and call the body at entry+12.
    for name, (o, entry) in GOT.items():
        assert rd(got_off(o)) == entry and byaddr.get(entry, '').find(name) >= 0, name
        w0, w1, w2 = (rd(entry - 0x400000 + k) for k in (0, 4, 8))
        assert w0 >> 16 == 0x3c1c and w1 >> 16 == 0x279c and w2 == 0x0399e021, name   # lui/addiu/addu gp
        tgt = {'LoadOtherType': L['wLOT'], 'EnterClass': L['wEC'],
               'LoadMusicByType1': L['wT1'], 'LoadAlbumBySonger1': L['wS1']}[name]
        struct.pack_into('<II', app, entry - 0x400000, j(tgt), 0)
    struct.pack_into('<I', app, 0x4a0f34 - 0x400000, j(L['caveA']))
    struct.pack_into('<I', app, 0x4a1508 - 0x400000, j(L['caveB']))
    open(sys.argv[2], 'wb').write(app)
    print('cave used %d/%d bytes' % (len(code), CAVE_SZ))
    for k, v in L.items():
        print('  %-14s %#x' % (k, v))


if __name__ == '__main__':
    main()
