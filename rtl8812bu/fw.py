"""ファームウェアのダウンロード（観測ベース）。

観測から分かっていること:

  * FW ファイル（linux-firmware の `rtw88/rtw8822b_fw.bin`）の先頭 64 バイトは
    ヘッダで、ドライバはこれを送らず **65 バイト目以降**を送る。
  * 転送は「48 バイトのヘッダ + 最大 4096 バイトのペイロード」の繰り返し。
    ヘッダの先頭 2 バイトはペイロード長（リトルエンディアン）。
  * 1 回の起動で FW ダウンロードは 2 回行われる（probe 時と start 時）。

FW 本体はこのリポジトリに含めない（Realtek の配布物であり MIT ではない）。
利用者が自分の環境のファイルを指定する。
"""

from __future__ import annotations

import hashlib
import os

FW_MAGIC = b"\x22\x88"      # 観測: FW ファイル先頭のシグネチャ
FW_HEADER_LEN = 64          # 観測: ドライバが読み飛ばすヘッダ長
PAGE_HEADER_LEN = 48        # 観測: 転送ごとのヘッダ長

# 観測: ペイロード連結列の 11217 バイト目に 0x00 が 1 バイト挿入されていた。
# （ページ境界の都合で生じたもの。None にすると挿入しない。）
PAD_OFFSET: int | None = 11217

# 既知の FW（同じ個体・同じ配布物かどうかの確認用。必須ではない）
KNOWN_SHA256 = "a72da690597bfa99d8eb6fc2ab090d18d8ad92ac2befd35db1c9e3662d8d8418"


class FirmwareError(RuntimeError):
    pass


def load(path: str) -> tuple[bytes, dict]:
    """FW ファイルを読み、送信対象（ヘッダ 64 バイトを除いた本体）を返す。"""
    if not os.path.exists(path):
        raise FirmwareError(
            f"FW が見つかりません: {path}\n"
            "  linux-firmware の rtw88/rtw8822b_fw.bin を用意して --fw で指定してください。\n"
            "  （このリポジトリは FW を同梱しません）"
        )
    blob = open(path, "rb").read()
    if blob[:2] != FW_MAGIC:
        raise FirmwareError(
            f"FW のシグネチャが想定外です: {blob[:2].hex()} (期待 {FW_MAGIC.hex()})"
        )
    info = {
        "path": path,
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "version": f"{blob[4]}.{blob[6]}",   # 観測: byte4/6 が 30.20（16bit フィールド）
        "matches_known": hashlib.sha256(blob).hexdigest() == KNOWN_SHA256,
    }
    return blob[FW_HEADER_LEN:], info


def build_stream(body: bytes) -> bytes:
    """転送するペイロード列を組み立てる（観測された並びを再現）。"""
    if PAD_OFFSET is None:
        return body
    return body[:PAD_OFFSET] + b"\x00" + body[PAD_OFFSET:]


class Downloader:
    """1 回分の FW ダウンロード状態（ページ列を順に送る）。"""

    def __init__(self, chip, body: bytes, pages, log=None):
        self.chip = chip
        self.stream = build_stream(body)
        self.pages = pages
        self.offset = 0
        self.index = 0
        self.log = log or (lambda *_: None)

    def reset(self) -> None:
        """新しいダウンロードの開始（送信位置を先頭に戻す）。"""
        self.offset = 0
        self.index = 0

    def send_page(self, page_idx: int, payload_len: int, header: bytes) -> None:
        payload = self.stream[self.offset:self.offset + payload_len]
        if len(payload) < payload_len:
            payload = payload + b"\x00" * (payload_len - len(payload))
        self.offset += payload_len
        self.chip.bulk_out(header + payload)
        self.index += 1

    def done(self) -> bool:
        return self.offset >= len(self.stream)
