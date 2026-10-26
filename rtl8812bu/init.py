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


class InitError(RuntimeError):
    pass


class ReadMismatch(InitError):
    pass


def replay(chip, body: bytes, log=None, max_phase: int = 99,
           verify: bool = True, check_reads: bool = True,
           strict_reads: bool = False, max_reports: int = 12) -> dict:
    """観測された初期化列を順に再生する。"""
    log = log or (lambda *_: None)
    dl = fwmod.Downloader(chip, body, fwdl_pages.PAGES, log=log)

    t0 = time.time()
    writes = reads = pages = 0
    mismatches: list[tuple[int, int, int, int]] = []

    for op in init_seq.OPS:
        kind, phase = op[0], op[1]
        if phase > max_phase:
            break

        if kind == "dl":
            dl.reset()
            continue

        if kind == "w":
            _, _, reg, width, value = op
            chip.write(reg, value, width)
            writes += 1
            if writes % 1000 == 0:
                log(f"[init] {writes} writes ({time.time() - t0:.1f}s)")

        elif kind == "r":
            if not check_reads:
                continue
            _, _, reg, width, expected = op
            got = chip.read(reg, width)
            reads += 1
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


def replay_monitor(chip, body: bytes, log=None, check_reads: bool = False) -> dict:
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
    for op in monitor_seq.UP:
        kind = op[0]
        if kind == "w":
            chip.write(op[1], op[3], op[2])
            writes += 1
        elif kind == "r":
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
            "read_mismatches": mismatches, "fw_ready": ready}
