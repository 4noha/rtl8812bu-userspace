#!/usr/bin/env python3
"""usbmon.py — Linux usbmon テキストダンプのパーサ（クリーンルーム入力層）

usbmon のテキスト形式（`/sys/kernel/debug/usb/usbmon/Nu`）は次の形をしている。

    <tag> <time> <event> <addr> [<flags>] <length> [<data-tag>] [= <data>]

例（制御 OUT・ベンダ要求 0x05 = レジスタ書き込み）:

    ffff0000c4ecdc00 255700505 S Co:1:003:0 s 40 05 04e0 0000 0001 1 = 35

例（制御 IN・レジスタ読み出し。データは完了行に出る）:

    ffff0000c41a5f00 255747015 S Ci:1:003:0 s c0 05 0080 0000 0002 2 <
    ffff0000c41a5f00 255747900 C Ci:1:003:0 0 2 = 0100

このモジュールは「観測された事実」だけを取り出す。意味付けはしない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- データ型

VENDOR_WRITE = 0x40  # bmRequestType: vendor, host->device
VENDOR_READ = 0xC0   # bmRequestType: vendor, device->host
REQ_REG = 0x05       # bRequest: レジスタアクセス

_XFER = {"C": "control", "B": "bulk", "I": "interrupt"}


@dataclass
class Record:
    """デバイスへ向かう 1 転送（submission 単位でまとめたもの）。"""

    tag: str
    time_us: int
    kind: str            # 'control' | 'bulk' | 'interrupt'
    ep: str              # '1:003:0' など
    direction: str       # 'in' | 'out'
    bm: int | None = None
    breq: int | None = None
    wvalue: int | None = None
    windex: int | None = None
    wlength: int | None = None
    data: bytes = b""

    # ---- 便宜的な判定 ------------------------------------------------
    @property
    def is_reg_write(self) -> bool:
        return (
            self.kind == "control"
            and self.bm == VENDOR_WRITE
            and self.breq == REQ_REG
        )

    @property
    def is_reg_read(self) -> bool:
        return (
            self.kind == "control"
            and self.bm == VENDOR_READ
            and self.breq == REQ_REG
        )

    @property
    def reg(self) -> int:
        return self.wvalue or 0

    @property
    def value(self) -> int:
        """書き込みデータをリトルエンディアンで整数化。"""
        v = 0
        for i, b in enumerate(self.data[:4]):
            v |= b << (8 * i)
        return v

    def summary(self) -> str:
        if self.is_reg_write:
            return f"W 0x{self.reg:04x} <- 0x{self.value:0{2 * len(self.data)}x}"
        if self.is_reg_read:
            return f"R 0x{self.reg:04x} -> 0x{self.value:0{2 * len(self.data)}x}"
        if self.kind == "bulk":
            return f"BULK {self.direction} {len(self.data)}B"
        return f"? bm=0x{self.bm or 0:02x} req=0x{self.breq or 0:02x}"


# ---------------------------------------------------------------- パーサ

_TAG = re.compile(r"^[0-9a-f]{8,}$")


def _hexbytes(tokens: list[str]) -> bytes:
    out = bytearray()
    for tok in tokens:
        # データは 2 桁区切りで空白で並ぶが、4 桁連結で出る場合もある
        if len(tok) % 2:
            return b""
        try:
            out += bytes.fromhex(tok)
        except ValueError:
            return b""
    return bytes(out)


def parse(text: str) -> list[Record]:
    """usbmon テキストを Record の列に変換する（送信方向のみ・時系列順）。"""
    records: list[Record] = []
    pending: dict[str, Record] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] == "#":
            continue
        tok = line.split()
        if len(tok) < 4 or not _TAG.match(tok[0]):
            continue

        tag, time_us, event, addr = tok[0], tok[1], tok[2], tok[3]
        m = re.match(r"^([A-Z])([a-z]):(\d+):(\d+):(\d+)$", addr)
        if not m:
            continue
        xfer = _XFER.get(m.group(1), "?")
        direction = "in" if m.group(2) == "i" else "out"
        ep = f"{m.group(3)}:{m.group(4)}:{m.group(5)}"

        if event == "S":
            rec = Record(tag=tag, time_us=int(time_us), kind=xfer,
                         ep=ep, direction=direction)
            i = 4  # 0..3 は tag/time/event/addr
            if xfer == "control" and tok[i] == "s":
                vals = tok[i + 1:i + 7]  # bm req wValue wIndex wLength dataLen
                rec.bm = int(vals[0], 16)
                rec.breq = int(vals[1], 16)
                rec.wvalue = int(vals[2], 16)
                rec.windex = int(vals[3], 16)
                rec.wlength = int(vals[4], 16)
                rest = tok[i + 7:]
                if rest and rest[0] == "=":
                    rec.data = _hexbytes(rest[1:])
            else:
                if tok[i].startswith("-"):   # flags
                    i += 1
                if i < len(tok) and tok[i].isdigit():
                    i += 1
                rest = tok[i:]
                if rest and rest[0] == "=":
                    rec.data = _hexbytes(rest[1:])
            pending[tag] = rec
            records.append(rec)

        elif event == "C":
            rec = pending.pop(tag, None)
            if rec is None:
                continue
            i = 5
            if rec.kind != "control":
                if tok[i].startswith("-"):
                    i += 1
            if i < len(tok) and tok[i].isdigit():
                i += 1
            rest = tok[i:]
            if rest and rest[0] == "=" and not rec.data:
                rec.data = _hexbytes(rest[1:])

    return records


def load(path: str) -> list[Record]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse(fh.read())


# ---------------------------------------------------------------- 補助

def writes(records: list[Record]) -> list[Record]:
    return [r for r in records if r.is_reg_write]


def reads(records: list[Record]) -> list[Record]:
    return [r for r in records if r.is_reg_read]


def bulk(records: list[Record], direction: str = "in") -> list[Record]:
    return [r for r in records if r.kind == "bulk" and r.direction == direction]
