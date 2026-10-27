#!/usr/bin/env python3
"""chan_diag.py — チャネルごとの受信統計と、設定の効き方の確認（読み取り専用）。

やること:
  1. 初期化 → モニタモード
  2. 各チャネルについて
       - チャネル切替（観測表の再生）
       - 0x0e90（観測: 下位バイトがチャネル番号）を読み、狙ったチャネルか確認
       - 一定時間受信して、フレーム数・種別・SSID を集計
       - 表に載っているレジスタを読み戻し、書いた値と一致するか確認
  3. 結果を表で表示

送信はしない（モニタ受信とレジスタ読み出しのみ）。

    python3 tools/chan_diag.py --fw /path/to/fw.bin --channels 36,44,48 --dwell 8
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from rtl8812bu import chan, fw, ieee80211, init, rx          # noqa: E402
from rtl8812bu.tables import chan_table                     # noqa: E402
from rtl8812bu.usb import Chip                              # noqa: E402

CHAN_REG = 0x0E90   # 観測: 下位バイトがチャネル番号（36→0x24, 44→0x2c, 48→0x30）


def expected_last(ch: int) -> dict[int, tuple[int, int]]:
    d: dict[int, tuple[int, int]] = {}
    for reg, width, value in chan_table.CHANNELS[ch]:
        d[reg] = (width, value)
    return d


def measure(receiver, seconds: float) -> dict:
    stats = {
        "frames": 0,
        "types": collections.Counter(),
        "ssids": collections.Counter(),
        "bssids": set(),
        "lengths": [],
    }
    end = time.time() + seconds
    while time.time() < end:
        try:
            frame = receiver.queue.get(timeout=0.15)
        except Exception:
            continue
        info = ieee80211.parse(frame)
        stats["frames"] += 1
        stats["types"][info.get("name", "?")] += 1
        stats["lengths"].append(info.get("len", 0))
        if "bssid" in info:
            stats["bssids"].add(info["bssid"])
        if info.get("name") in ("beacon", "probe-resp") and "ssid" in info:
            stats["ssids"][info["ssid"]] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fw", required=True)
    ap.add_argument("--channels", default="36,44,48")
    ap.add_argument("--dwell", type=float, default=8.0)
    ap.add_argument("--no-reset", action="store_true")
    args = ap.parse_args()

    channels = [int(x) for x in args.channels.split(",")]
    body, info = fw.load(args.fw)

    chip = Chip()
    receiver = rx.Receiver(chip)
    rows = []
    try:
        if not args.no_reset:
            chip.reset()
        init.replay(chip, body, verify=False, check_reads=False)
        init.replay_monitor(chip, body)
        receiver.start()

        for ch in channels:
            chan.set_channel(chip, ch)
            time.sleep(0.3)
            tuned = chip.read(CHAN_REG, 4)
            st = measure(receiver, args.dwell)
            # 読み戻し（受信の後に行い、受信への影響も見る）
            exp = expected_last(ch)
            mism = []
            for reg, (width, value) in sorted(exp.items()):
                try:
                    got = chip.read(reg, width)
                except Exception:
                    continue
                if got != value:
                    mism.append((reg, width, value, got))
            st2 = measure(receiver, max(2.0, args.dwell / 3))
            rows.append({
                "ch": ch,
                "tuned": tuned,
                "stats": st,
                "mismatch": mism,
                "after": st2,
            })
            print(f"ch{ch:<4d} frames={st['frames']:<5d} "
                  f"after_readback={st2['frames']:<4d} "
                  f"0x0e90=0x{tuned:08x} (ch={tuned & 0xFF}) "
                  f"reg_mismatch={len(mism)}", flush=True)
    finally:
        receiver.stop()
        init.shutdown(chip)            # モニタのまま放置すると固着することがある
        chip.close()

    print("\n=== まとめ ===")
    print(f"{'ch':>4} {'frames':>7} {'BSSID':>6} {'avg_len':>8} {'top types':<40} SSID")
    for r in rows:
        st = r["stats"]
        avg = sum(st["lengths"]) / len(st["lengths"]) if st["lengths"] else 0
        top = ", ".join(f"{k}:{v}" for k, v in st["types"].most_common(4))
        ssids = ", ".join(sorted(st["ssids"])[:4])
        print(f"{r['ch']:>4} {st['frames']:>7} {len(st['bssids']):>6} {avg:>8.0f} {top:<40} {ssids}")

    print("\n=== レジスタ読み戻しの不一致 ===")
    for r in rows:
        if not r["mismatch"]:
            print(f"ch{r['ch']}: なし（表の値をすべて読み戻せた）")
            continue
        print(f"ch{r['ch']}: {len(r['mismatch'])} 件")
        for reg, width, value, got in r["mismatch"][:8]:
            print(f"   0x{reg:04x} 期待=0x{value:0{2 * width}x} 実際=0x{got:0{2 * width}x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
