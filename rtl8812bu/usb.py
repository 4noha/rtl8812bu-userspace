"""USB 転送層（pyusb）。

観測（usbmon）から分かっていること:

  * ベンダリクエストは `bmRequestType=0x40`（書き込み）/ `0xc0`（読み出し）、
    `bRequest=0x05`、`wValue` = レジスタ番号、`wIndex=0`。
  * データ幅は 1 / 2 / 4 バイトで、いずれもリトルエンディアン。
  * バルク端点は OUT = 0x05、IN = 0x84。
  * バルク IN のバッファは 32768 バイトで投入されていた。
"""

from __future__ import annotations

import time

import usb.core
import usb.util

VID = 0x0BDA
PID = 0xB812

EP_BULK_OUT = 0x05
EP_BULK_IN = 0x84

REQ_REG = 0x05
BM_WRITE = 0x40
BM_READ = 0xC0

RX_BUF_SIZE = 32768

DEFAULT_TIMEOUT_MS = 2000


class ChipError(RuntimeError):
    pass


class Chip:
    """8822BU への最小限の転送インタフェース。"""

    def __init__(self, serial: str | None = None, timeout_ms: int = DEFAULT_TIMEOUT_MS):
        dev = usb.core.find(idVendor=VID, idProduct=PID)
        if dev is None:
            raise ChipError(
                "0bda:b812 が見つかりません（未接続、または他プロセスが掴んでいます）"
            )
        if serial is not None and dev.serial_number != serial:
            raise ChipError(f"シリアル不一致: {dev.serial_number!r}")
        self.dev = dev
        self.timeout_ms = timeout_ms
        self._open()

    # ------------------------------------------------------------ 初期化
    def _open(self) -> None:
        try:
            self.dev.set_configuration()
        except usb.core.USBError:
            # 既に構成済みなら無視してよい
            pass
        try:
            usb.util.claim_interface(self.dev, 0)
        except usb.core.USBError as exc:
            raise ChipError(
                f"インタフェースを取得できません（{exc}）。"
                "他プロセスが掴んでいる場合は root 権限か再挿抜が必要です"
            ) from exc
        # 挿した直後は電源投入シーケンスが落ち着くまで少し待つ
        time.sleep(0.2)

    def reset(self) -> None:
        """USB ポートをリセットして既定状態に戻す。"""
        try:
            self.dev.reset()
        except usb.core.USBError as exc:
            raise ChipError(f"USB リセットに失敗: {exc}") from exc
        time.sleep(0.05)
        self._open()

    def close(self) -> None:
        try:
            usb.util.release_interface(self.dev, 0)
        except usb.core.USBError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------ 制御転送
    def write(self, reg: int, value: int, width: int = 1) -> None:
        data = int(value).to_bytes(width, "little")
        self.dev.ctrl_transfer(BM_WRITE, REQ_REG, reg, 0, data,
                               timeout=self.timeout_ms)

    def read(self, reg: int, width: int = 1) -> int:
        data = self.dev.ctrl_transfer(BM_READ, REQ_REG, reg, 0, width,
                                      timeout=self.timeout_ms)
        return int.from_bytes(bytes(data), "little")

    # ------------------------------------------------------------ バルク転送
    def bulk_out(self, data: bytes, timeout_ms: int | None = None) -> None:
        self.dev.write(EP_BULK_OUT, data,
                       timeout=timeout_ms or self.timeout_ms)

    def bulk_in(self, size: int = RX_BUF_SIZE, timeout_ms: int | None = None) -> bytes:
        return bytes(self.dev.read(EP_BULK_IN, size,
                                   timeout=timeout_ms or self.timeout_ms))

    # ------------------------------------------------------------ 状態確認
    def preflight(self) -> dict:
        """長い初期化の前に、チップが応答するか確かめる。

        読めない場合は「固着している」と判断して早く止める（途中まで書いて
        放置すると復旧が面倒になるため）。
        """
        try:
            info = {
                "mcufw_ctrl": self.read(0x0080, 4),
                "sys_func_en": self.read(0x0002, 1),
                "sys_cfg": self.read(0x0000, 1),
            }
        except usb.core.USBError as exc:
            raise ChipError(
                f"チップが応答しません（{exc}）。\n"
                "  初期化を始める前に止めました。USB を抜き差ししてから再実行してください。"
            ) from exc
        return info

    def fw_ready(self) -> bool:
        """MCUFW_CTRL（0x0080）の上位ビットで FW 起動完了を判定する。

        観測: FW ダウンロード完了直後の読み出しは 0xc078（bit15/bit14 が立つ）。
        """
        return (self.read(0x0080, 2) & 0xC000) == 0xC000

    def wait_fw_ready(self, timeout_s: float = 2.0) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.fw_ready():
                return True
            time.sleep(0.01)
        return False

    def describe(self) -> str:
        d = self.dev
        return (f"{d.idVendor:04x}:{d.idProduct:04x} "
                f"bus={d.bus} addr={d.address} "
                f"{d.manufacturer} / {d.product}")
