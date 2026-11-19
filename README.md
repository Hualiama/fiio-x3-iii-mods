# FiiO X3 Mark III — stock firmware hacks

Custom features patched directly into the **stock FW1.2.4** firmware of the FiiO X3 Mark III
(Ingenic JZ4760, MIPS32 LE, Linux 2.6.31, BusyBox). Every change is a surgical
binary/resource patch that keeps the rest of the firmware byte-identical, so you can always go back
by reflashing stock FW1.2.4.

> ⚠️ Flashing modified firmware is at your own risk. All of it has been tested on one device.

**The ready-to-flash patched firmware is included:** https://github.com/Hualiama/fiio-x3-iii-mods/releases/tag/firmware — make sure to rename it to "X3_Mark_Ⅲ.fw" |
FW1.2.4 + SD hook + scroll v2 + Date & time + Random rows (rootfs md5 `c162f5afa0d45477a2201de2db21c664`).
Copy it to the SD card root and flash it from the update menu; no need to build anything yourself.

## Features

| Feature | Status | How |
|---|---|---|
| **Offline Last.fm scrobbler** | ✅ v0.5, verified on device | `sdcard/custom.sh`, launched from SD by a boot hook |
| **SD boot hook** (`mmc.sh` → `/mnt/mmc/custom.sh`) | ✅ | rootfs patch to `etc/udev/scripts/mmc.sh` |
| **Scroll-wheel acceleration** (v2, exponential, ×128 cap) | ✅ | code cave in dead `JumpRamdomFile` |
| **Settings → Date & time** | ✅ verified on device | `tools/build_datetime.py` |
| **"Random" row in Album / Artist lists** | ✅ verified on device | `tools/build_random.py` |

### Scrobbler
Writes `/.scrobbler.log` (Audioscrobbler 1.1) on the SD card — upload it to Last.fm with any
`.scrobbler.log` uploader. Pure BusyBox `sh`/`awk`, no network. Detects now-playing from
`mplayer`'s open file descriptors, reads length/tags from m4a atoms (path fallback), detects pause
via CPU ticks, survives power-off mid-track, and keeps the clock moving forward across boots.
When you set the clock in **Settings → Date & time**, this session's scrobbles are shifted to the
corrected time automatically.

**The player has no working RTC** (it reads Oct 2010 at every cold boot) — set the time in
Settings after a cold boot.

### Date & time
Last item of System settings. Wheel adjusts the bracketed field (year 2020–2037, month, day,
hour, minute), OK = next field / save, Back = previous field / cancel. Calls `settimeofday`,
writes the RTC, and tells the scrobbler the delta. Menu label translated into all 11 UI languages.

### Random row
"Random" (with a shuffle icon) is the first row in **Album** and **Artist**. Album → Random opens
a random album's track list; Artist → Random opens a random artist's albums — no auto-play.
"Jump List" continuous play skips the row. Label translated into all 11 languages.

## Repo layout

```
docs/
  firmware-teardown-and-scrobbler.md   project log, firmware format, scrobbler design, feature status
  reverse-engineering-reference.md     addresses, GOT slots, toolchain gotchas, patch/repack procedure
  random-row-patch.md                  Random row: design, hooks, tests, on-device checklist
sdcard/
  custom.sh                            scrobbler v0.5 — copy to the SD card root
tools/
  yaffs.py            extract the YAFFS2 rootfs + write chunkmap.json
  yaffs_patch.py      surgically replace files inside the YAFFS2 image (same chunk count)
  mipsdis.py          GOT/string-annotated MIPS disassembler for fifo-audio-app
  refscan.py          prove a candidate code cave has zero references
  build_datetime.py   patch: Settings → Date & time
  emu_test.py         Unicorn tests for the Date & time patch
  build_random.py     patch: Random row in Album/Artist
  emu_random.py       Unicorn tests for the Random row patch
```

## Setup

```sh
pip install -r requirements.txt     # pyelftools capstone keystone-engine unicorn
```

## Building it yourself (optional)

Only needed if you want to change the patches; the included `.fw` already has everything.

The patches stack in this order: **stock FW1.2.4 → SD hook + scroll v2 → Date & time → Random row**.
(The SD-hook and scroll-v2 build steps were done by hand; their details are in
`docs/reverse-engineering-reference.md` §2–4.)

```sh
# 1. unpack the .fw (gzip'd tar: fifo_uimage, .md5, fifo_rootfs.yaffs2, .md5)
mkdir fw && tar -xzf X3_Mark_Ⅲ.fw -C fw

# 2. extract the rootfs and its chunk map
python3 tools/yaffs.py fw/fifo_rootfs.yaffs2 work/root     # -> work/chunkmap.json

# 3. patch the player app (input must be the matching earlier build)
APP=work/root/usr/pv-project/fifo-audio-app
python3 tools/build_datetime.py $APP app.dt          # on the scroll-v2 build
python3 tools/emu_test.py app.dt                     # -> ALL OK
python3 tools/build_random.py app.dt app.dt_rand
python3 tools/emu_random.py app.dt_rand              # -> ALL OK

# 4. edit the resource files (txt_list_*.txt string 196 / 405, pic_list1-6 id 159),
#    then write everything back into the image
python3 tools/yaffs_patch.py fw/fifo_rootfs.yaffs2 new_rootfs.yaffs2 work/chunkmap.json \
    /usr/pv-project/fifo-audio-app=app.dt_rand \
    /usr/pv-project/320x240-Resource/Config/txt_list_en.txt=edited/txt_list_en.txt  # ...etc

# 5. validate: re-extract new_rootfs.yaffs2 and md5-diff every file — only the intended ones may differ

# 6. repack (order matters, .md5 = 32 lowercase hex, NO trailing newline)
cp new_rootfs.yaffs2 fw/fifo_rootfs.yaffs2
printf %s "$(md5sum fw/fifo_rootfs.yaffs2 | cut -c1-32)" > fw/fifo_rootfs.yaffs2.md5
(cd fw && tar -cf - fifo_uimage fifo_uimage.md5 fifo_rootfs.yaffs2 fifo_rootfs.yaffs2.md5) | gzip > X3_Mark_Ⅲ.fw
```

Copy `X3_Mark_Ⅲ.fw` to the SD card root and flash via the normal update menu.
Copy `sdcard/custom.sh` to the SD card root for the scrobbler.

**Recovery:** reflash the stock FW1.2.4 through the same update menu.

## Key facts at a glance
- `fifo-audio-app` is not stripped (symbols + DWARF). Module `$gp` = `0x5d9530` for every function.
- Code caves live in verified-dead functions: `JumpRamdomFile` @0x445934 (~870 B free) and
  `class_lesssecond` @0x49cf24 (~1.3 KB free). Free RW scratch: 0x5d8670–0x5d9000.
- Keystone gotchas: it auto-fills delay slots with `nop`, and `jal localLabel` becomes a GOT
  stub — use `bal`.
- BusyBox awk gotcha: `-F'\t'` splits on the letter `t`; use `BEGIN{FS="\t"}`.
