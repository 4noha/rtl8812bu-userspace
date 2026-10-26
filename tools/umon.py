#!/usr/bin/env python3
"""umon.py — Linux 上で usbmon のバイナリ捕捉を簡易形式で書き出す（VM/実機用）。

usbmon のテキスト API（`Nu`）は 1 レコードあたり 32 バイトで切られるため、
ファームウェアのバルク転送や RX フレームの全体が必要なときはこちらを使う。

使い方（root）:
    sudo python3 tools/umon.py /dev/usbmon1 /tmp/capture.bin 20

出力フォーマット（1 レコード = 40 バイトヘッダ + 8 バイト setup + データ）:

    '<QBBBBHBBqiiii'  # id, type, xfer, epnum, devnum, busnum,
                      # flag_setup, flag_data, ts_sec, ts_usec,
                      # status, len_urb, len_cap

`tools/usbmon_bin.py` が読み側。データ長は `len_cap`（flag_data が '-'
のときはデータ無し）。
"""

import os
import select
import struct
import sys
import time

HDR = struct.Struct("<QBBBBHBBqiiii")


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    path, out, dur = sys.argv[1], sys.argv[2], float(sys.argv[3])

    fd = os.open(path, os.O_RDONLY)
    outf = open(out, "wb", buffering=1 << 20)
    end = time.time() + dur
    buf = b""
    n = 0

    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.5)
        if not r:
            continue
        buf += os.read(fd, 1 << 20)
        off = 0
        while off + 48 <= len(buf):
            (uid, typ, xfer, epnum, devnum, busnum, fs, fdat,
             tsec, tusec, status, len_urb, len_cap) = HDR.unpack_from(buf, off)
            dlen = len_cap if fdat != ord("-") else 0
            if off + 48 + dlen > len(buf):
                break
            setup = buf[off + 40:off + 48]
            data = buf[off + 48:off + 48 + dlen]
            outf.write(HDR.pack(uid, typ, xfer, epnum, devnum, busnum, fs, fdat,
                                tsec, tusec, status, len_urb, len_cap))
            outf.write(setup)
            outf.write(data)
            off += 48 + dlen
            n += 1
        buf = buf[off:]

    outf.close()
    print(f"records {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
