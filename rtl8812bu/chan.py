"""チャネル設定（観測ベース）。

`tables/chan_table.py`（20MHz）と `tables/chan_table40.py`（40MHz）には
「あるチャネルへ切り替えたときに観測された書き込み列」がそのまま入っている。
ここでは選択されたチャネル・帯域幅の列を順に再生するだけ。

40MHz の中心周波数は、観測した `iw` の設定に従う（36/44 が HT40+、40/48 が HT40-。
つまり 36+40 → center 38、44+48 → center 46）。

注意: 観測は「基準チャネル（20MHz の ch36）→ 目的の設定」の遷移なので、
任意の開始状態から同じ結果になることは保証しない（README の制限事項を参照）。
"""

from __future__ import annotations

from .tables import chan_table, chan_table40

WIDTHS = ("20", "40")

_TABLES = {
    "20": chan_table.CHANNELS,
    "40": chan_table40.CHANNELS_40,
}


class ChannelError(ValueError):
    pass


def available(width: str = "20") -> list[int]:
    return sorted(_TABLES[_check_width(width)])


def _check_width(width: str) -> str:
    w = str(width)
    if w not in _TABLES:
        raise ChannelError(f"帯域幅 {width!r} は未対応です（{', '.join(WIDTHS)}）")
    return w


def current_band(channel: int) -> str:
    return "2.4GHz" if channel <= 14 else "5GHz"


def set_channel(chip, channel: int, width: str = "20", log=None) -> int:
    """観測された書き込み列を再生してチャネルを切り替える。"""
    log = log or (lambda *_: None)
    w = _check_width(width)
    table = _TABLES[w]
    rows = table.get(channel)
    if rows is None:
        raise ChannelError(
            f"ch{channel} ({w}MHz) は未観測です。利用可能: {sorted(table)}"
        )
    for reg, rwidth, value in rows:
        chip.write(reg, value, rwidth)
    log(f"[chan] ch{channel} ({current_band(channel)}, {w}MHz) {len(rows)} writes")
    return len(rows)
