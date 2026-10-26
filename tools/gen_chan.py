#!/usr/bin/env python3
"""gen_chan.py — チャネル切替の USB 捕捉からチャネル別の設定列を生成する。

入力: 「基準チャネルに合わせる → 捕捉開始 → 目的チャネルへ切り替える」を
      1 チャネルずつ繰り返して取ったバイナリ捕捉ディレクトリ
      （ファイル名 `ch<番号>.bin`）。

出力: `rtl8812bu/tables/chan_table.py`

    CHANNELS = { <ch>: [(reg, width, value), ...], ... }

方針: 「そのチャネルへ切り替えたときに、どのレジスタへ何を書いたか」だけを記録する。
レジスタの意味やチャネル式の推定はしない。
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usbmon_bin as ub  # noqa: E402

# 切替シーケンスの内部に現れる無通信区間（最大でも数百 ms）より十分大きい値。
BURST_GAP_US = 500_000


def burst(writes: list) -> list:
    """書き込み列から、最も多くの書き込みを含む連続した塊を取り出す。"""
    groups: list[list] = []
    cur: list = []
    prev = None
    for w in writes:
        if prev is not None and w.time_us - prev > BURST_GAP_US:
            if cur:
                groups.append(cur)
            cur = []
        cur.append(w)
        prev = w.time_us
    if cur:
        groups.append(cur)
    return max(groups, key=len) if groups else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--out", default="rtl8812bu/tables")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "ch*.bin")),
                   key=lambda p: int(re.search(r"ch(\d+)", p).group(1)))

    table: dict[int, list] = {}
    digests: dict[int, str] = {}
    for path in files:
        ch = int(re.search(r"ch(\d+)", path).group(1))
        blob = open(path, "rb").read()
        digests[ch] = hashlib.sha256(blob).hexdigest()[:16]
        recs = ub.parse(blob)
        rows = burst([r for r in recs if r.is_reg_write])
        table[ch] = [(r.reg, len(r.data), r.value) for r in rows]
        print(f"  ch{ch:<4d} {len(rows):4d} writes  (capture {len(recs)} records)")

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "chan_table.py")
    with open(path, "w") as f:
        f.write('"""自動生成 — 手で編集しない（tools/gen_chan.py）。\n\n')
        f.write("各チャネルへ切り替えたときに観測された書き込み列（基準チャネルからの遷移）。\n")
        f.write("観測元の SHA256（先頭 16 桁）:\n")
        for ch, d in sorted(digests.items()):
            f.write(f"  ch{ch}: {d}\n")
        f.write(f"生成: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write('"""\n\n')
        f.write("CHANNELS = {\n")
        for ch, rows in sorted(table.items()):
            f.write(f"    {ch}: [  # {len(rows)} writes\n")
            for reg, width, val in rows:
                f.write(f"        (0x{reg:04x}, {width}, 0x{val:0{2 * width}x}),\n")
            f.write("    ],\n")
        f.write("}\n")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
