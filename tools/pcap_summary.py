#!/usr/bin/env python3
"""pcap_summary.py — 802.11 の pcap（radiotap）を種別ごとに集計する。

Linux ドライバで取った捕捉を「フレーム種別の内訳」で見るための道具。
自前実装の出力（--jsonl）と突き合わせれば、受信内容が同じかを比較できる。

    python3 tools/pcap_summary.py /tmp/mon6.pcap
    python3 tools/pcap_summary.py /tmp/gt/*.pcap
"""

from __future__ import annotations

import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rtl8812bu import ieee80211   # noqa: E402


def read_pcap(path: str):
    d = open(path, "rb").read()
    if len(d) < 24:
        return
    magic = d[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        end = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        end = ">"
    else:
        raise ValueError(f"pcap ではない: {path}")
    linktype = struct.unpack_from(end + "I", d, 20)[0]
    off = 24
    while off + 16 <= len(d):
        ts_s, ts_us, incl, orig = struct.unpack_from(end + "IIII", d, off)
        off += 16
        pkt = d[off:off + incl]
        off += incl
        if linktype == 127:            # radiotap
            rtlen = struct.unpack_from("<H", pkt, 2)[0]
            frame = pkt[rtlen:]
        elif linktype == 105:          # ieee80211 (FCS 付きのことがある)
            frame = pkt
        else:
            continue
        yield frame


def summarise(path: str) -> dict:
    types = collections.Counter()
    ssids = collections.Counter()
    bssids = set()
    n = 0
    for frame in read_pcap(path):
        info = ieee80211.parse(frame)
        n += 1
        types[info.get("name", "?")] += 1
        if "bssid" in info:
            bssids.add(info["bssid"])
        if info.get("name") in ("beacon", "probe-resp") and "ssid" in info:
            ssids[info["ssid"]] += 1
    return {"frames": n, "types": types, "bssids": bssids, "ssids": ssids}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    for path in argv:
        s = summarise(path)
        name = os.path.basename(path)
        print(f"=== {name}: {s['frames']} frames, {len(s['bssids'])} BSSID ===")
        print("  種別:", ", ".join(f"{k}:{v}" for k, v in s["types"].most_common()))
        ctrl = sum(v for k, v in s["types"].items()
                   if k in ("rts", "cts", "ack", "ba", "bar", "ps-poll"))
        data = sum(v for k, v in s["types"].items() if k.startswith(("data", "qos")))
        print(f"  beacon={s['types'].get('beacon', 0)} data系={data} 制御系={ctrl}")
        if s["ssids"]:
            print("  SSID:", ", ".join(f"{k}({v})" for k, v in s["ssids"].most_common(6)))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
