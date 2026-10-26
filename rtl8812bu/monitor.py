"""モニタモードの受信ツール（クリーンルーム実装の動作確認用）。

使い方:
    python3 -m rtl8812bu.monitor --fw /path/rtw8822b_fw.bin --ch 6 --secs 15
    python3 -m rtl8812bu.monitor --list
    python3 -m rtl8812bu.monitor --fw ... --ch 36 --secs 0 --jsonl /tmp/frames.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import time

from . import chan, fw, ieee80211, init, rx
from .usb import Chip, ChipError

DEFAULT_FW_PATHS = [
    os.environ.get("RTL8812BU_FW", ""),
    "/lib/firmware/rtw88/rtw8822b_fw.bin",
    "firmware/rtw8822b_fw.bin",
]


def _find_fw(explicit: str | None) -> str:
    if explicit:
        return explicit
    for p in DEFAULT_FW_PATHS:
        if p and os.path.exists(p):
            return p
    return DEFAULT_FW_PATHS[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rtl8812bu.monitor")
    ap.add_argument("--fw", help="FW ファイル（rtw8822b_fw.bin）")
    ap.add_argument("--ch", type=int, default=6, help="チャネル")
    ap.add_argument("--secs", type=float, default=10.0, help="受信時間（0 で Ctrl-C まで）")
    ap.add_argument("--jsonl", help="受信フレームを JSON Lines で保存")
    ap.add_argument("--list", action="store_true", help="利用可能なチャネルを表示")
    ap.add_argument("--no-reset", action="store_true", help="USB リセットを省略")
    ap.add_argument("--no-verify", action="store_true", help="FW 起動確認を省略")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        print("利用可能なチャネル:", ", ".join(str(c) for c in chan.available()))
        return 0

    log = (lambda *_: None) if args.quiet else (lambda msg: print(msg, file=sys.stderr))

    fw_path = _find_fw(args.fw)
    try:
        body, info = fw.load(fw_path)
    except fw.FirmwareError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 2
    log(f"[fw] {info['path']} size={info['size']} version={info['version']} "
        f"sha256={info['sha256'][:16]}… known={info['matches_known']}")

    try:
        chip = Chip()
    except ChipError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 3
    log(f"[usb] {chip.describe()}")

    out = open(args.jsonl, "w") if args.jsonl else None
    receiver = rx.Receiver(chip, log=log)
    try:
        if not args.no_reset:
            log("[usb] リセット")
            chip.reset()
        res = init.replay(chip, body, log=log, verify=not args.no_verify)
        log(f"[init] {res}")

        mon = init.replay_monitor(chip, body, log=log)
        log(f"[monitor] {mon}")

        chan.set_channel(chip, args.ch, log=log)
        receiver.start()

        t0 = time.time()
        deadline = None if args.secs <= 0 else t0 + args.secs
        try:
            while not (deadline and time.time() > deadline):
                try:
                    frame = receiver.queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                info_f = ieee80211.parse(frame)
                if out:
                    out.write(json.dumps({**info_f, "ts": round(time.time(), 6)},
                                         ensure_ascii=False, default=str) + "\n")
                if not args.quiet:
                    print(f"{time.time() - t0:8.3f} {ieee80211.describe(info_f)}",
                          flush=True)
        except KeyboardInterrupt:
            pass
    finally:
        receiver.stop()
        if out:
            out.close()
        chip.close()

    log(f"[rx] {receiver.stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
