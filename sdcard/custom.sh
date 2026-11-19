#!/bin/sh
# FiiO X3 Mark III - offline Last.fm scrobbler (v0.5)
# v0.5: works with the firmware's new Settings > Date & time screen.
#       - when you set the time there, the firmware leaves the correction in
#         /tmp/.clockset; every scrobble of this session (and the track playing
#         now) is shifted by it, so plays made before you set the clock are fixed.
#       - the clock is saved every poll (was every 30s, so each boot restarted
#         up to 30s in the past).
#       - the scrobbler's own files no longer pull the boot clock forward (they
#         could hold "future" times after you set the clock back).
#       Note: the hardware RTC does NOT survive power-off on this player (it reads
#       Oct 2010 at every boot), so after a cold boot set the time in Settings.
# v0.4: keeps the hardware RTC updated and trusts it on boot if it kept running.
# v0.3: busybox awk ignores \t in -F'\t' (split on letter t!) -> use FS="\t";
#       path fallback handles disc-track names like '1-08 - Title'.
# Place this file at the ROOT of your SD card, named exactly:  custom.sh
# The patched mmc.sh launches it automatically once the card is mounted.
#
# Writes /mnt/mmc/.scrobbler.log (Audioscrobbler 1.1) for upload to Last.fm.
# No network. Never touches playback; only reads /proc and the audio files.
#
# v0.2 changes (from on-device recon):
#  - /mnt/mtdblock7/playlast is never written by the stock app, so now-playing
#    is taken from the file the mplayer process has open (/proc/<pid>/fd).
#  - database.db has time=0 for every track, so length (and tags) are read
#    from the .m4a itself (moov/mvhd + udta/meta/ilst), path as fallback.
#  - pause is detected via mplayer CPU time, so paused time doesn't count.
#  - the device clock resets to Oct 2010 every boot; it is now moved forward
#    to the newest of: saved clock, newest file time on the SD card.
#  - in-progress play is saved, so power-off / track that ends playback still
#    gets scrobbled (on stop, or on next boot).

SD=${SD:-/mnt/mmc}
DATA=/mnt/mtdblock7
LOG="$SD/.scrobbler.log"
STATE="$SD/.scrobbler.state"
CLOCK="$SD/.scrobbler.clock"
CLOCKSET=${CLOCKSET:-/tmp/.clockset}   # written by Settings > Date & time
RECON="$SD/scrobbler-recon"
DBG="$SD/scrobbler-debug.txt"
POLL=${POLL:-5}            # seconds between checks
MINLEN=30         # Last.fm ignores tracks shorter than 30s
PAUSE_TICKS=3     # mplayer CPU ticks per poll below this = paused
SAVE_EVERY=6      # save full state every N polls (6*5s = 30s)
DEBUG_POLLS=40    # verbose debug for the first N polls after start

now(){ date +%s 2>/dev/null; }
dbg(){ echo "$(now) $*" >> "$DBG" 2>/dev/null; }

# ---------- helpers: epoch -> "YYYY-MM-DD hh:mm:ss" (busybox date may lack @N) ----------
epoch2str(){ awk -v t="$1" 'BEGIN{
  d=int(t/86400); s=t-d*86400; z=d+719468; era=int(z/146097); doe=z-era*146097
  yoe=int((doe-int(doe/1460)+int(doe/36524)-int(doe/146096))/365); y=yoe+era*400
  doy=doe-(365*yoe+int(yoe/4)-int(yoe/100)); mp=int((5*doy+2)/153)
  dd=doy-int((153*mp+2)/5)+1; m=(mp<10)?mp+3:mp-9; if(m<=2)y++
  printf "%04d-%02d-%02d %02d:%02d:%02d", y,m,dd,int(s/3600),int((s%3600)/60),s%60 }'; }

isnum(){ case "$1" in ''|*[!0-9]*) return 1;; esac; return 0; }
isint(){ case "$1" in -*) isnum "${1#-}";; *) isnum "$1";; esac; }

# ---------- clock: never go backwards past what we've already seen ----------
# hardware RTC (JZ4760 has one; stock firmware never uses it). Empty if none.
rtc_read(){
  t=$(cat /sys/class/rtc/rtc0/since_epoch 2>/dev/null)
  if ! isnum "$t" && command -v hwclock >/dev/null 2>&1; then
    hwclock -s -u >/dev/null 2>&1 && t=$(now)     # sets system time from RTC; fix_clock resets it below
  fi
  isnum "$t" && echo "$t"
}
rtc_write(){ [ -n "$HAVE_RTC" ] && hwclock -w -u >/dev/null 2>&1; }

fix_clock(){
  cur=$(now); best=$cur; saved=""
  for f in "$SD"/* "$SD"/.[!.]*; do
    [ -e "$f" ] || continue
    case "${f##*/}" in .scrobbler.*|scrobbler-*) continue;; esac   # our own files: covered by $CLOCK
    t=$(stat -c %Y "$f" 2>/dev/null)
    isnum "$t" && [ "$t" -gt "$best" ] && best=$t
  done
  if [ -r "$CLOCK" ]; then saved=$(cat "$CLOCK" 2>/dev/null); isnum "$saved" && [ "$saved" -gt "$best" ] && best=$saved; fi
  rtc=$(rtc_read)
  # trust the RTC only if it kept counting past our last saved time (and isn't absurd)
  if isnum "$rtc" && [ "$rtc" -gt "$best" ]; then
    if ! isnum "$saved" || [ "$rtc" -le $((saved + 31622400)) ]; then best=$rtc; RTC_USED=1; fi
  fi
  if [ "$best" != "$(now)" ]; then
    date -u -s "@$best" >/dev/null 2>&1 || date -u -s "$(epoch2str "$best")" >/dev/null 2>&1
  fi
  command -v hwclock >/dev/null 2>&1 && HAVE_RTC=1
  rtc_write; sleep 2
  RTC_BACK=$(cat /sys/class/rtc/rtc0/since_epoch 2>/dev/null)
  dbg "clock: rtc readback after write=$RTC_BACK (system $(now))"
  dbg "clock: boot=$cur saved=$saved rtc=$rtc rtc_used=${RTC_USED:-0} hwclock=${HAVE_RTC:-0} rtcdev=[$(ls /dev/rtc* /sys/class/rtc 2>/dev/null | tr '\n' ' ')] target=$best now=$(now) ($(date 2>/dev/null))"
}

# shift_log DELTA: move every scrobble written in this session by DELTA seconds
shift_log(){
  [ -s "$LOG" ] || return 0
  awk -v n="$SESSION_LINES" -v d="$1" 'BEGIN{FS="\t"; OFS="\t"}
    NR>n && substr($0,1,1)!="#" && NF>=7 && $7 ~ /^[0-9]+$/ { $7=sprintf("%.0f", $7+d) }
    { print }' "$LOG" > "$LOG.tmp" && mv -f "$LOG.tmp" "$LOG"
}

# ---------- m4a reader (pure dd/od/awk) ----------
# atom_hdr FILE OFF -> "size hdrlen typehex"
atom_hdr(){
  dd if="$1" bs=1 skip="$2" count=16 2>/dev/null | od -An -tu1 | awk '
    {for(i=1;i<=NF;i++)b[++n]=$i}
    END{ if(n<8) exit 1
      s=((b[1]*256+b[2])*256+b[3])*256+b[4]; h=8
      if(s==1){ if(n<16) exit 1; s=0; for(i=9;i<=16;i++) s=s*256+b[i]; h=16 }
      printf "%.0f %d %02x%02x%02x%02x\n", s, h, b[5],b[6],b[7],b[8] }'
}
# find_child FILE START END TYPEHEX -> "off size hdrlen"
find_child(){
  _fc_f=$1; _fc_p=$2; _fc_e=$3; _fc_w=$4; _fc_n=0
  while [ "$_fc_p" -lt "$_fc_e" ] && [ "$_fc_n" -lt 200 ]; do
    _fc_h=$(atom_hdr "$_fc_f" "$_fc_p") || return 1
    set -- $_fc_h
    _fc_s=$1; [ "$_fc_s" = 0 ] && _fc_s=$((_fc_e - _fc_p))
    [ "$_fc_s" -lt 8 ] && return 1
    [ "$3" = "$_fc_w" ] && { echo "$_fc_p $_fc_s $2"; return 0; }
    _fc_p=$((_fc_p + _fc_s)); _fc_n=$((_fc_n + 1))
  done
  return 1
}
rd_text(){ dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null | tr '\t\r\n\000' '    ' | sed 's/ *$//'; }

# m4a_meta FILE -> "artist\talbum\ttitle\tlen\ttrack" (fields may be empty)
m4a_meta(){
  _f=$1; _a=""; _al=""; _t=""; _ln=0; _tk=""
  _sz=$(stat -c %s "$_f" 2>/dev/null); isnum "$_sz" || return 1
  _mv=$(find_child "$_f" 0 "$_sz" 6d6f6f76) || return 1          # moov
  set -- $_mv; _ms=$(($1 + $3)); _me=$(($1 + $2))
  if _x=$(find_child "$_f" "$_ms" "$_me" 6d766864); then         # mvhd
    set -- $_x
    _ln=$(dd if="$_f" bs=1 skip=$(($1 + $3)) count=32 2>/dev/null | od -An -tu1 | awk '
      function be(i,k, v,j){v=0;for(j=0;j<k;j++)v=v*256+b[i+j];return v}
      {for(i=1;i<=NF;i++)b[++n]=$i}
      END{ if(b[1]==1){ts=be(21,4);du=be(25,8)} else {ts=be(13,4);du=be(17,4)}
           if(ts>0) printf "%d", du/ts+0.5; else print 0 }')
  fi
  if _x=$(find_child "$_f" "$_ms" "$_me" 75647461); then         # udta
    set -- $_x; _us=$(($1 + $3)); _ue=$(($1 + $2))
    if _x=$(find_child "$_f" "$_us" "$_ue" 6d657461); then       # meta
      set -- $_x; _mts=$(($1 + $3)); _mte=$(($1 + $2))
      _il=$(find_child "$_f" $((_mts + 4)) "$_mte" 696c7374) || \
      _il=$(find_child "$_f" "$_mts" "$_mte" 696c7374)            # ilst (full box or QT meta)
      if [ -n "$_il" ]; then
        set -- $_il; _p=$(($1 + $3)); _ie=$(($1 + $2)); _n=0
        while [ "$_p" -lt "$_ie" ] && [ "$_n" -lt 100 ]; do
          _h=$(atom_hdr "$_f" "$_p") || break
          set -- $_h; _is=$1; _ih=$2; _ty=$3
          [ "$_is" -lt 8 ] && break
          case "$_ty" in
            a9415254|61415254|a9616c62|a96e616d|74726b6e)          # (c)ART aART (c)alb (c)nam trkn
              _do=$((_p + _ih))
              if _dh=$(atom_hdr "$_f" "$_do"); then
                set -- $_dh
                if [ "$3" = 64617461 ]; then                       # data
                  _vo=$((_do + $2 + 8)); _vl=$(($1 - $2 - 8))
                  [ "$_vl" -gt 512 ] && _vl=512
                  if [ "$_vl" -gt 0 ]; then
                    case "$_ty" in
                      a9415254) _a=$(rd_text "$_f" "$_vo" "$_vl");;
                      61415254) _aa=$(rd_text "$_f" "$_vo" "$_vl");;
                      a9616c62) _al=$(rd_text "$_f" "$_vo" "$_vl");;
                      a96e616d) _t=$(rd_text "$_f" "$_vo" "$_vl");;
                      74726b6e) _tk=$(dd if="$_f" bs=1 skip="$_vo" count=4 2>/dev/null | od -An -tu1 | awk '{for(i=1;i<=NF;i++)b[++n]=$i} END{v=b[3]*256+b[4]; if(v>0) print v}');;
                    esac
                  fi
                fi
              fi;;
          esac
          _p=$((_p + _is)); _n=$((_n + 1))
        done
      fi
    fi
  fi
  [ -z "$_a" ] && _a=$_aa; _aa=""
  printf '%s\t%s\t%s\t%s\t%s' "$_a" "$_al" "$_t" "${_ln:-0}" "$_tk"
}

# path fallback: .../Artist/Album/NN - Title.ext
path_meta(){
  printf '%s\n' "$1" | awk -F/ '{
    fn=$NF; al=(NF>=2)?$(NF-1):""; ar=(NF>=3)?$(NF-2):""
    sub(/\.[^.]*$/,"",fn); tk=""
    if (match(fn,/^([0-9]+-)?[0-9]+([-. ]+|$)/)) { tk=substr(fn,1,RLENGTH); fn=substr(fn,RLENGTH+1); sub(/[-. ]+$/,"",tk); sub(/^[0-9]+-/,"",tk); sub(/^0+/,"",tk) }
    printf "%s\t%s\t%s\t0\t%s", ar, al, fn, tk }'
}

field(){ printf '%s' "$1" | awk -v n="$2" 'BEGIN{FS="\t"} {print $n}'; }

# track_meta FILE -> "artist\talbum\ttitle\tlen\ttrack"
track_meta(){
  _pm=$(path_meta "$1")
  case "$1" in *.m4a|*.M4A|*.mp4|*.MP4|*.m4b|*.M4B) _mm=$(m4a_meta "$1");; *) _mm="";; esac
  # merge: tags win, path fills blanks
  printf '%s\n%s\n' "$_pm" "$_mm" | awk 'BEGIN{FS="\t"}
    NR==1{for(i=1;i<=5;i++)p[i]=$i; next}
    {for(i=1;i<=5;i++) if($i!="" && !(i==4 && $i+0==0)) p[i]=$i}
    END{printf "%s\t%s\t%s\t%d\t%s", p[1],p[2],p[3],p[4]+0,p[5]}'
}

# ---------- now playing ----------
AUDIO_RE='\.(m4a|m4b|mp4|aac|mp3|flac|ogg|oga|opus|wav|aif|aiff|ape|wv|wma|dsf|dff)$'
open_audio(){  # PID -> path of audio file it has open on the SD card
  ls -l "/proc/$1/fd" 2>/dev/null | awk -v sd="$SD/" -v re="$AUDIO_RE" '
    { i=index($0," -> "); if(!i) next; f=substr($0,i+4)
      if(index(f,sd)==1 && tolower(f) ~ re){ print f; exit } }'
}
cpu_ticks(){ awk '{print $14+$15}' "/proc/$1/stat" 2>/dev/null; }

# ---------- one-time diagnostics (kept from v0.1) ----------
if [ -z "$SCROBBLER_LIB" ]; then

if [ -f /tmp/scrobbler.pid ] && kill -0 "$(cat /tmp/scrobbler.pid 2>/dev/null)" 2>/dev/null; then
  exit 0
fi
echo $$ > /tmp/scrobbler.pid

if [ ! -d "$RECON" ]; then
  mkdir -p "$RECON" 2>/dev/null
  { echo "# scrobbler recon $(now)"; echo "## uname"; uname -a;
    echo "## date"; date; echo "## mount"; mount;
    echo "## ls -la $DATA"; ls -la "$DATA"; echo "## ps"; ps; } > "$RECON/sysinfo.txt" 2>&1
  ls -laR "$DATA" > "$RECON/mtdblock7-tree.txt" 2>/dev/null
  sync
fi

# ---------- scrobbler ----------
[ -f "$DBG" ] && [ "$(stat -c %s "$DBG" 2>/dev/null || echo 0)" -gt 262144 ] && : > "$DBG"
dbg "=== scrobbler v0.5 start pid $$"
fix_clock
[ -s "$LOG" ] || printf '#AUDIOSCROBBLER/1.1\n#TZ/UNKNOWN\n#CLIENT/FiiO-X3III offline 0.5\n' > "$LOG"
SESSION_LINES=$(wc -l < "$LOG" 2>/dev/null); isnum "$SESSION_LINES" || SESSION_LINES=0

CK=""; CS=0; CP=0; CL=""
if [ -r "$STATE" ]; then           # resume / recover play from last session
  { read -r CK; read -r CS; read -r CP; read -r CL; } < "$STATE" 2>/dev/null
  isnum "$CS" || CS=0; isnum "$CP" || CP=0
  dbg "recovered state: $CK played=$CP"
fi

save_clock(){ now > "$CLOCK.tmp" 2>/dev/null && mv -f "$CLOCK.tmp" "$CLOCK" && sync; }
save_state(){
  if [ -n "$CK" ]; then printf '%s\n%s\n%s\n%s\n' "$CK" "$CS" "$CP" "$CL" > "$STATE.tmp" && mv -f "$STATE.tmp" "$STATE"
  else rm -f "$STATE"; fi
  save_clock; rtc_write; sync
}

finish(){  # decide whether the current play gets scrobbled
  [ -n "$CK" ] || return 0
  len=$(field "$CL" 4); isnum "$len" || len=0
  half=$((len / 2)); [ "$half" -gt 240 ] && half=240
  ok=0
  if [ "$len" -gt 0 ]; then
    [ "$len" -ge "$MINLEN" ] && [ "$CP" -ge "$half" ] && ok=1
  else
    [ "$CP" -ge 240 ] && ok=1      # unknown length: 4 min played always qualifies
  fi
  if [ "$ok" = 1 ]; then
    printf '%s\t%s\t%s\t%s\t%s\tL\t%s\t\n' \
      "$(field "$CL" 1)" "$(field "$CL" 2)" "$(field "$CL" 3)" \
      "$(field "$CL" 5)" "$len" "$CS" >> "$LOG"
    dbg "SCROBBLE played=$CP len=$len : $CL"
  else
    dbg "skip played=$CP len=$len : $CK"
  fi
  CK=""; CS=0; CP=0; CL=""
}

# user changed the clock in Settings > Date & time: fix this session's timestamps
check_clockset(){
  [ -r "$CLOCKSET" ] || return 0
  d=$(cat "$CLOCKSET" 2>/dev/null); rm -f "$CLOCKSET"
  isint "$d" || return 0
  [ "$d" = 0 ] && return 0
  LAST=$((LAST + d))
  [ -n "$CK" ] && CS=$((CS + d))
  shift_log "$d"
  save_state
  dbg "clock set in Settings: delta=${d}s, session scrobbles shifted (from line $SESSION_LINES)"
}

EMPTY=0; LAST=$(now); LTICK=""; LPID=""; POLLS=0
while :; do
  grep -q " $SD " /proc/mounts 2>/dev/null || { save_state; rm -f /tmp/scrobbler.pid; exit 0; }
  check_clockset
  POLLS=$((POLLS + 1))
  T=$(now); EL=$((T - LAST)); LAST=$T
  [ "$EL" -lt 0 ] && EL=0; [ "$EL" -gt $((POLL * 3)) ] && EL=$POLL

  key=""; MP=""
  for p in $(pidof mplayer 2>/dev/null); do key=$(open_audio "$p"); [ -n "$key" ] && { MP=$p; break; }; done
  if [ -z "$key" ]; then
    for p in $(pidof fifo-audio-app 2>/dev/null); do key=$(open_audio "$p"); [ -n "$key" ] && break; done
  fi

  # playing (not paused)?  mplayer burning CPU = decoding
  playing=1
  if [ -n "$MP" ]; then
    tk=$(cpu_ticks "$MP")
    if [ "$MP" = "$LPID" ] && isnum "$tk" && isnum "$LTICK"; then
      [ $((tk - LTICK)) -lt "$PAUSE_TICKS" ] && playing=0
    fi
    DT=""; [ "$MP" = "$LPID" ] && isnum "$tk" && isnum "$LTICK" && DT=$((tk - LTICK))
    LPID=$MP; LTICK=$tk
  fi

  if [ "$POLLS" -le "$DEBUG_POLLS" ]; then
    dbg "poll mplayer=[$(pidof mplayer 2>/dev/null)] app=[$(pidof fifo-audio-app 2>/dev/null)] ticks=$LTICK dt=$DT playing=$playing key=$key"
    if [ "$POLLS" -le 3 ]; then
      for p in $(pidof mplayer 2>/dev/null) $(pidof fifo-audio-app 2>/dev/null); do
        ls -l "/proc/$p/fd" 2>/dev/null | sed "s|^|  fd[$p] |" >> "$DBG"
      done
    fi
  fi

  if [ -n "$key" ]; then
    EMPTY=0
    if [ "$key" != "$CK" ]; then
      finish
      CK=$key; CS=$T; CP=0; CL=$(track_meta "$key")
      dbg "now playing: $CL"
      save_state
    else
      [ "$playing" = 1 ] && CP=$((CP + EL))
      # repeat-one: same file played past its length -> scrobble and restart
      len=$(field "$CL" 4)
      if isnum "$len" && [ "$len" -gt 0 ] && [ "$CP" -ge $((len + POLL * 2)) ]; then
        finish; CK=$key; CS=$T; CP=0; CL=$(track_meta "$key")
      fi
    fi
  else
    EMPTY=$((EMPTY + 1))
    [ "$EMPTY" -ge 2 ] && [ -n "$CK" ] && { finish; save_state; }   # playback stopped
  fi

  if [ $((POLLS % SAVE_EVERY)) = 0 ]; then save_state; else save_clock; fi
  sleep "$POLL"
done

fi
