#!/usr/bin/env python3
"""gen_monitor.py — モニタモードへの切替手順を捕捉から生成する。

入力（1 手順 1 ファイル）:
    down.bin     マネージド（up）→ down
    up.bin       down → `set type monitor` → up   （モニタモードでの起動）

出力: `rtl8812bu/tables/monitor_seq.py`

    DOWN = [...]   # (reg, width, value) の列
    UP   = [...]   # ('w'|'r'|'dl'|'fw'|'gap', ...) の列（FW を含む）

`iw set type monitor` 自体は USB 転送を出さない（捕捉で 0 件）。
実際の切替は次の up で起きる。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_init          # noqa: E402
import usbmon_bin as ub  # noqa: E402

BURST_GAP_US = 300_000


def main_burst_range(recs: list) -> tuple[int, int]:
    """書き込みが連続している最大の区間 [開始, 終了] を返す（時刻は usec）。"""
    writes = [r for r in recs if r.is_reg_write]
    groups: list[list] = []
    cur: list = []
    prev = None
    for r in writes:
        if prev is not None and r.time_us - prev > BURST_GAP_US:
            if cur:
                groups.append(cur)
            cur = []
        cur.append(r)
        prev = r.time_us
    if cur:
        groups.append(cur)
    best = max(groups, key=len)
    return best[0].time_us, best[-1].time_us


def burst_writes(path: str) -> list:
    """捕捉から、最も多くの書き込みを含む連続した塊を取り出す。"""
    recs = ub.load(path)
    groups: list[list] = []
    cur: list = []
    prev = None
    for r in recs:
        if not r.is_reg_write:
            continue
        if prev is not None and r.time_us - prev > BURST_GAP_US:
            if cur:
                groups.append(cur)
            cur = []
        cur.append(r)
        prev = r.time_us
    if cur:
        groups.append(cur)
    return max(groups, key=len) if groups else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("down")
    ap.add_argument("up")
    ap.add_argument("--out", default="rtl8812bu/tables")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    down_rows = [(r.reg, len(r.data), r.value) for r in burst_writes(args.down)]

    up_recs = ub.load(args.up)
    start, end = main_burst_range(up_recs)
    # 主要な塊の外（数百 ms 後に周期的に出る背景書き込み）は表に入れない
    core = [r for r in up_recs if start <= r.time_us <= end]
    ops, _ = gen_init.build(core)   # (kind, [phase, ...]) の列

    path = os.path.join(args.out, "monitor_seq.py")
    with open(path, "w") as f:
        f.write('"""自動生成 — 手で編集しない（tools/gen_monitor.py）。\n\n')
        f.write("マネージド（up）→ モニタ（up）へ切り替えるときに観測された転送列。\n")
        for name, p in (("down", args.down), ("up", args.up)):
            d = hashlib.sha256(open(p, "rb").read()).hexdigest()
            f.write(f"観測元 {name}: {os.path.basename(p)} sha256={d}\n")
        f.write(f"生成: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write('"""\n\n')
        f.write("# (reg, width, value)\n")
        f.write("DOWN = [\n")
        for reg, width, value in down_rows:
            f.write(f"    (0x{reg:04x}, {width}, 0x{value:0{2 * width}x}),\n")
        f.write("]\n\n")
        f.write("# ('w', reg, width, value) / ('r', reg, width, value)\n")
        f.write("# ('dl',) ダウンロード開始 / ('fw', page, len, header) / ('gap', ms)\n")
        f.write("UP = [\n")
        for kind, v in ops:
            v = v[1:]   # phase を落とす
            if kind == "w":
                f.write(f"    ('w', 0x{v[0]:04x}, {v[1]}, 0x{v[2]:0{2 * v[1]}x}),\n")
            elif kind == "r":
                f.write(f"    ('r', 0x{v[0]:04x}, {v[1]}, 0x{v[2]:0{2 * v[1]}x}),\n")
            elif kind == "fw":
                f.write(f"    ('fw', {v[0]}, {v[1]}, '{v[2]}'),\n")
            elif kind == "dl":
                f.write("    ('dl',),\n")
            elif kind == "bulk":
                f.write(f"    ('bulk', '{v[0]}'),\n")
            elif kind == "gap":
                f.write(f"    ('gap', {v[0]}),\n")
        f.write("]\n")

    print(f"DOWN: {len(down_rows)} writes")
    print(f"UP:   区間 {(end - start) / 1e6:.1f}s / {len(core)} records")
    print(f"UP:   {len(ops)} ops "
          f"({sum(1 for k, _ in ops if k == 'w')} writes, "
          f"{sum(1 for k, _ in ops if k == 'fw')} fw pages)")
    print(f"-> {path}")


if __name__ == "__main__":
    main()
