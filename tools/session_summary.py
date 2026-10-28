#!/usr/bin/env python3
"""session_summary.py — モニタ受信の JSONL を「誰が何をしたか」で集計する。

`rtl8812bu.monitor --jsonl out.jsonl` の出力を読み、
端末ごとの動き（ビーコン / プローブ要求 / 認証 / アソシエーション / データ）を
時系列と件数でまとめる。モニターの挙動を追うための道具。

    python3 tools/session_summary.py /tmp/frames.jsonl
    python3 tools/session_summary.py /tmp/frames.jsonl --mac 80:c7:55:... --timeline
"""

from __future__ import annotations

import argparse
import collections
import json
import os


def load(path: str) -> list[dict]:
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def addr_of(r: dict) -> str | None:
    """送信元（管理フレームは sa、制御フレームは ta）を推定する。"""
    return r.get("sa") or r.get("ta") or r.get("bssid")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--mac", help="この端末に絞って表示する")
    ap.add_argument("--timeline", action="store_true", help="時系列も出す")
    args = ap.parse_args()

    rows = load(args.jsonl)
    if not rows:
        print("フレームがありません")
        return 1

    t0 = min(r.get("ts", 0) for r in rows)
    dur = max(r.get("ts", 0) for r in rows) - t0

    print(f"=== 全体: {len(rows)} フレーム / {dur:.1f} 秒 ===")
    print("チャネル:", dict(collections.Counter(r.get("rx_ch") for r in rows)))
    print("種別:", ", ".join(f"{k}:{v}" for k, v in
                             collections.Counter(r.get("name") for r in rows).most_common(10)))

    # 端末ごとの内訳
    per: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for r in rows:
        a = addr_of(r)
        if a:
            per[a][r.get("name", "?")] += 1
    print(f"\n=== 端末 {len(per)} 台（送信元アドレス別） ===")
    print(f"{'addr':<20} {'total':>6}  種別")
    for a, c in sorted(per.items(), key=lambda kv: -sum(kv[1].values())):
        if args.mac and a != args.mac:
            continue
        top = ", ".join(f"{k}:{v}" for k, v in c.most_common(5))
        print(f"{a:<20} {sum(c.values()):>6}  {top}")

    # プローブ要求（セットアップ/再接続の挙動を見る）
    probes = [r for r in rows if r.get("name") == "probe-req"]
    if probes:
        print(f"\n=== プローブ要求 {len(probes)} 件 ===")
        for a, c in collections.Counter(addr_of(r) for r in probes).most_common():
            ssids = collections.Counter(r.get("ssid", "") for r in probes if addr_of(r) == a)
            print(f"  {a}: {c} 件  SSID: {dict(ssids)}")
    else:
        print("\nプローブ要求: なし")

    # 認証・アソシエーション（接続の試行）
    joins = [r for r in rows if r.get("name") in ("auth", "assoc-req", "reassoc-req")]
    if joins:
        print(f"\n=== 接続の試行 {len(joins)} 件 ===")
        for r in joins[:20]:
            print(f"  {r.get('ts', 0) - t0:7.2f}s {r.get('name'):<10} "
                  f"{addr_of(r)} -> bssid={r.get('bssid')} status={r.get('status')}")

    if args.timeline:
        print("\n=== 時系列（先頭 60 件） ===")
        for r in rows[:60]:
            print(f"  {r.get('ts', 0) - t0:8.2f}s {r.get('name', '?'):<12} "
                  f"{addr_of(r)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
