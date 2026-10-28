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

# 40MHz は受信量が多く、モニタ停止時の後始末が重い。安全弁として滞在時間に上限を置く。
SAFE_MAX_SECONDS_40 = 30.0

# モニタ切替の 0x0020 <- 0xed は実測で固着点だったが、省略すると受信が止まる
# （RX に必要な時計設定）。よって既定では回避しない。調査記録は docs/status.md。
RISKY_WRITES: frozenset = frozenset()

DEFAULT_FW_PATHS = [
    os.environ.get("RTL8812BU_FW", ""),
    "/lib/firmware/rtw88/rtw8822b_fw.bin",
    "firmware/rtw8822b_fw.bin",
]


def apply_safety_limit(width: str, secs: float, dwell: float, n_channels: int,
                       max_secs: float = 0.0) -> tuple[float, float, str | None]:
    """帯域幅に応じた安全弁。単発の秒数と巡回の滞在秒数を返す。

    戻り値: (secs, dwell, 適用した上限の説明 or None)
    上限は 40MHz のときだけ働く（--max-secs で明示指定もできる）。
    """
    cap = max_secs if max_secs and max_secs > 0 else (
        SAFE_MAX_SECONDS_40 if str(width) == "40" else None
    )
    if cap is None:
        return secs, dwell, None
    clamped = False
    if n_channels <= 1:
        if secs <= 0 or secs > cap:
            secs = cap
            clamped = True
    else:
        cap_dwell = cap / max(1, n_channels)
        if dwell > cap_dwell:
            dwell = cap_dwell
            clamped = True
    note = (f"{width}MHz は合計 {cap:.0f} 秒で打ち切ります（--max-secs で変更可）"
            if clamped else None)
    return secs, dwell, note


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
    ap.add_argument("--width", default="20", choices=chan.WIDTHS,
                    help="帯域幅（20 / 40MHz）")
    ap.add_argument("--channels", help="複数チャネルを巡回（例: 1,6,11,36,44,48,149,165）")
    ap.add_argument("--dwell", type=float, default=3.0, help="巡回時の 1 チャネルあたり秒数")
    ap.add_argument("--max-secs", type=float, default=0.0,
                    help="受信の上限秒数（0 で既定: 40MHz は 30 秒、20MHz は無制限）")
    ap.add_argument("--secs", type=float, default=10.0, help="受信時間（0 で Ctrl-C まで）")
    ap.add_argument("--jsonl", help="受信フレームを JSON Lines で保存")
    ap.add_argument("--list", action="store_true", help="利用可能なチャネルを表示")
    ap.add_argument("--no-reset", action="store_true", help="USB リセットを省略")
    ap.add_argument("--no-verify", action="store_true", help="FW 起動確認を省略")
    ap.add_argument("--write-delay-ms", type=float, default=0.0,
                    help="レジスタ書き込みの間隔（ms）。VM 観測では約 1.4ms 空いていた")
    ap.add_argument("--settle-ms", type=float, default=0.0,
                    help="時計系レジスタ(0x0020)を書く前の待ち（ms）。既定 0（実験用）")
    ap.add_argument("--fast-start", action="store_true",
                    help="FW 稼働中なら初期化を省略する（実験的。既定は毎回初期化）")
    ap.add_argument("--check-reads", action="store_true",
                    help="初期化中に観測値との読み出し照合を行う（診断用・転送が増える）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        for w in chan.WIDTHS:
            print(f"{w:>2s}MHz:", ", ".join(str(c) for c in chan.available(w)))
        return 0

    log = (lambda *_: None) if args.quiet else (lambda msg: print(msg, file=sys.stderr))

    n_ch = len([c for c in (args.channels or "").split(",") if c.strip()]) or 1
    secs, dwell, safety_note = apply_safety_limit(args.width, args.secs, args.dwell, n_ch,
                                                 args.max_secs)
    if safety_note:
        print(f"[safety] {safety_note}", file=sys.stderr)

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
    try:
        pf = chip.preflight()
    except ChipError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        chip.close()
        return 4
    log(f"[usb] preflight: MCUFW_CTRL=0x{pf['mcufw_ctrl']:08x} "
        f"SYS_FUNC_EN=0x{pf['sys_func_en']:02x} SYS_CFG=0x{pf['sys_cfg']:02x}")

    out = open(args.jsonl, "w") if args.jsonl else None
    receiver = rx.Receiver(chip, log=log)
    try:
        if not args.no_reset:
            log("[usb] リセット")
            chip.reset()
        fw_already_up = (pf["mcufw_ctrl"] & 0xC000) == 0xC000
        if fw_already_up and args.fast_start:
            # FW が動いている個体に 2 回目のダウンロードを重ねると不安定になるため、
            # 初期化（probe+start）を省略してモニタ切替だけ行う。
            log("[init] FW が稼働中のため初期化を省略（--fast-start）")
            res = {"skipped": True}
        else:
            res = init.replay(chip, body, log=log, verify=not args.no_verify,
                              check_reads=args.check_reads,
                              delay_s=args.write_delay_ms / 1000.0)
        log(f"[init] {res}")

        settle = {0x0020: args.settle_ms / 1000.0} if args.settle_ms > 0 else {}
        mon = init.replay_monitor(chip, body, log=log,
                                  delay_s=args.write_delay_ms / 1000.0,
                                  skip=RISKY_WRITES, settle_s=settle)
        log(f"[monitor] {mon}")

        if not args.channels:
            chan.set_channel(chip, args.ch, width=args.width, log=log)
        receiver.start()

        t0 = time.time()
        deadline = None if secs <= 0 else t0 + secs

        def emit(frame: bytes, ch: int | None) -> None:
            info_f = ieee80211.parse(frame)
            if out:
                rec = {**info_f, "ts": round(time.time(), 6)}
                if ch is not None:
                    rec["rx_ch"] = ch
                out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            if not args.quiet:
                tag = f"ch{ch:<4d}" if ch is not None else "      "
                print(f"{time.time() - t0:8.3f} {tag} {ieee80211.describe(info_f)}",
                      flush=True)

        try:
            if args.channels:
                plan = [int(c) for c in args.channels.split(",") if c.strip()]
                for c in plan:
                    if c not in chan.available(args.width):
                        print(f"エラー: ch{c} は {args.width}MHz では未観測です", file=sys.stderr)
                        return 2
                summary: dict[int, dict] = {}
                for ch in plan:
                    chan.set_channel(chip, ch, width=args.width, log=log)
                    stats = summary.setdefault(ch, {"frames": 0, "ssid": set()})
                    end = time.time() + dwell
                    while time.time() < end:
                        try:
                            frame = receiver.queue.get(timeout=0.15)
                        except queue.Empty:
                            continue
                        stats["frames"] += 1
                        info_f = ieee80211.parse(frame)
                        if "ssid" in info_f and info_f["name"] in ("beacon", "probe-resp"):
                            stats["ssid"].add(info_f["ssid"])
                        emit(frame, ch)
                log("[sweep] 結果:")
                for ch in plan:
                    st = summary[ch]
                    ssids = ", ".join(sorted(st["ssid"])) or "-"
                    log(f"  ch{ch:<4d} frames={st['frames']:<5d} SSID: {ssids}")
            else:
                while not (deadline and time.time() > deadline):
                    try:
                        frame = receiver.queue.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    emit(frame, None)
        except KeyboardInterrupt:
            pass
    finally:
        receiver.stop()
        init.shutdown(chip, log=log)   # モニタのまま放置すると固着することがある
        if out:
            out.close()
        chip.close()

    log(f"[rx] {receiver.stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
