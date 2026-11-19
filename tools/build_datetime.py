#!/usr/bin/env python3
"""Add a "Date & time" item to System settings of the FiiO X3 Mark III app.

Patches fifo-audio-app (FW1.2.4 + scroll v2) in place:
  1. new code in the dead function JumpRamdomFile, after the scroll-v2 cave
  2. SysSet_Fun jump-table entry 26 (was the hidden "Firmware update") -> new handler
  3. Setting(): list wrap 26 -> 27 items so entry 26 is reachable
usage: build_datetime.py IN_APP OUT_APP
"""
import sys, struct
from keystone import Ks, KS_ARCH_MIPS, KS_MODE_MIPS32, KS_MODE_LITTLE_ENDIAN
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN

BASE = 0x400000
GP = 0x5d9530
CAVE = 0x445a3c                       # first free byte after scroll v2 cave
CAVE_END = 0x445934 + 3636            # end of JumpRamdomFile
JT26 = 0x5588d0 + 26 * 4              # SysSet_Fun jump table, entry 26
SYSSET_REDRAW = 0x499a54              # ShowSettingFace(2,5); return
SYSSET_RET = 0x499a68                 # return v0
WRAP_DOWN = 0x49b6dc                  # slti v0,v0,0x14  (SetStart wrap)
WRAP_UP = 0x49b870                    # addiu v0,zero,0x13 (SetStart on wrap-up)
TITLE_STR = 196                       # TxtList id used as label/title

# GOT offsets ($gp-relative)
G = dict(time=-0x7468, localtime_r=-0x7878, usleep=-0x6b10, sprintf=-0x7e08,
         system=-0x7108, presskey=-0x723c, key_clear=-0x73b0, get_key=-0x7d0c,
         Input=-0x7b94, Gui=-0x7048, draw_text=-0x7e20, display_update=-0x74d4,
         set_text_color=-0x6c6c, set_font=-0x790c, draw_sprite=-0x6f98,
         blit_sprite=-0x70c8, GetIndexData=-0x6e34, PicList=-0x7cb8, Pic=-0x737c,
         ShowSettingFace=-0x6fc0)

STRINGS = [
    ('S_E', ''), ('S_LB', '['), ('S_RB', ']'),
    ('F_DATE', '%s%04d%s-%s%02d%s-%s%02d%s'),
    ('F_TIME', '%s%02d%s:%s%02d%s'),
    ('H_NEXT', 'Wheel: adjust    OK: next'),
    ('H_SAVE', 'Wheel: adjust    OK: save'),
    ('H_DONE', 'Time set'),
    ('H_FAIL', 'Could not set time'),
    ('F_CMD', "echo %d > /tmp/.clockset; hwclock -w -u >/dev/null 2>&1; "
              "grep -q ' /mnt/mmc ' /proc/mounts && echo %u > /mnt/mmc/.scrobbler.clock; sync"),
]
TABLES = [
    ('T_MIN', [2020, 1, 1, 0, 0]),
    ('T_MAX', [2037, 12, 31, 23, 59]),
    ('T_DIM', [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]),
    ('T_CUM', [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]),
]


def gp():
    return 'lui $gp, 0x5d\nori $gp, $gp, 0x9530\n'


def call(name):
    return f'lw $t9, {G[name]}($gp)\njalr $t9\nnop\n' + gp()


def la(reg, addr):
    return f'lui {reg}, {addr >> 16:#x}\nori {reg}, {reg}, {addr & 0xffff:#x}\n'


_n = [0]


def dim(dst, y, m, tmp):
    """dst = days in month m of year y (2020..2037: leap <=> y%4==0)."""
    _n[0] += 1
    L = f'dim{_n[0]}'
    return (la(tmp, D['T_DIM']) +
            f'addiu {dst}, {m}, -1\nsll {dst}, {dst}, 2\naddu {dst}, {dst}, {tmp}\n'
            f'lw {dst}, 0({dst})\n'
            f'addiu {tmp}, $zero, 2\nbne {m}, {tmp}, {L}\nnop\n'
            f'andi {tmp}, {y}, 3\nbnez {tmp}, {L}\nnop\naddiu {dst}, {dst}, 1\n{L}:\n')


def source():
    s = ''
    # ---------------- SysSet_Fun jump-table entry 26 (runs in its frame) ---------------
    s += 'e26:\n' + gp()
    s += 'addiu $v0, $zero, 4\nbne $s0, $v0, e26_redraw\nnop\n'
    s += 'bal dt_screen\nnop\n' + gp()
    s += 'addiu $v1, $zero, 0xfb\nbne $v0, $v1, e26_redraw\nnop\n'
    s += f'move $v0, $v1\nj {SYSSET_RET:#x}\nnop\n'
    s += f'e26_redraw:\nj {SYSSET_REDRAW:#x}\nnop\n'

    # ---------------- dt_screen(): returns 0 (back/saved) or 0xfb (home) --------------
    # frame 0x1c0: 0x34 time_t, 0x38 struct tm, 0x64 fields[5], 0x80 buf1,
    #              0xc0 buf2, 0x100 cmd buf, 0x1a0 timeval
    s += 'dt_screen:\naddiu $sp, $sp, -0x1c0\n'
    s += 'sw $ra, 0x1bc($sp)\nsw $s0, 0x1b8($sp)\nsw $s1, 0x1b4($sp)\nsw $s2, 0x1b0($sp)\nsw $s3, 0x1ac($sp)\n'
    s += gp()
    s += 'move $a0, $zero\n' + call('time') + 'sw $v0, 0x34($sp)\n'
    s += 'addiu $a0, $sp, 0x34\naddiu $a1, $sp, 0x38\n' + call('localtime_r')
    s += 'addiu $s2, $sp, 0x64\n'
    s += 'lw $t0, 0x4c($sp)\naddiu $t0, $t0, 1900\nsw $t0, 0($s2)\n'      # year
    s += 'lw $t0, 0x48($sp)\naddiu $t0, $t0, 1\nsw $t0, 4($s2)\n'         # month
    s += 'lw $t0, 0x44($sp)\nsw $t0, 8($s2)\n'                            # day
    s += 'lw $t0, 0x40($sp)\nsw $t0, 12($s2)\n'                           # hour
    s += 'lw $t0, 0x3c($sp)\nsw $t0, 16($s2)\n'                           # minute
    # clamp every field into range (fresh boot shows 2010 -> 2020)
    s += 'lw $t0, 0($s2)\nslti $t1, $t0, 2020\nbeqz $t1, ylo\nnop\naddiu $t0, $zero, 2020\nylo:\n'
    s += 'slti $t1, $t0, 2038\nbnez $t1, yhi\nnop\naddiu $t0, $zero, 2037\nyhi:\nsw $t0, 0($s2)\n'
    s += 'move $s1, $zero\n'
    s += 'lw $a0, %d($gp)\n' % G['Input'] + call('key_clear')
    s += 'addiu $s3, $zero, 1\n'                                          # dirty
    # ---- main loop ----
    s += 'loop:\nlw $a0, %d($gp)\n' % G['Input'] + call('get_key')
    s += 'move $s0, $v0\nbnez $s0, gotkey\nnop\n'
    s += 'beqz $s3, idle\nnop\nmove $a0, $zero\nbal draw\nnop\n' + gp() + 'move $s3, $zero\n'
    s += 'idle:\nlui $a0, 0\nori $a0, $a0, 20000\n' + call('usleep') + 'b loop\nnop\n'
    s += 'gotkey:\n' + call('presskey')
    for k in (1, 0x39, 0x3b):
        s += f'addiu $t0, $zero, {k:#x}\nbeq $s0, $t0, dec\nnop\n'
    for k in (2, 0x3a, 0x3c):
        s += f'addiu $t0, $zero, {k:#x}\nbeq $s0, $t0, inc\nnop\n'
    s += 'addiu $t0, $zero, 4\nbeq $s0, $t0, ok\nnop\n'
    s += 'addiu $t0, $zero, 3\nbeq $s0, $t0, back\nnop\n'
    s += 'addiu $t0, $zero, 0xfa\nbeq $s0, $t0, mark\nnop\n'
    s += 'addiu $t0, $zero, 0xfb\nbeq $s0, $t0, home\nnop\n'
    s += 'b loop\nnop\n'
    s += 'dec:\naddiu $a0, $zero, -1\nb adj\nnop\n'
    s += 'inc:\naddiu $a0, $zero, 1\n'
    s += 'adj:\nsll $t1, $s1, 2\naddu $t1, $s2, $t1\nlw $t2, 0($t1)\naddu $t2, $t2, $a0\nsw $t2, 0($t1)\n'
    s += 'bal wrapclamp\nnop\n'
    # month/year change may shorten the month: re-clamp the day
    s += 'lw $t0, 8($s2)\n'
    s += 'lw $t8, 0($s2)\nlw $t9, 4($s2)\n' + dim('$t1', '$t8', '$t9', '$t5')
    s += 'slt $t2, $t1, $t0\nbeqz $t2, dayok\nnop\nsw $t1, 8($s2)\ndayok:\n'
    s += 'mark:\naddiu $s3, $zero, 1\nb loop\nnop\n'
    s += 'ok:\naddiu $t0, $zero, 4\nbeq $s1, $t0, save\nnop\naddiu $s1, $s1, 1\nb mark\nnop\n'
    s += 'back:\nbeqz $s1, cancel\nnop\naddiu $s1, $s1, -1\nb mark\nnop\n'
    s += 'cancel:\nmove $v0, $zero\nb out\nnop\n'
    s += 'home:\naddiu $v0, $zero, 0xfb\nb out\nnop\n'
    # ---- save: epoch from fields, settimeofday, hwclock + scrobbler clock file ----
    s += 'save:\n'
    s += 'lw $t0, 0($s2)\n'                                   # Y
    s += 'addiu $t1, $t0, -1970\naddiu $t2, $zero, 365\nmultu $t1, $t2\nmflo $t3\n'   # (Y-1970)*365
    s += 'addiu $t1, $t0, -1969\nsrl $t1, $t1, 2\naddu $t3, $t3, $t1\n'                # + leap days before Y
    s += 'lw $t4, 4($s2)\n' + la('$t5', D['T_CUM'])
    s += 'addiu $t6, $t4, -1\nsll $t6, $t6, 2\naddu $t6, $t6, $t5\nlw $t6, 0($t6)\naddu $t3, $t3, $t6\n'
    s += 'slti $t6, $t4, 3\nbnez $t6, leapx\nnop\nandi $t6, $t0, 3\nbnez $t6, leapx\nnop\naddiu $t3, $t3, 1\nleapx:\n'
    s += 'lw $t6, 8($s2)\naddiu $t6, $t6, -1\naddu $t3, $t3, $t6\n'                     # + day-1
    s += la('$t2', 86400) + 'multu $t3, $t2\nmflo $t3\n'
    s += 'lw $t6, 12($s2)\naddiu $t2, $zero, 3600\nmultu $t6, $t2\nmflo $t6\naddu $t3, $t3, $t6\n'
    s += 'lw $t6, 16($s2)\naddiu $t2, $zero, 60\nmultu $t6, $t2\nmflo $t6\naddu $t3, $t3, $t6\n'
    s += 'sw $t3, 0x1a0($sp)\nsw $zero, 0x1a4($sp)\n'
    # old clock right before the change -> delta for the scrobbler (/tmp/.clockset)
    s += 'move $a0, $zero\n' + call('time') + 'lw $t3, 0x1a0($sp)\nsubu $t3, $t3, $v0\nsw $t3, 0x1a8($sp)\n'
    s += 'addiu $a0, $sp, 0x1a0\nmove $a1, $zero\naddiu $v0, $zero, 4079\nsyscall\n'     # settimeofday
    s += gp()
    s += 'bnez $a3, failed\nnop\n'
    s += 'addiu $a0, $sp, 0x100\n' + la('$a1', D['F_CMD']) + 'lw $a2, 0x1a8($sp)\nlw $a3, 0x1a0($sp)\n' + call('sprintf')
    s += 'addiu $a0, $sp, 0x100\n' + call('system')
    s += la('$a0', D['H_DONE']) + 'b showmsg\nnop\n'
    s += 'failed:\n' + la('$a0', D['H_FAIL'])
    s += 'showmsg:\nbal draw\nnop\n' + gp()
    s += 'lui $a0, 0xa\nori $a0, $a0, 0xae60\n' + call('usleep')      # 700 ms
    s += 'move $v0, $zero\n'
    s += 'out:\nlw $ra, 0x1bc($sp)\nlw $s0, 0x1b8($sp)\nlw $s1, 0x1b4($sp)\nlw $s2, 0x1b0($sp)\nlw $s3, 0x1ac($sp)\n'
    s += 'addiu $sp, $sp, 0x1c0\njr $ra\nnop\n'

    # ---------------- wrapclamp: wrap field s1 of fields[s2] into its range (leaf) -------
    s += 'wrapclamp:\nsll $t0, $s1, 2\naddu $t9, $s2, $t0\nlw $t1, 0($t9)\n'
    s += la('$t2', D['T_MIN']) + 'addu $t2, $t2, $t0\nlw $t2, 0($t2)\n'
    s += la('$t3', D['T_MAX']) + 'addu $t3, $t3, $t0\nlw $t3, 0($t3)\n'
    s += 'addiu $t4, $zero, 2\nbne $s1, $t4, wc_nd\nnop\nlw $t6, 0($s2)\nlw $t7, 4($s2)\n'
    s += dim('$t3', '$t6', '$t7', '$t5') + 'wc_nd:\n'
    s += 'slt $t4, $t1, $t2\nbeqz $t4, wc_a\nnop\nmove $t1, $t3\nwc_a:\n'      # below min -> max
    s += 'slt $t4, $t3, $t1\nbeqz $t4, wc_b\nnop\nmove $t1, $t2\nwc_b:\n'      # above max -> min
    s += 'sw $t1, 0($t9)\njr $ra\nnop\n'

    # ---------------- sprite(a1=id): draw a PicList picture at its own x,y ---------------
    s += 'sprite:\naddiu $sp, $sp, -0x20\nsw $ra, 0x1c($sp)\n' + gp()
    s += 'lw $a0, %d($gp)\n' % G['PicList'] + call('GetIndexData')
    s += 'sw $v0, 0x18($sp)\nlw $t0, %d($gp)\nsw $v0, 0($t0)\n' % G['Pic']
    s += 'lw $a1, 4($v0)\nlw $a0, %d($gp)\nmove $a2, $zero\nmove $a3, $zero\n' % G['Gui'] + call('draw_sprite')
    s += 'lw $v0, 0x18($sp)\nlw $a1, 8($v0)\nlw $a2, 12($v0)\nlw $a0, %d($gp)\n' % G['Gui'] + call('blit_sprite')
    s += 'lw $ra, 0x1c($sp)\naddiu $sp, $sp, 0x20\njr $ra\nnop\n'

    # ---------------- sel(a0=k): v0 = "[" or "", v1 = "]" or "" for field k (leaf) -------
    s += 'sel:\n' + la('$v0', D['S_E']) + la('$v1', D['S_E'])
    s += 'bne $a0, $s1, sel_x\nnop\n' + la('$v0', D['S_LB']) + la('$v1', D['S_RB']) + 'sel_x:\njr $ra\nnop\n'

    # ---------------- draw(a0 = message or 0): full screen redraw ------------------------
    s += 'draw:\naddiu $sp, $sp, -0x50\nsw $ra, 0x4c($sp)\nsw $a0, 0x48($sp)\n' + gp()
    s += f'move $a0, $zero\naddiu $a1, $zero, {TITLE_STR}\n' + call('ShowSettingFace')
    s += 'addiu $a1, $zero, 715\nbal sprite\nnop\n'          # adjust/bg.png   (0,23)
    s += 'addiu $a1, $zero, 716\nbal sprite\nnop\n' + gp()   # text_bg.png pill (42,188)
    s += 'lw $a0, %d($gp)\naddiu $a1, $zero, 0xff\naddiu $a2, $zero, 0xff\naddiu $a3, $zero, 0xff\n' % G['Gui'] + call('set_text_color')
    s += 'lw $a0, %d($gp)\naddiu $a1, $zero, 4\n' % G['Gui'] + call('set_font')
    # date line
    s += 'move $a0, $zero\nbal sel\nnop\nmove $a2, $v0\nsw $v1, 0x10($sp)\nlw $a3, 0($s2)\n'
    s += 'sw $a2, 0x40($sp)\nsw $a3, 0x44($sp)\n'
    s += 'addiu $a0, $zero, 1\nbal sel\nnop\nsw $v0, 0x14($sp)\nsw $v1, 0x1c($sp)\nlw $t0, 4($s2)\nsw $t0, 0x18($sp)\n'
    s += 'addiu $a0, $zero, 2\nbal sel\nnop\nsw $v0, 0x20($sp)\nsw $v1, 0x28($sp)\nlw $t0, 8($s2)\nsw $t0, 0x24($sp)\n'
    s += 'lw $a2, 0x40($sp)\nlw $a3, 0x44($sp)\naddiu $a0, $s2, 0x1c\n' + la('$a1', D['F_DATE']) + call('sprintf')
    s += 'lw $a0, %d($gp)\naddiu $a1, $s2, 0x1c\naddiu $a2, $zero, 160\naddiu $a3, $zero, 105\naddiu $t0, $zero, 1\nsw $t0, 0x10($sp)\n' % G['Gui'] + call('draw_text')
    # time line
    s += 'addiu $a0, $zero, 3\nbal sel\nnop\nsw $v0, 0x40($sp)\nsw $v1, 0x10($sp)\n'
    s += 'addiu $a0, $zero, 4\nbal sel\nnop\nsw $v0, 0x14($sp)\nsw $v1, 0x1c($sp)\nlw $t0, 16($s2)\nsw $t0, 0x18($sp)\n'
    s += 'lw $a2, 0x40($sp)\nlw $a3, 12($s2)\naddiu $a0, $s2, 0x5c\n' + la('$a1', D['F_TIME']) + call('sprintf')
    s += 'lw $a0, %d($gp)\naddiu $a1, $s2, 0x5c\naddiu $a2, $zero, 160\naddiu $a3, $zero, 150\naddiu $t0, $zero, 1\nsw $t0, 0x10($sp)\n' % G['Gui'] + call('draw_text')
    # hint / message in the pill
    s += 'lw $a0, %d($gp)\naddiu $a1, $zero, 2\n' % G['Gui'] + call('set_font')
    s += 'lw $a1, 0x48($sp)\nbnez $a1, hint_x\nnop\n' + la('$a1', D['H_NEXT'])
    s += 'addiu $t0, $zero, 4\nbne $s1, $t0, hint_x\nnop\n' + la('$a1', D['H_SAVE']) + 'hint_x:\n'
    s += 'lw $a0, %d($gp)\naddiu $a2, $zero, 160\naddiu $a3, $zero, 210\naddiu $t0, $zero, 1\nsw $t0, 0x10($sp)\n' % G['Gui'] + call('draw_text')
    s += 'lw $a0, %d($gp)\n' % G['Gui'] + call('display_update')
    s += 'lw $ra, 0x4c($sp)\naddiu $sp, $sp, 0x50\njr $ra\nnop\n'
    return s


def data_blob(base):
    out, addr = b'', {}
    for name, vals in TABLES:
        addr[name] = base + len(out)
        out += struct.pack('<%dI' % len(vals), *vals)
    for name, txt in STRINGS:
        addr[name] = base + len(out)
        out += txt.encode() + b'\0'
    while len(out) % 4:
        out += b'\0'
    return out, addr


def assemble(data_base):
    global D
    _, D = data_blob(data_base)
    ks = Ks(KS_ARCH_MIPS, KS_MODE_MIPS32 | KS_MODE_LITTLE_ENDIAN)
    # keystone fills every branch/jump delay slot with a nop itself
    src = '\n'.join(l for l in source().split('\n') if l.strip() != 'nop')
    enc, _ = ks.asm(src, CAVE)
    return bytes(enc)


def main():
    app = bytearray(open(sys.argv[1], 'rb').read())
    code = assemble(CAVE + 0x800)                      # pass 1: size only
    dbase = CAVE + ((len(code) + 15) & ~15)
    code = assemble(dbase)                             # pass 2: real addresses
    blob, _ = data_blob(dbase)
    end = dbase + len(blob)
    assert end <= CAVE_END, f'cave overflow: {end:#x} > {CAVE_END:#x}'
    off = CAVE - BASE
    assert len(code) <= dbase - CAVE
    app[off: off + len(code)] = code
    app[dbase - BASE: dbase - BASE + len(blob)] = blob
    # jump table entry 26 -> e26 (gp-relative offset)
    old = struct.unpack_from('<i', app, JT26 - BASE)[0]
    assert (old + GP) & 0xffffffff == 0x49a4ec, hex((old + GP) & 0xffffffff)
    struct.pack_into('<i', app, JT26 - BASE, CAVE - GP)
    # list wrap: 26 -> 27 items
    for va, old_w, new_w in ((WRAP_DOWN, 0x28420014, 0x28420015), (WRAP_UP, 0x24020013, 0x24020014)):
        cur = struct.unpack_from('<I', app, va - BASE)[0]
        assert cur == old_w, (hex(va), hex(cur))
        struct.pack_into('<I', app, va - BASE, new_w)
    open(sys.argv[2], 'wb').write(app)
    print(f'code {CAVE:#x}-{CAVE + len(code):#x} ({len(code)} B), data {dbase:#x}-{end:#x}, '
          f'cave free left {CAVE_END - end} B')
    for k, v in sorted(D.items(), key=lambda x: x[1]):
        print(f'  {k:7s} {v:#x}')


if __name__ == '__main__':
    main()
