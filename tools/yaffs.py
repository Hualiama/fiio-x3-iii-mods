#!/usr/bin/env python3
"""Minimal YAFFS2 reader for the FiiO X3 Mark III rootfs image.
2048-byte page + 64-byte spare, tags <seq,objid,chunkid,nbytes> LE at spare+2.
Usage: yaffs.py IMAGE OUTDIR   -> extracts files, writes OUTDIR/../chunkmap.json
"""
import struct, sys, os, json

PAGE, SPARE = 2048, 64
REC = PAGE + SPARE


def scan(img):
    n = len(img) // REC
    objs = {}      # oid -> dict(type,parent,name,size,mode,hdr_idx,link)
    chunks = {}    # oid -> {chunkid: (idx, nbytes)}
    for i in range(n):
        base = i * REC
        sp = img[base + PAGE: base + REC]
        seq, oid, cid, nb = struct.unpack_from('<IIII', sp, 2)
        if seq == 0xFFFFFFFF or oid == 0xFFFFFFFF:
            continue
        if cid == 0:
            pg = img[base: base + PAGE]
            typ, parent = struct.unpack_from('<II', pg, 0)
            name = pg[10:266].split(b'\0', 1)[0].decode('utf-8', 'replace')
            mode = struct.unpack_from('<I', pg, 268)[0]
            size = struct.unpack_from('<I', pg, 292)[0]
            alias = pg[0x130:0x130 + 160].split(b'\0', 1)[0].decode('utf-8', 'replace')
            objs[oid] = dict(type=typ, parent=parent, name=name, mode=mode,
                             size=size, hdr=i, alias=alias)
        else:
            chunks.setdefault(oid, {})[cid] = (i, nb)
    return objs, chunks


def path_of(objs, oid):
    parts = []
    seen = 0
    while oid in objs and oid != 1 and seen < 64:
        o = objs[oid]
        parts.append(o['name'])
        oid = o['parent']
        seen += 1
    return '/' + '/'.join(reversed(parts))


def main():
    img = open(sys.argv[1], 'rb').read()
    out = sys.argv[2]
    objs, chunks = scan(img)
    cmap = {}
    for oid, o in objs.items():
        p = path_of(objs, oid)
        dst = out + p
        if o['type'] == 3:
            os.makedirs(dst, exist_ok=True)
        elif o['type'] == 1:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            data = bytearray()
            for cid in sorted(chunks.get(oid, {})):
                idx, nb = chunks[oid][cid]
                data += img[idx * REC: idx * REC + nb]
            data = data[:o['size']]
            with open(dst, 'wb') as f:
                f.write(data)
            cmap[p] = dict(oid=oid, hdr=o['hdr'], size=o['size'],
                           chunks={str(c): v[0] for c, v in chunks.get(oid, {}).items()})
        elif o['type'] == 2:
            cmap[p] = dict(oid=oid, hdr=o['hdr'], symlink=o['alias'])
    json.dump(cmap, open(os.path.join(os.path.dirname(out.rstrip('/')), 'chunkmap.json'), 'w'))
    print(len(objs), 'objects,', sum(1 for v in cmap.values() if 'size' in v), 'files')


if __name__ == '__main__':
    main()
