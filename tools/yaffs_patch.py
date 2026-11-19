#!/usr/bin/env python3
"""Surgically replace file contents inside the FiiO YAFFS2 rootfs image.
Only the target files' data chunks + header fileSize change; every other byte is kept.
The new content must need the same number of 2048-byte chunks as the old one.
usage: yaffs_patch.py IN.yaffs2 OUT.yaffs2 CHUNKMAP.json /path/in/rootfs=localfile [...]
"""
import sys, json, struct

PAGE, REC = 2048, 2112


def main():
    img = bytearray(open(sys.argv[1], 'rb').read())
    cm = json.load(open(sys.argv[3]))
    for spec in sys.argv[4:]:
        path, local = spec.split('=', 1)
        e = cm[path]
        new = open(local, 'rb').read()
        chunks = {int(k): v for k, v in e['chunks'].items()}
        need = (len(new) + PAGE - 1) // PAGE
        assert sorted(chunks) == list(range(1, len(chunks) + 1)), 'non-contiguous chunk ids'
        assert need == len(chunks), f'{path}: needs {need} chunks, image has {len(chunks)}'
        for cid, idx in chunks.items():
            part = new[(cid - 1) * PAGE: cid * PAGE]
            base = idx * REC
            img[base: base + PAGE] = part + b'\xff' * (PAGE - len(part))
            seq, oid, c, nb = struct.unpack_from('<IIII', img, base + PAGE + 2)
            assert oid == e['oid'] and c == cid
            struct.pack_into('<I', img, base + PAGE + 2 + 12, len(part))
        h = e['hdr'] * REC
        struct.pack_into('<I', img, h + 292, len(new))
        print(f'{path}: {e["size"]} -> {len(new)} bytes, {len(chunks)} chunks rewritten')
    open(sys.argv[2], 'wb').write(img)


if __name__ == '__main__':
    main()
