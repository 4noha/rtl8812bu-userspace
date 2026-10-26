#!/usr/bin/env python3
"""gen_init.py — usbmon バイナリ捕捉から初期化シーケンスを生成する。

入力は `tools/umon.py` が書き出したバイナリ捕捉（`docs/capture.md` 参照）。
出力は `rtl8812bu/tables/init_seq.py` と `rtl8812bu/tables/fwdl_pages.py`。

方針（クリーンルーム）:
  * 参照するのは「バス上で観測された順序と値」だけ。
  * ドライバのソース・表・命名は一切参照しない。
  * 生成物は機械変換のみ。手で値を足さない。

使い方:
    python3 tools/gen_init.py /path/to/full.bin --out rtl8812bu/tables
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usbmon_bin as ub  # noqa: E402

# 「初期化」と「リンクアップ」の区切りは、捕捉中の長い無通信区間で判定する。
GAP_SPLIT_US = 1_000_000

# 通知用の待ちを入れる閾値（観測上これ以上空いていたら実装でも待つ）
GAP_EMIT_US = 20_000


def build(records: list[ub.URec]):
    """観測レコード列を (phase, op) の列に変換する。"""
    ops: list[tuple[str, list]] = []
    phases: list[int] = []
    phase = 0
    prev_t = None
    page_in_dl = 0

    for r in records:
        if prev_t is not None and r.time_us - prev_t > GAP_SPLIT_US:
            phase += 1
        if prev_t is not None and r.time_us - prev_t > GAP_EMIT_US:
            ops.append(("gap", [phase, int((r.time_us - prev_t) / 1000)]))
            phases.append(phase)
        prev_t = r.time_us

        if r.is_reg_write:
            # 0x0080 に 0x0001（2 バイト）を書くのが FW ダウンロードの開始合図
            if r.reg == 0x0080 and r.value == 0x0001 and len(r.data) == 2:
                ops.append(("dl", [phase]))
                page_in_dl = 0
            ops.append(("w", [phase, r.reg, len(r.data), r.value]))
        elif r.is_reg_read and r.data:
            # 読み出しも記録する（再生時に実機の値と比較して状態のずれを検出できる）
            ops.append(("r", [phase, r.reg, len(r.data), r.value]))
        elif r.is_bulk_out and len(r.data) >= 48:
            # 先頭 48 バイトは転送ごとのヘッダ（長さ + 固定パターン）
            hdr = r.data[:48]
            payload_len = len(r.data) - 48
            ops.append(("fw", [phase, page_in_dl, payload_len, hdr.hex()]))
            page_in_dl += 1
    return ops, phase + 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--out", default="rtl8812bu/tables")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    blob = open(args.capture, "rb").read()
    digest = hashlib.sha256(blob).hexdigest()
    records = ub.parse(blob)
    ops, nphase = build(records)

    os.makedirs(args.out, exist_ok=True)

    writes = sum(1 for k, _ in ops if k == "w")
    fw_pages = [v for k, v in ops if k == "fw"]
    gaps = sum(1 for k, _ in ops if k == "gap")

    with open(os.path.join(args.out, "init_seq.py"), "w") as f:
        f.write('"""自動生成 — 手で編集しない（tools/gen_init.py）。\n\n')
        f.write(f"観測元: {os.path.basename(args.capture)}\n")
        f.write(f"SHA256: {digest}\n")
        f.write(f"生成: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"レコード数: {len(records)} / 書き込み {writes} / FW ページ {len(fw_pages)} / 区間 {nphase}\n")
        if args.note:
            f.write(f"注記: {args.note}\n")
        f.write('"""\n\n')
        f.write("# ('dl',  phase)  FW ダウンロード開始（ここで送信位置がリセットされる）\n")
        f.write("# ('w',   phase, reg, width, value)  レジスタ書き込み\n")
        f.write("# ('r',   phase, reg, width, value)  レジスタ読み出し（観測値）\n")
        f.write("# ('fw',  phase, page_idx, payload_len, header_hex48)\n")
        f.write("# ('gap', phase, ms)\n")
        f.write("OPS = [\n")
        for kind, v in ops:
            if kind == "dl":
                f.write(f"    ('dl', {v[0]}),\n")
            elif kind == "w":
                f.write(f"    ('w', {v[0]}, 0x{v[1]:04x}, {v[2]}, 0x{v[3]:0{2 * v[2]}x}),\n")
            elif kind == "r":
                f.write(f"    ('r', {v[0]}, 0x{v[1]:04x}, {v[2]}, 0x{v[3]:0{2 * v[2]}x}),\n")
            elif kind == "fw":
                f.write(f"    ('fw', {v[0]}, {v[1]}, {v[2]}, '{v[3]}'),\n")
            else:
                f.write(f"    ('gap', {v[0]}, {v[1]}),\n")
        f.write("]\n")

    # FW ページ表（ヘッダのみ。ペイロードは FW 本体ファイルから読む）
    with open(os.path.join(args.out, "fwdl_pages.py"), "w") as f:
        f.write('"""自動生成 — 手で編集しない（tools/gen_init.py）。\n\n')
        f.write("FW ダウンロード時の転送ヘッダ（観測値）とペイロード長のみを持つ。\n")
        f.write("FW 本体（Realtek のバイナリ）はこのリポジトリに含めない。\n")
        f.write('"""\n\n')
        f.write("# (header48, payload_len)\n")
        f.write("PAGES = [\n")
        for _, page_idx, payload_len, hdr in fw_pages:
            f.write(f"    ({bytes.fromhex(hdr)!r}, {payload_len}),\n")
        f.write("]\n")
        f.write(f"\n# 観測されたペイロード合計 = {sum(p[3] for _, p in ops if _ == 'fw')} bytes\n" if False else "")
        f.write(f"TOTAL_PAYLOAD = {sum(v[2] for k, v in ops if k == 'fw')}\n")

    print(f"phases: {nphase} / writes: {writes} / fw pages: {len(fw_pages)} / gaps: {gaps}")
    print(f"fw payload total: {sum(v[2] for k, v in ops if k == 'fw')} bytes")
    print(f"-> {args.out}/init_seq.py, {args.out}/fwdl_pages.py")


if __name__ == "__main__":
    main()
