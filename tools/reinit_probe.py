#!/usr/bin/env python3
"""モニタモードを「同じプロセス内で 2 回」繰り返せるかを確かめる診断。

- 1 回目: init → 3 秒受信 → shutdown
- 2 回目: 同じハンドルのまま init → 3 秒受信 → shutdown
- 3 回目: いったん close して開き直して init（プロセスをまたぐ場合の再現）

どこで書き込みが固まるかを見る。
"""
import sys
import time

sys.path.insert(0, "/Users/4noha/works/tools/rtl8812au-cleanroom")

import usb.core
import usb.util

from rtl8812bu import init, monitor_seq
from rtl8812bu.usb import Chip  # noqa: F401  (存在しなければ下で調整)

FW = "/tmp/rtw8822b_fw.bin"


def log(*a):
    print(*a, flush=True)


def run_once(chip, label):
    t0 = time.time()
    try:
        init.replay(chip, monitor_seq.COLD, log=lambda *a: None)
        log(f"[{label}] init ok ({time.time()-t0:.1f}s) MCUFW=0x{chip.read(0x0080,2):04x}")
    except Exception as e:
        log(f"[{label}] init NG: {e}")
        return False
    try:
        frames = 0
        end = time.time() + 3
        while time.time() < end:
            frames += len(chip.read_bulk_frames(timeout_ms=200) if hasattr(chip, "read_bulk_frames") else [])
        log(f"[{label}] rx {frames} frames")
    except Exception as e:
        log(f"[{label}] rx NG: {e}")
    try:
        init.shutdown(chip, log=log)
    except Exception as e:
        log(f"[{label}] shutdown NG: {e}")
    return True


def main():
    log("== device open ==")
    dev = usb.core.find(idVendor=0x0bda, idProduct=0xb812)
    if dev is None:
        log("デバイスが見つかりません")
        return 2
    dev.set_configuration()
    chip = None
    for name in ("Chip", "UsbChip", "Device"):
        if hasattr(init, name):
            chip = getattr(init, name)(dev)
            break
    log("chip =", type(chip).__name__ if chip else "?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
