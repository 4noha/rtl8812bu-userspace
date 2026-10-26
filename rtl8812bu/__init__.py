"""RTL8812BU userspace スタック（観測ベース実装 / MIT）。

このパッケージは、Linux の rtw88 や Realtek ベンダードライバの **ソースを参照せず**、
USB バス上で観測した転送列だけから再構成した実装です。
詳細と由来は README と docs/provenance.md を参照してください。
"""

__all__ = ["usb", "fw", "init", "chan", "rx", "ieee80211"]
__version__ = "0.1.0"
