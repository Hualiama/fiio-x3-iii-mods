# FiiO X3 Mark III — firmware teardown + offline Last.fm scrobbler

> All features go into the stock FW1.2.4 app. The current build is included in the repo as [`../firmware/X3_Mark_Ⅲ.fw`](../firmware/).

## Current firmware build (2026-11-19): `X3_Mark_Ⅲ.fw` = FW1.2.4 + SD hook + scroll v2 + Date & time + Random rows
- 2026-11-19: Date & time build used as the base (md5 `55844a3ad04cdf1e2d5d0c5be002be5e`); added **Random row at the top of Album and Artist lists** (Feature 3). Details: [`random-row-patch.md`](random-row-patch.md). New rootfs md5 `c162f5afa0d45477a2201de2db21c664`. **Confirmed working on device (2026-11-19).**
- Previous build (2026-11-18): FW1.2.4 + SD hook + scroll v2 + Date & time.
- Base = the scroll-v2 build (verified: `add_key`+0 = `j 0x445934`, cap 128, 200 ms window; `mmc.sh` hook present).
- Added **Settings → Date & time** (last item of System settings). Details/addresses: [`reverse-engineering-reference.md`](reverse-engineering-reference.md) §8. Build/test tools: [`../tools/`](../tools/) (`build_datetime.py`, `emu_test.py`, `yaffs.py`, `yaffs_patch.py`, `mipsdis.py`).
- Date & time rootfs changes vs base: `fifo-audio-app` + the 11 `txt_list_*.txt` (string 196 relabeled).
- Verified off-device in a Unicorn MIPS emulator with stubbed app calls (exact epoch, leap years, wraps, cancel/home exits, 128-key wheel burst → single redraw). **Confirmed working on the device (2026-11-19).**

## Firmware package (FW1.2.4, the stock .fw)
- `.fw` = gzip'd GNU tar containing 4 members, in order:
  `fifo_uimage`, `fifo_uimage.md5`, `fifo_rootfs.yaffs2`, `fifo_rootfs.yaffs2.md5`
- `.md5` files = 32 lowercase hex chars, **no trailing newline**. Updater verifies these.
- Kernel: u-boot uImage, Linux 2.6.31.3, MIPS, Ingenic **JZ4760** SoC. BusyBox userland (not Android).
- Rootfs: YAFFS2, 106,261,056 bytes.

## YAFFS2 image geometry (critical for repacking)
- 2048-byte data page + 64-byte spare; 50,313 chunks total.
- Spare layout: bytes[0:2]=0xFFFF, **bytes[2:18]=packed tags** `<seq,objid,chunkid,nbytes>` (LE), bytes[18:64]=0xFF.
- **No software ECC in image** (device uses hardware ECC at flash time). Constant seq=4096 (mkyaffs2image-style).
- Object header chunks: chunkid=0, tag nbytes=0xFFFF. In the header page: type@[0:4], parent@[4:8], name@[10:266], mode@[268], times@[280:292], **fileSize@[292]**.
- Data chunks: chunkid>=1, nbytes=actual bytes, 0xFF padded.

## Deployment method used (low-risk, recoverable)
- **Surgical patch**, not a full repack: keep every stock chunk byte-identical, change only the target files' data chunks + header size field. Validated by re-extracting and diffing md5 of all 3219 files.
- Small size changes that keep the same chunk count (e.g. text-file edits within the last chunk's slack): rewrite the file's chunks, update tag nbytes + header fileSize (`tools/yaffs_patch.py` asserts same chunk count). For a file that GROWS past its chunk count, append new data chunks + bump header size. Binary (.text) patches keep file length identical.
- Boot hook: `etc/udev/scripts/mmc.sh` (runs on SD mount) patched to launch `/mnt/mmc/custom.sh` once per mount, guarded by `/tmp/custom_started`. Community-proven pattern (rogeliodh X1 II).
- After the flash, scrobbler logic lives in editable `custom.sh` on the SD card — no reflashing to iterate.
- Recovery: reflash stock FW1.2.4 via the normal update UI.
- Disassembly toolchain: pyelftools + capstone (MIPS32 LE, use `skipdata=True` for whole-.text sweeps). Binary has `.symtab` + DWARF `.debug_info`. `.text` @0x411e40 size 1323584.
- Decompiler: Ghidra 11.2.1 headless (Java 21). Import + auto-analysis ≈3 min; a small `Decomp.java` post-script dumps C for given addresses.

## Scrobbler (v0.5 — script in [`../sdcard/custom.sh`](../sdcard/custom.sh); v0.3/v0.4 verified on device)
- Writes `/mnt/mmc/.scrobbler.log` (Audioscrobbler 1.1): `artist\talbum\ttitle\ttracknum\tlength\tL|S\tstart_unixts\tmbid\n`.
- Last.fm rule: track >=30s AND (played >=50% OR >=240s) -> L; unknown length -> needs 240s played.
- Pure BusyBox sh + awk (available: awk, tr, od, dd, date, stat, md5sum, ps, pidof, kill, sync, hwclock).
- **BusyBox awk gotcha:** `-F'\t'` is NOT unescaped → splits on letter "t". Always use `BEGIN{FS="\t"}`. (This was the v0.2 bug.)
- **Recon findings (2026-11-18) that killed v0.1:**
  - `/mnt/mtdblock7/playlast` is NOT created even after playback → abandoned as now-playing source.
  - `database.db` `tfcard` has **time=0 for all 7291 rows** (no `track` column; columns: id,name,url,album,songer,style,title,time,time_end,time_create,Initial). Also a `favority` table, same shape.
  - Library = all `.m4a`, path layout `/mnt/mmc/<Artist>/<Album>/NN - Title.m4a` (some `D-NN - Title`).
  - Clock resets to Oct 1 2010 12:00 UTC every boot.
  - Processes: `./fifo-audio-app` (UI), `./mplayer -ao alsa -vo NULL -colorkey 0x020202 -dynamicbuf 0` (playback, persistent process), `./system-daemon`.
- **Approach:** now-playing = audio file open in `/proc/<mplayer pid>/fd` (confirmed on device); length+tags parsed from m4a atoms (moov/mvhd, udta/meta/ilst ©ART/aART/©alb/©nam/trkn) with dd/od/awk, path fallback; pause = mplayer CPU ticks delta < 3 per 5s poll; stop = no open file for 2 polls; state saved every 30s to `.scrobbler.state` and recovered on next boot; clock moved forward at start to max(saved `.scrobbler.clock`, newest SD-root mtime excluding the scrobbler's own files, RTC if it kept counting) — `date -s` confirmed working on device.
- **On-device verification (2026-11-18, v0.3):** full play → scrobbled (153/152s); skip at 25s → dropped; pause detected (dt=0 → playing=0); stop mid-play → finalized and scrobbled when played ≥50%. Tick deltas per 5s poll: playing 7–32 (spikes ~90–166 at track start), paused 0 → PAUSE_TICKS=3 has good margin.
- Timestamps are local wall-clock (FAT mtimes are local, read as UTC) — consistent with `#TZ/UNKNOWN` meaning local time. The Date & time screen also sets wall-clock-as-UTC, so they stay consistent.
- **RTC result (v0.4 logs, 3 power cycles 2026-11-18):** `/dev/rtc0` + `hwclock` exist, but `since_epoch` reads **1285934404–405 (2010-10-01 12:00:05) at every boot** even though the time was written before shutdown → the player cannot keep time across power-off. v0.5 keeps the write + read-back debug line to tell "writes rejected" from "RTC unpowered".
- **v0.4 bug found in logs:** clock saved only every 30 s → each boot resumed up to 30 s *before* the last session's last log line. Fixed in v0.5 (clock saved every poll).
- **v0.5 (2026-11-18):** reads `/tmp/.clockset` (written by Settings → Date & time = new−old seconds); shifts every log line written this session (lines after `SESSION_LINES`) and the in-progress track's start by that delta, adjusts `LAST`. Tested under BusyBox 1.36 sh/awk (shift, negative delta, garbage ignored, own-file mtimes ignored by fix_clock). **Confirmed on device (2026-11-19):** 88-line log covering Nov 18 20:18 → Nov 19 01:45 matched actual listening times. Start times jitter ±~10 s vs previous track end (5 s poll) — harmless.
- Remaining limitation: plays from an *earlier* session whose clock was never set keep their stale timestamps (unknown off-time). Workflow: after a cold boot, set the time in Settings before/while listening.

## Feature 2: scroll-wheel acceleration — SHIPPED (v2)
- See [`reverse-engineering-reference.md`](reverse-engineering-reference.md) §4. Wheel codes reach `add_key` as 3/4; `SpecialKeyFilter` (tail-called by `get_key`) maps raw codes to UI codes before screens see them.

## Feature 3: Random row in Album / Artist lists — SHIPPED 2026-11-19 (works on device)
- Spec: "Random" at the top of the Album list picks a random album; same at the top of the Artist list. Artist Random must **open** the artist (no playback). No random-songs option wanted.
- Implemented: Album → Random opens a random album's track list; Artist → Random opens a random artist's album list (both = "as if you selected that row"; no playback). Shuffle icon, label translated in 11 languages. Jump List continuation skips the row.
- Full design, addresses, tests and on-device checklist: [`random-row-patch.md`](random-row-patch.md). Tools: `build_random.py`, `emu_random.py`, `refscan.py`.
- Global shuffle (unchanged): `mcl_module::mcl_set_play_mode(int)` @0x4cb740; random queue `deq_playrandom`; "Play mode" = UI string id 30.

## Feature 4: Settings → Date & time — SHIPPED 2026-11-18 (works on device, confirmed 2026-11-19)
- Wheel adjusts the bracketed field (year 2020–2037, month, day, hour, minute; wraps; day clamped to month length incl. leap years); OK = next field, OK on minute = save; Back = previous field / cancel; Home key passes through.
- Save = `settimeofday` syscall (4079) directly, then `system("echo DELTA > /tmp/.clockset; hwclock -w -u; [SD mounted] && echo EPOCH > /mnt/mmc/.scrobbler.clock; sync")`. Shows "Time set" (or "Could not set time") for 0.7 s.
- Hint text in the screen is English only; the menu label is translated in all 11 languages.

## Key paths / addresses
- Player app: `/usr/pv-project/fifo-audio-app` (MIPS ELF, not stripped, debug symbols).
- UI strings: `/usr/pv-project/320x240-Resource/Config/txt_list_*.txt` (format `[id,text,x,y]`, UTF-8), per-language (big ch de en fr ita jp kor rus sp thai). Pictures: `pic_list*.txt` same format `[id,path,x,y]`. Both parsed into deques and looked up linearly by id → new ids can simply be appended (405 = "Random").
- Config/data partition: `/mnt/mtdblock7` (yaffs2 from `/dev/mtdblock5`): database.db, Config.ini, M3U8DIR/, BT dir.
- SD card: `/mnt/mmc` (vfat), music at `/mnt/mmc/<Artist>/<Album>/`.
- Key funcs: `SpecialKeyFilter(int)` @0x42980c (6008 B, key dispatch); `ChangeMusic(int,bool)` @0x468790; `IntoPlaying(bool)` @0x414e84; `Setting()` @0x49b144; `SysSet_Fun(int,int)` @0x499738; `ShowClassList` @0x4b0c7c; `EnterClass` @0x4af8fc; `LoadOtherType` @0x4a04c0; `ShowAllMusic` @0x4a0d3c.
