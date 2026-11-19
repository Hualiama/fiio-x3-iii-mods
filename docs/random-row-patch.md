# Random row in Album / Artist lists — patch notes (2026-11-19)

Build: `X3_Mark_Ⅲ.fw` = Date & time build (orig .fw md5 `55844a3a…`) + this patch.
Rootfs md5 `c162f5afa0d45477a2201de2db21c664`. Tools: [`../tools/build_random.py`](../tools/build_random.py), [`../tools/emu_random.py`](../tools/emu_random.py).
**Status: CONFIRMED WORKING ON DEVICE (2026-11-19).**

## Behaviour
- Category → Album / Artist: first row is "Random" (shuffle icon). Genre and other tabs unchanged.
- Album → Random: cursor jumps to a random album and opens it (track list), exactly as if selected. No playback.
- Artist → Random: cursor jumps to a random artist and opens that artist's album list. No playback.
- Back returns to the list with the picked album/artist highlighted (not the Random row).
- "Jump List" setting (catalog_jump, continuous play across albums/artists) skips the Random row:
  forward past the last item → first real item; back from the first real item → last item.

## How the browse lists work (fifo-audio-app)
- `ShowClassList(bool)` @0x4b0c7c = key loop. Root (`c_nLayer`=0) category index `cRoot_pos`:
  0 All songs, 1 Album, 2 Artist, 3 Genre, 4 Favourites, 5 Playlists, 6 Recently added.
- Layer 1 list (albums/artists/genres) is built by `LoadOtherType()` @0x4a04c0 into `deq_Dir`
  (sqlite → `SyncClassType` callback; adds "Unknown album/artist" (id −1, TxtList 320+cRoot); sorts with
  `less_second`). Drawn by `ShowAllMusic(int,bool)` @0x4a0d3c (layer 1, 7 rows, row h 31); deeper layers
  by `ShowClassMusic` (6 rows). Cursor = `cStart` (first visible) + `cSele` (row).
- OK → `EnterClass()` @0x4af8fc: layer 1 Album/Genre → `LoadMusicByType` (tracks, layer 2);
  layer 1 Artist → `LoadAlbumBySonger` (albums, layer 2) → `LoadMusicByAlbum` (layer 3). Song (type 8) → `Player`.
  Saves position in `i_grade[layer]` = cStart<<16 | cSele; `deq_break`/`deq_save` hold copies per layer.
- MPlayList (0x3c B, malloc'd, freed by Clear_deq_Dir/CopyShowDeq which deep-copy):
  +0 id, +4 name, +8.. strings, +0x1c type (4 = category item, 8 = song, 1/6 others), +0x38 initial letter.
- Jump List: `ChangeMusic`/`ChangeList` step `otherindex` over `deq_style_album` (`LoadMusicByType1` @0x4a426c)
  and `singerindex` over `deq_singer` (`LoadAlbumBySonger1` @0x4a7dfc); both are copies of the layer-1 list.
- quickSearch filters by +0x38 initial (Random row has 0 → never matches), then finds the row by name.

## Patch
- Code host: dead comparator `_Z16class_lesssecond…` @0x49cf24 (2884 B, zero refs incl. GOT-page+lo
  materialisations — checked with `refscan.py`). Used 1552 B (0x49cf24–0x49d534). JumpRamdomFile cave untouched.
- Hooks are **entry detours**: the 3-insn `$gp` prologue of LoadOtherType / EnterClass / LoadMusicByType1 /
  LoadAlbumBySonger1 → `j wrapper; nop`; wrappers set gp=0x5d9530 and call body at entry+12 (t9 not read
  before being reloaded in those bodies). **Do not redirect their GOT slots** — they are *global* GOT
  entries (exported in .dynsym), ld.so may rewrite them.
- wLOT: after body, if cRoot∈{1,2}, ret≥1 and row 0 isn't already fake → malloc fake (id −2, type 4,
  name = strdup(TxtList[405]) or built-in "Random"), push_front (fast path or `_M_push_front_aux` @0x4515b8), ret+1.
- wEC: layer 1, cRoot∈{1,2}, n≥2, cursor idx 0 and row 0 fake → r = 1 + rnd % (n−1);
  cStart = n≤7 ? 0 : min(r, n−7); cSele = r − cStart; then stock EnterClass. Afterwards records
  LASTA/LASTS = otherindex/singerindex.
- rnd: LCG (×1103515245 + 12345) mixed with `tools_module::mtimer` ms each press; state in RAM.
- wT1 / wS1 (`fixidx`): if index is 0 and list row 0 is fake → (last==1 ? n−1 : 1); then body; store last.
- ShowAllMusic icon: 0x4a0f34 (`lw v1,0x1c(v1)`) → `j caveA`, 0x4a1508 (`lw s1,0x1c(v1)`) → `j caveB`
  (delay slots keep the stock `addiu v0,zero,4`). For id −2 they load PicList[159] and continue at the
  stock type-4 draw paths 0x4a1094 / 0x4a196c; else return to 0x4a0f3c / 0x4a1510.
- Scratch RAM: 0x5d86a0 LASTA, 0x5d86a4 LASTS, 0x5d86a8 RNG (scroll v2 uses 0x5d8680–0x5d868c).
- Resources: TxtList **405** appended to all 11 txt_list files (Random, 随机, 隨機, ランダム, 무작위, Zufall,
  Aléatoire, Casuale, Aleatorio, Случайно, สุ่ม — glyphs checked in Msyh.ttf). PicList **159** (unused in code,
  was list/all.png "未发现") → `themeN/playing/random1.png,9,24` in pic_list1–6 (28×28 shuffle icon).
- Image: 18 files changed (app + 11 txt + 6 pic lists), chunk counts unchanged, re-extract diff = exactly those 18.

## Emulator coverage (emu_random.py, all pass)
- wLOT: Album/Artist add exactly one row (fast + slow push_front), other tabs/empty/error untouched,
  missing string → fallback, no duplicate when row already present, callee-saved regs/sp preserved.
- wEC: n = 2,3,7,8,9,30,500 — pick always in 1..n−1, window valid, every row reachable; consecutive
  presses within the same ms still vary; untouched when not on the Random row / wrong layer / tab.
- Jump List guard: forward wrap → 1, backward → n−1, n=2 → 1, normal steps / lists without the row untouched.
- Icon caves: fake row → PicList 159 + stock draw path; normal / Unknown rows unchanged.

## On-device checklist
1. Album and Artist show "Random" + shuffle icon at top (also check another theme). Genre has none.
2. Album → Random opens a random album's tracks; Back lands on that album. Repeat a few times.
3. Artist → Random opens a random artist's albums; Back lands on that artist.
4. With Jump List on: play the last album to the end → continues at the first album (not stuck);
   "previous" from first album's first track → last album.
5. Switch tabs via menu key, quick search (letters), SD re-scan: no duplicate Random rows, no crash.

## Known limitations
- If an album/artist is literally named "Random" and is chosen via quick search, the name lookup may land
  on the Random row instead (then a random pick happens).
- Jump List direction is inferred from the last index used; right after a resume-from-boot the very first
  "previous album" from the first album may go to album 1 instead of the last.
