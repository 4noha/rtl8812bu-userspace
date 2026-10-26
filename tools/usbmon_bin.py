#!/usr/bin/env python3
"""usbmon_bin.py — usbmon バイナリ捕捉のパーサ（クリーンルーム入力層）

`tools/umon.py`（VM 側で実行）が書き出した簡易バイナリストリームを読む。
フォーマットは 1 レコード = 固定 40 バイトのヘッダ + 8 バイトの setup + データ。

    '<QBBBBHBBqiiii'  # id, type, xfer, epnum, devnum, busnum,
                      # flag_setup, flag_data, ts_sec, ts_usec,
                      # status, len_urb, len_cap

値の意味（カーネルの mon_bin_hdr と同じ）:

    type       'S' = 送信（submission）, 'C' = 完了（completion）
    xfer       0=iso, 1=interrupt, 2=control, 3=bulk
    epnum      エンドポイント番号（bit7 が立っていれば IN）
    flag_setup 0    = setup パケットを含む制御転送
               '-'  = setup なし
    flag_data  '<'  = データは後続（制御 IN）
               '>'  = データは後続（制御 OUT / IN 完了）
               '-'  = データなし

テキスト形式（`Nu`）は 32 バイトで切られるため、完全なデータが必要な場合は
こちらを使う。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

HDR = struct.Struct("<QBBBBHBBqiiii")

XFER = {0: "iso", 1: "interrupt", 2: "control", 3: "bulk"}


@dataclass
class URec:
    tag: int
    event: str          # 'S' | 'C' | 'E'
    xfer: str           # 'control' | 'bulk' | ...
    epnum: int          # 生の値（bit7 = IN）
    busnum: int
    devnum: int
    status: int
    time_us: int
    setup: bytes = b""
    data: bytes = b""

    # ---- 制御転送の分解 ---------------------------------------------
    @property
    def bm(self) -> int:
        return self.setup[0] if len(self.setup) == 8 else -1

    @property
    def breq(self) -> int:
        return self.setup[1] if len(self.setup) == 8 else -1

    @property
    def wvalue(self) -> int:
        return struct.unpack_from("<H", self.setup, 2)[0] if len(self.setup) == 8 else -1

    @property
    def windex(self) -> int:
        return struct.unpack_from("<H", self.setup, 4)[0] if len(self.setup) == 8 else -1

    @property
    def wlength(self) -> int:
        return struct.unpack_from("<H", self.setup, 6)[0] if len(self.setup) == 8 else -1

    @property
    def is_in(self) -> bool:
        return bool(self.epnum & 0x80)

    @property
    def ep(self) -> int:
        return self.epnum & 0x7F

    # ---- 判定 --------------------------------------------------------
    @property
    def is_reg_write(self) -> bool:
        return (self.event == "S" and self.xfer == "control"
                and self.bm == 0x40 and self.breq == 0x05)

    @property
    def is_reg_read(self) -> bool:
        return (self.event == "S" and self.xfer == "control"
                and self.bm == 0xC0 and self.breq == 0x05)

    @property
    def is_bulk_out(self) -> bool:
        return self.event == "S" and self.xfer == "bulk" and not self.is_in

    @property
    def is_bulk_in_done(self) -> bool:
        return (self.event == "C" and self.xfer == "bulk"
                and self.is_in and bool(self.data))

    @property
    def reg(self) -> int:
        return self.wvalue

    @property
    def value(self) -> int:
        v = 0
        for i, b in enumerate(self.data[:4]):
            v |= b << (8 * i)
        return v

    def summary(self) -> str:
        if self.is_reg_write:
            return f"W 0x{self.reg:04x} <- 0x{self.value:0{2 * max(1, len(self.data))}x}"
        if self.is_reg_read:
            return f"R 0x{self.reg:04x}"
        if self.xfer == "bulk":
            return f"BULK {'in' if self.is_in else 'out'} {len(self.data)}B"
        return f"{self.xfer} {self.event}"


def parse(blob: bytes) -> list[URec]:
    out: list[URec] = []
    by_tag: dict[int, URec] = {}
    off = 0
    n = len(blob)
    while off + 40 <= n:
        (tag, typ, xfer, epnum, devnum, busnum, fsetup, fdata,
         tsec, tusec, status, len_urb, len_cap) = HDR.unpack_from(blob, off)
        setup = blob[off + 40:off + 48]
        dlen = len_cap if fdata != ord("-") else 0
        if off + 48 + dlen > n:
            break
        data = blob[off + 48:off + 48 + dlen]
        rec = URec(tag=tag, event=chr(typ), xfer=XFER.get(xfer, "?"),
                   epnum=epnum, busnum=busnum, devnum=devnum,
                   status=status, time_us=tsec * 1_000_000 + tusec,
                   setup=setup if fsetup == 0 else b"", data=data)
        out.append(rec)
        if rec.event == "S":
            by_tag[tag] = rec
        elif rec.event == "C":
            # 制御 IN のデータは完了側にしかないので、送信側へ貼り戻す
            src = by_tag.pop(tag, None)
            if src is not None and not src.data and data:
                src.data = data
        off += 48 + dlen
    return out


def load(path: str) -> list[URec]:
    with open(path, "rb") as fh:
        return parse(fh.read())
