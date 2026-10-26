#!/usr/bin/env python3
"""オフラインの自己テスト（実機不要）。

カバーする範囲:
  * usbmon テキスト/バイナリのパーサ（合成データ）
  * RX URB の分割（合成データ）
  * 802.11 フレームのパース（合成ビーコン/プローブ要求/ACK）
  * 生成済みテーブルの整合性（FW ページ長の合計、チャネル表の幅・値域）

実機のキャプチャは同梱しない（他人のネットワークのフレームを含むため）。
"""

from __future__ import annotations

import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))

import usbmon            # noqa: E402
import usbmon_bin        # noqa: E402
from rtl8812bu import ieee80211, rx  # noqa: E402
from rtl8812bu.tables import chan_table, fwdl_pages, init_seq, monitor_seq  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILED.append(name)


# ---------------------------------------------------------------- 合成データ

def beacon(ssid="TEST", channel=6) -> bytes:
    bssid = bytes.fromhex("020000000001")
    ies = bytes([0, len(ssid)]) + ssid.encode()
    ies += bytes([1, 4, 0x82, 0x84, 0x8B, 0x96])
    ies += bytes([3, 1, channel])
    body = struct.pack("<QHH", 0, 100, 0x0431) + ies
    hdr = bytes([0x80, 0x00]) + struct.pack("<H", 0) + b"\xff" * 6 + bssid + bssid + b"\x00\x00"
    return hdr + body


def urbs_with(frames: list[bytes]) -> bytes:
    out = b""
    for f in frames:
        prefix = bytearray(56)
        struct.pack_into("<H", prefix, 0, len(f))
        prefix[2] = 0x04
        prefix[3] = 0x84
        out += bytes(prefix) + f
    return out


# ---------------------------------------------------------------- テスト

def test_text_parser() -> None:
    text = "\n".join([
        "ffff0000c4ecdc00 255700505 S Co:1:003:0 s 40 05 04e0 0000 0001 1 = 35",
        "ffff0000c41a5f00 255747015 S Ci:1:003:0 s c0 05 0080 0000 0002 2 <",
        "ffff0000c41a5f00 255747900 C Ci:1:003:0 0 2 = 7840",
    ])
    recs = usbmon.parse(text)
    writes = [r for r in recs if r.is_reg_write]
    reads = [r for r in recs if r.is_reg_read]
    check("text: 書き込みを 1 件認識", len(writes) == 1)
    check("text: レジスタ/値", writes and writes[0].reg == 0x04E0 and writes[0].value == 0x35)
    check("text: 読み出しを 1 件認識", len(reads) == 1)
    check("text: 読み出し値は完了側から", reads and reads[0].value == 0x4078,
          f"got {reads[0].value if reads else None:#x}")


def test_bin_parser() -> None:
    hdr = struct.Struct("<QBBBBHBBqiiii")
    setup = bytes([0x40, 0x05, 0xE0, 0x04, 0, 0, 1, 0])
    blob = hdr.pack(1, ord("S"), 2, 0, 3, 1, 0, ord(">"), 0, 1000, 0, 1, 1) + setup + b"\x35"
    # 制御 IN: 送信側に setup、完了側にデータ
    blob += hdr.pack(2, ord("S"), 2, 0, 3, 1, 0, ord("<"), 0, 2000, 0, 2, 0)
    blob += bytes([0xC0, 0x05, 0x80, 0x00, 0, 0, 2, 0])
    blob += hdr.pack(2, ord("C"), 2, 0, 3, 1, ord("-"), ord(">"), 0, 2500, 0, 2, 2)
    blob += bytes(8) + b"\x78\x40"
    recs = usbmon_bin.parse(blob)
    check("bin: 書き込みを認識", any(r.is_reg_write and r.reg == 0x04E0 and r.value == 0x35 for r in recs))
    rd = [r for r in recs if r.is_reg_read]
    check("bin: 読み出し値が送信側に貼り戻される", rd and rd[0].value == 0x4078,
          f"got {rd[0].value if rd else None}")


def test_rx_split() -> None:
    f1 = beacon("AAA")
    f2 = beacon("BBBB")
    data = urbs_with([f1, f2])
    frames = rx.parse_urb(data)
    check("rx: 1 URB から 2 フレーム", len(frames) == 2, f"got {len(frames)}")
    check("rx: フレーム内容が一致", frames == [f1, f2])
    check("rx: 長さ 0 で停止", rx.parse_urb(b"\x00" * 60) == [])
    check("rx: 途中で切れた URB は捨てる", rx.parse_urb(urbs_with([f1]) + b"\x10" * 10) == [f1])


def test_ieee80211() -> None:
    info = ieee80211.parse(beacon("MYNET", 11))
    check("802.11: ビーコンと認識", info.get("name") == "beacon")
    check("802.11: SSID", info.get("ssid") == "MYNET")
    check("802.11: チャネル", info.get("ies", {}).get("channel") == 11)
    check("802.11: BSSID", info.get("bssid") == "02:00:00:00:00:01")

    ack = bytes.fromhex("d4000000") + b"\x02\x00\x00\x00\x00\x01" + b"\x00\x00"
    info = ieee80211.parse(ack[:10])
    check("802.11: ACK を認識", info.get("name") == "ack")

    probe = bytes([0x40, 0x00]) + b"\x00\x00" + b"\xff" * 6 + b"\x02\x11\x22\x33\x44\x55" + b"\xff" * 6 + b"\x00\x00"
    probe += bytes([0, 0])
    info = ieee80211.parse(probe)
    check("802.11: プローブ要求（SSID 空）", info.get("name") == "probe-req" and info.get("ssid") == "")


def test_tables() -> None:
    pages = fwdl_pages.PAGES
    check("表: FW ページ数が偶数（2 回分）", len(pages) % 2 == 0, f"{len(pages)}")
    check("表: ページヘッダは 48 バイト", all(len(h) == 48 for h, _ in pages))
    check("表: 先頭 2 バイトがペイロード長",
          all(struct.unpack_from("<H", h, 0)[0] == n for h, n in pages))
    check("表: ペイロード合計が 2 回分",
          fwdl_pages.TOTAL_PAYLOAD == 2 * sum(n for _, n in pages[:len(pages) // 2]))

    check("表: 初期化列に書き込みと FW がある",
          any(o[0] == "w" for o in init_seq.OPS) and any(o[0] == "fw" for o in init_seq.OPS))
    check("表: 読み出し値の幅が妥当",
          all(o[3] in (1, 2, 4) for o in init_seq.OPS if o[0] in ("w", "r")))

    check("表: FW ダウンロードの区切りが 2 個",
          sum(1 for o in init_seq.OPS if o[0] == "dl") == 2)
    check("表: 各ダウンロードのページ番号が 0 から連番",
          pages_start_at_zero(init_seq.OPS))
    check("表: モニタ切替の DOWN/UP がある",
          len(monitor_seq.DOWN) > 0 and any(o[0] == "fw" for o in monitor_seq.UP))
    check("表: モニタ切替の UP にも 1 回分の FW がある",
          sum(1 for o in monitor_seq.UP if o[0] == "fw") == len(pages) // 2)

    check("表: チャネルが 8 個", len(chan_table.CHANNELS) == 8)
    ok = True
    for ch, rows in chan_table.CHANNELS.items():
        for reg, width, value in rows:
            if width not in (1, 2, 4) or not (0 <= reg <= 0xFFFF) or value >= (1 << (8 * width)):
                ok = False
    check("表: チャネル表の幅と値域", ok)


def pages_start_at_zero(ops) -> bool:
    """'dl' の直後のページ番号が 0 で、その後 +1 ずつ増えていること。"""
    expect = None
    for op in ops:
        if op[0] == "dl":
            expect = 0
        elif op[0] == "fw":
            if expect is None or op[2] != expect:
                return False
            expect += 1
    return True


def main() -> int:
    print("usbmon テキストパーサ")
    test_text_parser()
    print("usbmon バイナリパーサ")
    test_bin_parser()
    print("RX URB の分割")
    test_rx_split()
    print("802.11 パーサ")
    test_ieee80211()
    print("生成テーブル")
    test_tables()

    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} 件 -> {FAILED}")
        return 1
    print("すべて成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
