"""受信（バルク IN）の解析。

観測から分かっていること（モニタモード・1 URB = 1 パケットの場合）:

  * バルク IN のバッファサイズは 32768 バイト。
  * データの先頭 56 バイトはパケットごとの前置き（ヘッダ）で、
    先頭 2 バイトがリトルエンディアンのフレーム長。
    3 バイト目は 0x04、4 バイト目は端点番号 0x84 だった。
  * フレーム本体はその直後（オフセット 56）から始まる。
    前置きとフレームの間に余分なバイトは無い（195/195 で一致を確認）。

この 56 バイトのうち、長さ以外のフィールドの意味は未確定なので触らない。
"""

from __future__ import annotations

import queue
import struct
import threading

RX_PREFIX_LEN = 56
MAX_FRAME_LEN = 4096


class RxError(RuntimeError):
    pass


def parse_urb(data: bytes) -> list[bytes]:
    """1 つのバルク IN バッファから 802.11 フレームを取り出す。"""
    frames: list[bytes] = []
    pos = 0
    n = len(data)
    while pos + RX_PREFIX_LEN <= n:
        plen = struct.unpack_from("<H", data, pos)[0]
        if plen == 0 or plen > MAX_FRAME_LEN:
            break
        start = pos + RX_PREFIX_LEN
        end = start + plen
        if end > n:
            break
        frames.append(data[start:end])
        pos = end
    return frames


def parse_prefix(data: bytes) -> dict:
    """前置き 56 バイトから、確実に読めたフィールドだけを取り出す。"""
    if len(data) < RX_PREFIX_LEN:
        return {}
    return {
        "len": struct.unpack_from("<H", data, 0)[0],
        "b2": data[2],
        "b3": data[3],
    }


class Receiver:
    """バックグラウンドでバルク IN を回し、フレームをキューに積む。"""

    def __init__(self, chip, buffer_size: int = 32768, timeout_ms: int = 300, log=None):
        self.chip = chip
        self.buffer_size = buffer_size
        self.timeout_ms = timeout_ms
        self.log = log or (lambda *_: None)
        self.queue: queue.Queue[bytes] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = {"urbs": 0, "frames": 0, "bytes": 0, "errors": 0}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="rtl8812bu-rx", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        import usb.core
        while not self._stop.is_set():
            try:
                buf = self.chip.bulk_in(self.buffer_size, self.timeout_ms)
            except usb.core.USBTimeoutError:
                continue
            except usb.core.USBError as exc:
                self.stats["errors"] += 1
                self.log(f"[rx] USB エラー: {exc}")
                if "No such device" in str(exc) or "disconnected" in str(exc):
                    break
                continue
            if not buf:
                continue
            self.stats["urbs"] += 1
            self.stats["bytes"] += len(buf)
            for frame in parse_urb(buf):
                self.stats["frames"] += 1
                self.queue.put(frame)

    def frames(self, timeout: float = 0.5):
        """フレームを順に返すジェネレータ（停止まで）。"""
        import queue as _q
        while not self._stop.is_set():
            try:
                yield self.queue.get(timeout=timeout)
            except _q.Empty:
                continue

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
