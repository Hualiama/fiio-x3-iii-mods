# FiiO X3 Mark III — Reverse-Engineering Reference

Consolidated, verified RE notes for `fifo-audio-app` and the firmware. Companion to
[`firmware-teardown-and-scrobbler.md`](firmware-teardown-and-scrobbler.md) (project log / feature status).
All addresses are from the FW1.2.4 image; the binary is **not stripped** and ships DWARF.
Scripts: [`../tools/`](../tools/) — `yaffs.py` (extract + chunk map), `yaffs_patch.py` (surgical
rewrite), `mipsdis.py` (GOT/string-annotated disasm), `build_datetime.py`, `emu_test.py`.

## 1. Platform
- SoC: Ingenic **JZ4760**, MIPS32, **little-endian**. Kernel Linux 2.6.31.3 (uImage).
- Userland: BusyBox + glibc 2.3.6. Not Android.
- Player app: `/usr/pv-project/fifo-audio-app` — ELF32 LSB MIPS **EXEC** (fixed load 0x400000),
  dynamically linked, interpreter `/lib/ld.so.1`, **with .symtab + DWARF .debug_info**.
- Segments: LOAD#1 file 0..0x173200 → vaddr 0x400000 (R E: .text, .rodata incl. jump tables);
  LOAD#2 file 0x173200 → 0x5b3200 (RW). For anything < 0x573200: file off = vaddr − 0x400000.
- `.text` @ **0x411e40**, size 1323584. PIC/GOT: every function begins with the
  `$gp` setup idiom `lui $gp,0x12; addiu $gp,$gp,<off>; addu $gp,$gp,$t9`. The computed
  **module $gp is constant = 0x5d9530** (same for all functions) — a code cave can load
  it directly (`lui $gp,0x5d; ori $gp,$gp,0x9530`). Globals/func-ptrs reached
  `$gp`-relative (e.g. `lw $t9,-0x74c8($gp)`, `lw $v0,-0x7e18($gp)`).
- Switch jump tables: `.rodata` words holding (target − gp); code does `addu a0,a0,gp; jr a0`.

## 2. Disassembly / patch toolchain
- `pip install pyelftools capstone keystone-engine unicorn`; `Cs/Ks(..._MIPS, ..._MIPS32|..._LITTLE_ENDIAN)`.
- capstone stops at undecodable words — set `md.skipdata = True` for whole-.text sweeps.
- **Keystone auto-fills branch/jump/jalr delay slots with `nop`** — do NOT write your own `nop`
  after branches (you get two). Write straight-line logic (restore `$sp` before `jr $ra`). Always re-disasm.
- **Keystone compiles `jal <localLabel>` into a GOT stub (`lw $t9,0($gp); jalr`) — WRONG.**
  Use `bal <label>` (PC-relative link) for intra-cave calls; `j <label>`/`j 0xABS` is fine.
- Calling app/libc functions from a cave: `lw $t9, GOTOFF($gp); jalr $t9`, then re-set `$gp`
  (lui/ori). External libc calls go through lazy stubs — need correct `$gp` and `$t9`.
- **Off-device testing:** Unicorn MIPS32-LE; map both LOAD segments, point the needed GOT slots at
  `jr ra` stubs and emulate them in Python hooks; hook `UC_HOOK_INTR` for `syscall` (`tools/emu_test.py`).
- **Code caves:** `.text` has no padding, exec seg has no slack. Host in a **verified-dead
  function**: zero direct `j/jal`, address absent from `.got`/all loaded data (only
  `.eh_frame` FDE + non-loaded `.symtab`/debug refs OK), not in `.dynsym`. Host used:
  **`_Z14JumpRamdomFilev` @0x445934 (3636 B, dead)** — usage now:
  0x445934–0x445a3c scroll v2 · 0x445a3c–0x446400 Date & time (code 0x445a3c–0x446260, data 0x44626c–) ·
  **free 0x446400–0x446768 (~870 B)**. Second host (Random rows): `_Z16class_lesssecond…` @0x49cf24 (2884 B,
  1552 B used) — see [`random-row-patch.md`](random-row-patch.md).
- **Writable scratch:** `.bss` ends 0x5d8670; the mapped bss page extends zero-filled to
  0x5d9000, so **0x5d8670..0x5d9000 is free zero RW scratch** (used 0x5d8680–0x5d868c by scroll v2,
  0x5d86a0–0x5d86ac by Random rows).

## 3. Patching & repack procedures (reversible, no full rebuild)
### YAFFS2 image (`fifo_rootfs.yaffs2`)
- 2048-byte page + 64-byte spare; tags packed at **spare offset 2** (16 bytes:
  `<seq,objid,chunkid,nbytes>` LE), rest 0xFF, **no software ECC** (HW ECC at flash). seq=4096.
- Header chunk: chunkid=0, nbytes=0xFFFF (no extra-header info in tags); page: type@[0:4],
  parent@[4:8], name@[10:266], mode@[268], a/m/ctime@[280:292], **fileSize@[292]**.
  Data chunk: chunkid>=1, nbytes@(spare+14), 0xFF-padded.
- **Surgical edit**: map chunkid→image-index (tag objid==file oid, chunkid = file_off//2048 + 1);
  rewrite that file's chunks; update tag nbytes + header fileSize. Same chunk count required
  (`yaffs_patch.py` asserts). vaddr→file: `vaddr − 0x400000`.
- Validate: re-extract, md5-diff all 3219 files — only intended file(s) differ.

### Rebuild `.fw`
- GNU tar order: `fifo_uimage, fifo_uimage.md5, fifo_rootfs.yaffs2, fifo_rootfs.yaffs2.md5`;
  gzip. `.md5` = 32 lowercase hex, **no newline**. File on card must be named `X3_Mark_Ⅲ.fw`.
  Flash via update UI (power+Shortcuts). Recovery: reflash stock FW1.2.4.

## 4. Input subsystem (scroll-acceleration)
Flow: reader threads (`run1..6`/`get_key_thread1..5`) → `get_key_onboardN` → `add_key(code)`
→ `std::deque<int>` queue → UI `get_key()` → **tail-calls `SpecialKeyFilter(code)`** (raw → UI code).
- `input_event` 16 B: sec@0,usec@4,type@8,code@10,value@12 (LE).
- **CONFIRMED (on-device ev*.bin):** wheel = **`/dev/input/event1`**, EV_KEY, **code 3 / code 4
  = the two directions**, each detent = press+release. `event0` = buttons (codes 2,8 — don't
  accelerate). Fast spin ~20–70 ms/detent; single steps ≥500 ms.
- `add_key(int)` @ **0x4bdc34**: `lock(mutex@this+0x5c); if(size<cap) deque.push_back; unlock`.
  a0=this, a1=code. gp setup +0/+4/+8; **body @ +0x0c (0x4bdc40)**.
- `get_key()` @ **0x4bcc68** — non-blocking (returns 0 when empty); calls `MainScheduling()` first.
  ms-tick helper: `lw $t9,-0x6b74($gp); lw $a0,-0x6ca8($gp); jalr` → v0 = ms.
- **UI key codes seen by screens (after SpecialKeyFilter), from Setting()/SetSleepTime():**
  1 = up/decrease, 2 = down/increase, 0x39/0x3b = up variants, 0x3a/0x3c = down variants,
  3 = back, 4 = OK, 0xfa = redraw, 0xfb = go home (propagate as return value),
  0x46/0x47/0x52/0x53/0xb3 = volume (`Adjustvol`).

### Scroll-acceleration — shipped builds (detour: `add_key`+0 = `j 0x445934`; cave in JumpRamdomFile)
- **v1** (`X3_Mark_III_scroll.fw`): backlog-based. step = clamp(deque_size+1, 1, 10). Too low
  a ceiling (capped by key-buffer size) — superseded.
- **v2 — CURRENT** (`X3_Mark_III_scroll_v2.fw`): exponential time-based ramp. State @0x5d8680
  (last_ms, last_dir, step). On code 3/4: now=tick_ms; if same dir & 0≤dt<200 ms → step<<=1
  (cap **128**); else step=1. Push code `step` times (`bal real_push`; real_push = set gp +
  `j 0x4bdc40`). Reentrant (stack; only event1 thread writes state). Reaches 128/detent in ~7
  fast detents; slow/reverse snaps to 1. **Knobs:** CAP (0x81 slti / `li 0x80`), FAST window
  (0xc8=200ms), ramp (sll = ×2). All verified by re-disasm; tune on-device for feel.
  Screens that redraw per key should drain the queue before redrawing (Date & time does).
  *(The v2 build script itself is not in this repo — only the resulting build and these notes.)*

## 5. UI list / navigation
- `ShowAllMusic(int,bool)` @ **0x4a0d3c** / `ShowClassMusic(int,bool)` @ **0x4a1dbc**: Category
  render+nav. Tests key codes incl. 1,2,3,4,7,8,0xf0.
- `ShowMusicName(int)` @0x45c5a8. `ShowSideScroll` @0x430150. `SpecialKeyFilter(int)` @ **0x42980c**.
- File browser: `ShowFileList` @0x442d88, `Turn_fList` @0x444f18, `fList_Enter` @0x44c22c,
  `BrowerFileList` @0x44ed44, `ShowFList2` @0x43a254, `LoadFolderList` @0x43d7ec.
- **gui_module primitives (all via GOT, a0 = `Gui` object `lw a0,-0x7048(gp)`):**
  `draw_text(str,x,y,align)` -0x7e20 (5th arg @sp+0x10; align 1 = centre, y = baseline);
  `set_text_color(r,g,b)` -0x6c6c; `set_font(i)` -0x790c (Msyh.ttf 0..4 = 12/14/16/18/20 px; settings
  use `3-(fontSize==0)`); `draw_sprite(path,0,0)` -0x6f98 + `blit_sprite(x,y)` -0x70c8;
  `display_update()` -0x74d4; `get_string_width(font,str,len)` -0x7798.
  Pictures: `GetIndexData(PicList,id)` -0x6e34 (PicList obj -0x7cb8) → entry {+4 path,+8 x,+0xc y};
  same call on `TxtList` (-0x7328) → {+4 text}. Store last pic entry in `Pic` (-0x737c) like the app.
- Useful GOT slots: `time` -0x7468, `localtime_r` -0x7878, `usleep` -0x6b10, `sprintf` -0x7e08,
  `system` -0x7108, `presskey_time` -0x723c, `get_key` -0x7d0c, `get_key_clear` -0x73b0,
  `Input` obj -0x7b94, `ShowSettingFace` -0x6fc0, `ShowTitleBar` -0x79fc, `SetStart` -0x7d10, `SetSele` -0x73c8.

## 6. Playback / play-mode
- `mcl_module` = hardware/player-service layer: `mcl_set_play_mode(int)` @ **0x4cb740**,
  `mcl_set_mode` @0x4cb68c, `mcl_set_play` @0x4cbcbc, `mcl_get_music_info` @0x4ccad8,
  `mcl_get_playing_status` @0x4cd6c8.
- Random machinery present: `deq_playrandom` @0x5d5ee0, `iRandom`, `/dev/urandom`. Global
  shuffle exists (UI "Play mode", string id 30).
- `ChangeMusic(int,bool)` @ **0x468790**, `IntoPlaying(bool)` @0x414e84. `record_lastplay`
  static @0x5bc418 (writes `/mnt/mtdblock7/playlast`).
- Tab string ids: All Songs=10, Album=11, Artist=12. Per-tab Random row implemented — see
  [`random-row-patch.md`](random-row-patch.md).

## 7. On-device data/config
- `/mnt/mtdblock7` (yaffs2 on `/dev/mtdblock5`): `Config.ini`, `database.db` (SQLite, table
  `tfcard`: songer=artist, time=length), `blueconfig.ini`, `M3U8DIR/`, BT dir. `playlast`
  is never written by the stock app in practice.
- `/mnt/mmc`: SD card (`/dev/mmcblk0p1`, vfat).
- Boot hook: `etc/udev/scripts/mmc.sh` → runs `/mnt/mmc/custom.sh` on card mount.
- Clock: resets to 2010-10-01 12:00 every cold boot; RTC (`rtc0`) also reads 2010 at boot → set via Settings → Date & time.

## 8. System settings menu + Date & time patch (2026-11-18)
- `Setting()` @ **0x49b144**: key loop. List = `SetStart` (first visible) + `SetSele` (row 0..6), 7 rows.
  Down: `SetSele++` or `SetStart++`; wrap test `slti v0,SetStart,0x14` @ **0x49b6dc**. Up from top:
  `SetStart=0x13, SetSele=6` @ **0x49b870**. Stock list = **26 items (idx 0..25)**.
  OK (key 4) → `SysSet_Fun(4, SetStart+SetSele)`; return 0xfb → go home.
- `ShowSettingFace(mode,titleId)` @ **0x481f60**: label of idx i = TxtList[**170+i**]; right side by
  `ShowSetRight(idx,row)` @0x4811cc (own jump table for idx<0x18, default arrow for ≥0x18).
  mode 0 = draw only; 1/2 = + display_update; 3 = + blit_background_r. Title = TxtList[titleId].
- `SysSet_Fun(key,idx)` @ **0x499738**: `idx<27` → jump table @ **0x5588d0** (27 entries, target−gp).
  Map: 0 UpdateDatabase … 21 AboutInfo, 22 SetLanguage, 23 font size (0x49a370), 24 FormatDevice,
  25 RestoreFun, **26 (hidden, unreachable in stock) TFupdate_firware "Firmware update" @0x49a4ec**.
  Common exits: **0x499a54** = ShowSettingFace(2,5)+return; **0x499a68** = return v0.
- Value-picker pattern to copy: `SetSleepTime()` @0x494218 + `ShowSleepTime(int)` @0x480ca0 —
  ShowSettingFace(0,title) background, PicList 715 `adjust/bg.png` (0,23) overlay, 716 `text_bg.png`
  pill (42,188, 237×31), value text centred at (160,210).
- String 196 has no other references (no `li 0xc4` anywhere in .text) → safe to relabel.
- **Patch (build_datetime.py):** jump-table entry 26 → `e26` @0x445a3c (checks key==4, `bal dt_screen`,
  exits via 0x499a54/0x499a68); wrap constants 0x14→**0x15**, 0x13→**0x14** (27 items);
  TxtList 196 relabeled in all 11 languages ("Date & time", 日期和时间, 日期與時間, 日付と時刻,
  날짜 및 시간, Datum & Uhrzeit, Date et heure, Data e ora, Fecha y hora, Дата и время, วันที่และเวลา —
  all glyphs present in Msyh.ttf).
- `dt_screen` (frame 0x1c0): time()+localtime_r → fields Y/M/D/h/m (year clamped 2020..2037);
  loop: get_key; draw only when queue empty & dirty; usleep 20 ms idle. Save: epoch computed
  in code, `settimeofday` via `syscall` 4079 (a3≠0 → "Could not set time"), then system() with
  `/tmp/.clockset` delta + hwclock + `.scrobbler.clock`. Display: font 4 date line y=105, time line
  y=150, active field in [ ], hint (font 2) in pill y=210.
