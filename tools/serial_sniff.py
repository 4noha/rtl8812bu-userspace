#!/usr/bin/env python3
"""serial_sniff.py — ESP32 スニファの出力を読んで 802.11 フレームを解析する。

`tools/esp32-sniffer/esp32-sniffer.ino` が USB シリアルに流す

    <ch> <rssi> <hex>

を読み、管理フレームを種別ごとに集計する。FCS（末尾 4 バイト）は自動で落とす。

    python3 tools/serial_sniff.py /dev/cu.usbserial-1110 30
    python3 tools/serial_sniff.py /dev/cu.usbserial-1110 30 --jsonl /tmp/esp.jsonl

`--jsonl` は本リポジトリの他ツール（session_summary.py など）と同じ形式で出す。
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from rtl8812bu import ieee80211  # noqa: E402


def frames_from_line(line: str) -> dict | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split()
    if len(parts) != 3:
        return None
    try:
        ch = int(parts[0])
        rssi = int(parts[1])
        raw = bytes.fromhex(parts[2])
    except ValueError:
        return None
    if len(raw) < 28:
        return None
    frame = raw[:-4]          # FCS を落とす
    info = ieee80211.parse(frame)
    info["rx_ch"] = ch
    info["rssi"] = rssi
    info["ts"] = time.time()
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("seconds", type=float, nargs="?", default=30.0)
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--jsonl")
    args = ap.parse_args()

    try:
        import serial  # pyserial
    except ImportError:
        print("pyserial が必要です: python3 -m pip install pyserial", file=sys.stderr)
        return 2

    try:
        port = serial.Serial(args.port, args.baud, timeout=0.5)
    except Exception as exc:
        print(f"ポートを開けません（{args.port}）: {exc}", file=sys.stderr)
        return 3

    out = open(args.jsonl, "w") if args.jsonl else None
    stats = collections.Counter()
    ssids = collections.Counter()
    t0 = time.time()
    print(f"# {args.port} @ {args.baud} を {args.seconds:.0f} 秒読みます", file=sys.stderr)
    try:
        while time.time() - t0 < args.seconds:
            line = port.readline().decode("utf-8", "replace")
            info = frames_from_line(line)
            if info is None:
                continue
            stats[info.get("name", "?")] += 1
            if info.get("name") in ("beacon", "probe-resp") and "ssid" in info:
                ssids[(info["name"], info["ssid"], info.get("bssid"))] += 1
            if out:
                out.write(json.dumps(info, ensure_ascii=False, default=str) + "\n")
    except KeyboardInterrupt:
        pass
    finally:
        port.close()
        if out:
            out.close()

    print(f"\n=== 受信 {sum(stats.values())} フレーム ===")
    for k, v in stats.most_common(12):
        print(f"  {k:<12} {v}")
    if ssids:
        print("\n=== beacon / probe-resp ===")
        for (name, ssid, bssid), v in ssids.most_common(10):
            print(f"  {name:<11} {bssid}  ssid={ssid!r}  {v} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
