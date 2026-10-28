"""初期化シーケンスの再生。

`rtl8812bu/tables/init_seq.py` は usbmon の観測から機械生成したもので、
「どのレジスタに、どの幅で、どの値が、どの順で書かれたか」だけを持つ。
ここではそれを順に再生する。意味付け（なぜその値か）は行わない。

観測された読み出しも実行し、観測値と突き合わせる。値が食い違った箇所は
「状態が観測時とずれている」ことを意味するので、警告として出す
（`strict_reads=True` ならその場で中断）。

FW ダウンロードは観測列の中に埋め込まれているので、同じ位置で実行する
（1 回の起動で 2 回現れる。probe 相当と start 相当）。
"""

from __future__ import annotations

import time

from . import fw as fwmod
from .tables import fwdl_pages
from .tables import init_seq
from .tables import monitor_seq



SEC_REG = 0x04E0   # 保護付き書き込みのミラー（観測: 書き込み先の現在値が入る）

# 読むたびに値が変わる「ストリーム」のレジスタ。ポーリングしてはいけない。
# 観測: 0x0030 は 1,025 回の読み出しで 1,024 種類の値（efuse の読み出し）。
POLL_EXCLUDE = frozenset({0x0030})

POLL_TIMEOUT_S = 0.050   # 期待値になるまで待つ上限
POLL_INTERVAL_S = 0.002


def poll_read(chip, reg: int, width: int, expected: int,
              timeout_s: float = POLL_TIMEOUT_S) -> tuple[int, int, float]:
    """期待値になるまで読む（ドライバの「状態ビットを待つ」動作の再現）。

    戻り値: (最後の値, 試行回数, 待ち時間秒)
    """
    t0 = time.time()
    tries = 0
    while True:
        tries += 1
        got = chip.read(reg, width)
        if got == expected:
            return got, tries, time.time() - t0
        if time.time() - t0 >= timeout_s:
            return got, tries, time.time() - t0
        time.sleep(POLL_INTERVAL_S)


class GuardedWriter:
    """観測された書き込み列を、保護付き書き込み（RMW）を理解して再生する。

    観測されたパターン:

        R  <reg>        -> old          （現在値を読む）
        W  0x04e0       <- old          （「今の値はこれ」とチップに伝える）
        W  <reg>        <- new          （新しい値）
        W  0x04e0       <- new          （ミラーを更新）

    この「old」は捕捉した瞬間の値なので、実機の状態が違うと噛み合わず、
    チップが無応答になる（0x04e0 経由の書き込みがタイムアウトする）。
    ここでは書き込み直前に現在値を読み、違っていれば差分だけを適用する。
    """

    # 時計・PLL 系とみられるレジスタ。書く前に少し待つ（観測: ここで固着した）。
    SETTLE_DEFAULT = {0x0020: 0.050}

    def __init__(self, chip, log=None, delay_s: float = 0.0, skip=frozenset(),
                 settle_s: dict[int, float] | None = None):
        self.chip = chip
        self.log = log or (lambda *_: None)
        self.delay_s = delay_s
        self.skip = set(skip)          # (reg, value) の組で指定した書き込みを避ける
        self.skipped = 0
        self.settle_s = dict(self.SETTLE_DEFAULT if settle_s is None else settle_s)
        self.settled = 0
        self.last_read: dict[int, int] = {}
        self.pending: tuple[int, int] | None = None      # (value, width)
        self.last_target: tuple[int, int, int] | None = None  # (captured_new, width, actual)
        self.adapted = 0

    def note_read(self, reg: int, value: int) -> None:
        self.last_read[reg] = value

    def write(self, reg: int, width: int, value: int) -> None:
        if reg == SEC_REG:
            if (self.last_target and self.last_target[0] == value
                    and self.last_target[1] == width):
                self.chip.write(reg, self.last_target[2], width)
            else:
                self.chip.write(reg, value, width)
            self.pending = (value, width)
            self.last_target = None
            return

        if (self.pending and self.pending[1] == width
                and self.last_read.get(reg) == self.pending[0]):
            old = self.pending[0]
            live = self.chip.read(reg, width)
            if live != old:
                # ミラーだけ実機の値に合わせ、書き込む値は捕捉した絶対値のまま。
                # （差分適用にすると、捕捉時と状態が違うぶん必要なビットが落ちる）
                self.chip.write(SEC_REG, live, width)
                if self.delay_s:
                    time.sleep(self.delay_s)
                self.chip.write(reg, value, width)
                self.last_target = (value, width, value)
                self.pending = None
                self.adapted += 1
                self.log(f"[init] ミラーを実機値に合わせました "
                         f"reg=0x{reg:04x} 捕捉時=0x{old:0{2 * width}x} "
                         f"実機=0x{live:0{2 * width}x} 書き込み=0x{value:0{2 * width}x}")
                return

        settle = self.settle_s.get(reg)
        if settle:
            time.sleep(settle)         # PLL/時計が落ち着くのを待つ
            self.settled += 1

        if (reg, value) in self.skip:
            self.skipped += 1
            self.log(f"[init] 既知の危険な書き込みを回避しました reg=0x{reg:04x} <- 0x{value:0{2 * width}x}")
            self.pending = None
            self.last_target = None
            return
        self.chip.write(reg, value, width)
        if self.delay_s:
            time.sleep(self.delay_s)
        self.pending = None
        self.last_target = None


class InitError(RuntimeError):
    pass


class ReadMismatch(InitError):
    pass


def replay(chip, body: bytes, log=None, max_phase: int = 99,
           verify: bool = True, check_reads: bool = False,
           strict_reads: bool = False, max_reports: int = 12,
           delay_s: float = 0.0, poll_reads: bool = True,
           poll_timeout_s: float = POLL_TIMEOUT_S) -> dict:
    """観測された初期化列を順に再生する。"""
    log = log or (lambda *_: None)
    dl = fwmod.Downloader(chip, body, fwdl_pages.PAGES, log=log)

    t0 = time.time()
    writes = reads = pages = 0
    waited_total = 0.0
    poll_waits = 0
    mismatches: list[tuple[int, int, int, int]] = []

    gw = GuardedWriter(chip, log, delay_s=delay_s)
    last = "開始前"
    for op in init_seq.OPS:
        kind, phase = op[0], op[1]
        if phase > max_phase:
            break

        if kind == "dl":
            dl.reset()
            continue

        if kind == "w":
            _, _, reg, width, value = op
            last = f"reg=0x{reg:04x} <- 0x{value:0{2 * width}x} ({width}B)"
            try:
                gw.write(reg, width, value)
            except Exception as exc:
                raise InitError(
                    f"初期化の {writes} 書き込み目で失敗しました（{last}）: {exc}\n"
                    "  USB を抜き差ししてから再実行してください。"
                ) from exc
            writes += 1
            if writes % 500 == 0:
                log(f"[init] {writes} writes ({time.time() - t0:.1f}s)")

        elif kind == "r":
            _, _, reg, width, expected = op
            gw.note_read(reg, expected)      # 保護付き書き込みの判定に必要
            if reg in POLL_EXCLUDE:
                continue
            if not poll_reads:
                continue
            last = f"read reg=0x{reg:04x} ({width}B)"
            try:
                got, tries, waited = poll_read(chip, reg, width, expected,
                                               poll_timeout_s)
            except Exception as exc:
                raise InitError(
                    f"初期化の読み出し（{reads + 1} 回目、{last}）で失敗しました: {exc}\n"
                    "  USB を抜き差ししてから再実行してください。"
                ) from exc
            reads += 1
            waited_total += waited
            if waited > 0.005:
                poll_waits += 1
            if got != expected:
                mismatches.append((reads, reg, expected, got))
                if len(mismatches) <= max_reports:
                    log(f"[init] 読み出し不一致 #{reads} reg=0x{reg:04x} "
                        f"観測=0x{expected:0{2 * width}x} 実機=0x{got:0{2 * width}x}")
                if strict_reads:
                    raise ReadMismatch(
                        f"reg 0x{reg:04x} が観測値と異なります "
                        f"(観測 0x{expected:x} / 実機 0x{got:x})"
                    )

        elif kind == "bulk":
            chip.bulk_out(bytes.fromhex(op[2]))
            continue

        elif kind == "fw":
            _, _, page_idx, payload_len, header_hex = op
            dl.send_page(page_idx, payload_len, bytes.fromhex(header_hex))
            pages += 1

        elif kind == "gap":
            ms = op[2]
            if ms >= 20:
                time.sleep(ms / 1000.0)

    elapsed = time.time() - t0
    result = {
        "writes": writes,
        "reads": reads,
        "poll_waits": poll_waits,
        "waited_s": round(waited_total, 2),
        "read_mismatches": len(mismatches),
        "fw_pages": pages,
        "seconds": round(elapsed, 2),
    }
    log(f"[init] 再生完了: {writes} writes / {reads} reads "
        f"({len(mismatches)} 不一致) / {pages} FW pages / {elapsed:.1f}s")

    if verify:
        ready = chip.wait_fw_ready(2.0)
        result["fw_ready"] = ready
        result["mcufw_ctrl"] = chip.read(0x0080, 2)
        log(f"[init] MCUFW_CTRL=0x{result['mcufw_ctrl']:04x} fw_ready={ready}")
        if not ready:
            raise InitError(
                f"FW が起動しませんでした（MCUFW_CTRL=0x{result['mcufw_ctrl']:04x}）。\n"
                "  対処: USB を抜き差ししてから再実行してください。"
                "初期化の途中で失敗したチップは、ソフトウェアのリセットでは戻らない"
                "ことがあります（docs/status.md 参照）。"
            )
    return result


def replay_monitor(chip, body: bytes, log=None, check_reads: bool = False,
                   delay_s: float = 0.0, skip=frozenset(),
                   settle_s: dict[int, float] | None = None,
                   poll_reads: bool = True,
                   poll_timeout_s: float = POLL_TIMEOUT_S) -> dict:
    """マネージド（up）からモニタモード（up）へ切り替える。

    観測: `ip link set down`（12 書き込み）→ `iw set type monitor`（USB 転送なし）
    → `ip link set up`（FW ダウンロードを含む 1683 書き込み）。
    """
    log = log or (lambda *_: None)
    dl = fwmod.Downloader(chip, body, fwdl_pages.PAGES)

    for reg, width, value in monitor_seq.DOWN:
        chip.write(reg, value, width)

    writes = reads = pages = 0
    mismatches = 0
    waited_total = 0.0
    poll_waits = 0
    gw = GuardedWriter(chip, log, delay_s=delay_s, skip=skip, settle_s=settle_s)
    for op in monitor_seq.UP:
        kind = op[0]
        if kind == "r":
            reg, width, expected = op[1], op[2], op[3]
            gw.note_read(reg, expected)
            if poll_reads and reg not in POLL_EXCLUDE:
                got, tries, waited = poll_read(chip, reg, width, expected,
                                               poll_timeout_s)
                reads += 1
                waited_total += waited
                if waited > 0.005:
                    poll_waits += 1
                if got != expected:
                    mismatches += 1
            continue
        if kind == "w":
            try:
                gw.write(op[1], op[2], op[3])
            except Exception as exc:
                raise InitError(
                    f"モニタ切替の {writes} 書き込み目で失敗しました"
                    f"（reg=0x{op[1]:04x} <- 0x{op[3]:0{2 * op[2]}x}）: {exc}\n"
                    "  USB を抜き差ししてから再実行してください。"
                ) from exc
            writes += 1
        elif False:
            if not check_reads:
                continue
            got = chip.read(op[1], op[2])
            reads += 1
            if got != op[3]:
                mismatches += 1
        elif kind == "dl":
            dl.reset()
        elif kind == "bulk":
            chip.bulk_out(bytes.fromhex(op[1]))
        elif kind == "fw":
            dl.send_page(op[1], op[2], bytes.fromhex(op[3]))
            pages += 1
        elif kind == "gap":
            if op[1] >= 20:
                time.sleep(op[1] / 1000.0)

    ready = chip.wait_fw_ready(2.0)
    log(f"[monitor] モニタモードへ切替: {writes} writes / {pages} FW pages / "
        f"fw_ready={ready} MCUFW_CTRL=0x{chip.read(0x0080, 2):04x}")
    if not ready:
        raise InitError("モニタモード切替後に FW が起動していません。")
    return {"writes": writes, "reads": reads, "fw_pages": pages,
            "read_mismatches": mismatches, "poll_waits": poll_waits,
            "waited_s": round(waited_total, 2), "fw_ready": ready}


def shutdown(chip, log=None) -> bool:
    """チップを安全な状態（モニタ停止相当）に戻す。

    観測: `ip link set down` は 12 個の書き込みで、受信と DMA を止める。
    これを再生せずにモニタモードのまま放っておくと、チップが固着することがある
    （読みも書きもタイムアウトし、USB の再列挙では戻らない）。

    戻り値: 成功したら True。すでに固着している場合は False。
    """
    log = log or (lambda *_: None)
    try:
        for reg, width, value in monitor_seq.DOWN:
            chip.write(reg, value, width)
        log("[shutdown] モニタ停止の書き込みを再生しました")
        # 受信を止めたあと USB をリセットしておく（放置時の固着を避けるため）
        try:
            chip.reset()
            log("[shutdown] USB をリセットしました")
        except Exception as exc:
            log(f"[shutdown] USB リセットは失敗（{exc}）")
        return True
    except Exception as exc:      # 固着しているとここに来る
        log(f"[shutdown] 停止に失敗（{exc}）。抜き差しが必要かもしれません")
        return False
