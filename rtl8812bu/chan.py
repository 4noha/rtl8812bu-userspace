"""チャネル設定（観測ベース）。

`tables/chan_table.py` には「あるチャネルへ切り替えたときに観測された書き込み列」
がそのまま入っている。ここでは選択されたチャネルの列を順に再生するだけ。

注意: 観測は「基準チャネル → 目的チャネル」の遷移なので、任意の開始状態から
同じ結果になることは保証しない（README の制限事項を参照）。
"""

from __future__ import annotations

from .tables import chan_table

CHANNELS = sorted(chan_table.CHANNELS)

BAND_2G = [c for c in CHANNELS if c <= 14]
BAND_5G = [c for c in CHANNELS if c > 14]


class ChannelError(ValueError):
    pass


def available() -> list[int]:
    return CHANNELS


def current_band(channel: int) -> str:
    return "2.4GHz" if channel <= 14 else "5GHz"


def set_channel(chip, channel: int, log=None) -> int:
    """観測された書き込み列を再生してチャネルを切り替える。"""
    log = log or (lambda *_: None)
    rows = chan_table.CHANNELS.get(channel)
    if rows is None:
        raise ChannelError(
            f"ch{channel} は未観測です。利用可能: {CHANNELS}"
        )
    for reg, width, value in rows:
        chip.write(reg, value, width)
    log(f"[chan] ch{channel} ({current_band(channel)}) {len(rows)} writes")
    return len(rows)
