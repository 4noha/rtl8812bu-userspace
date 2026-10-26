#!/usr/bin/env python3
"""diff_trace.py — 2 つの usbmon バイナリ捕捉の「書き込み列」を比較する。

同じ初期状態から取った 2 本（Linux ドライバ / 自前実装）を比べ、
最初に食い違う位置と、そこまでの件数を出す。

    python3 tools/diff_trace.py /tmp/ref.bin /tmp/ours.bin
    python3 tools/diff_trace.py ref.bin ours.bin --context 5 --ignore-bulk
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usbmon_bin as ub  # noqa: E402


def writes(path: str) -> list[tuple[int, int, int]]:
    recs = ub.load(path)
    return [(r.reg, len(r.data), r.value) for r in recs if r.is_reg_write]


def bulk_sizes(path: str) -> list[int]:
    return [len(r.data) for r in ub.load(path) if r.is_bulk_out]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("ours")
    ap.add_argument("--context", type=int, default=6)
    ap.add_argument("--ignore-bulk", action="store_true")
    args = ap.parse_args()

    a = writes(args.ref)
    b = writes(args.ours)
    print(f"基準 {args.ref}: {len(a)} writes")
    print(f"比較 {args.ours}: {len(b)} writes")

    n = min(len(a), len(b))
    first = next((i for i in range(n) if a[i] != b[i]), None)

    if first is None and len(a) == len(b):
        print("書き込み列は完全一致")
    elif first is None:
        print(f"先頭 {n} 件は一致。件数が違う（差 {len(b) - len(a):+d}）")
        first = n
    else:
        print(f"最初の不一致: {first} 件目")
        lo = max(0, first - args.context)
        hi = min(n, first + args.context + 1)
        print(f"{'idx':>6} {'基準':<22} {'比較':<22}")
        for i in range(lo, hi):
            mark = "  <--" if i == first else ""
            print(f"{i:6d} {fmt(a[i]):<22} {fmt(b[i]):<22}{mark}")

    if not args.ignore_bulk:
        ba, bb = bulk_sizes(args.ref), bulk_sizes(args.ours)
        print(f"\nバルク OUT: 基準 {len(ba)} 件 / 比較 {len(bb)} 件")
        if ba == bb:
            print("  転送サイズ列は一致")
        else:
            i = next((k for k in range(min(len(ba), len(bb))) if ba[k] != bb[k]), None)
            if i is None:
                print(f"  先頭 {min(len(ba), len(bb))} 件は一致、件数が違う")
            else:
                print(f"  最初の不一致: {i} 件目 基準={ba[i]} 比較={bb[i]}")

    return 0 if first is None else 1


def fmt(row: tuple[int, int, int]) -> str:
    reg, width, value = row
    return f"0x{reg:04x} <- 0x{value:0{2 * width}x} ({width}B)"


if __name__ == "__main__":
    raise SystemExit(main())
