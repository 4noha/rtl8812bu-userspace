# rtl8812au-cleanroom

RTL8812BU（`0bda:b812`）を **ユーザ空間から直接動かす**ためのスタックです。
Linux カーネルドライバ（rtw88）や Realtek ベンダードライバの **ソースを参照せず**、
USB バス上で観測した転送だけを入力として再構成しています。

- ライセンス: **MIT**（`LICENSE`）
- 対象: macOS / Linux / その他 libusb が動く環境
- 目的: Wi-Fi の観測（モニタモード受信）と、その上に載るツールの土台

> 現状、**初期化とファームウェアの起動（`MCUFW_CTRL=0xc078`）まで実機で確認済み**。
> モニタモード切替とチャネル設定も実装済みで、実機での受信確認が次の 1 歩です。
> 経緯と残件は [`docs/status.md`](docs/status.md)。

---

## 何を「観測」から作ったか

| 生成物 | 入力 | 内容 |
|---|---|---|
| `rtl8812bu/tables/init_seq.py` | usbmon（Linux の rtw88 が動いている間の転送） | 5466 件の書き込み + 1611 件の期待読み出し値 |
| `rtl8812bu/tables/fwdl_pages.py` | 同上（バルク OUT） | FW 転送 1 回分 ×2 のヘッダ 48 バイトとペイロード長 |
| `rtl8812bu/tables/chan_table.py` | usbmon（モニタモードでチャネルを切り替えた捕捉） | 8 チャネル分の書き込み列 |
| `rtl8812bu/tables/monitor_seq.py` | usbmon（down → monitor → up の捕捉） | モニタモード切替の書き込み列（FW 再ダウンロード含む） |

生成器は `tools/` にあり、いずれも「観測 → 表」の機械変換だけを行います。
由来と検証方法は [`docs/provenance.md`](docs/provenance.md)。

### 観測から決めた仕様（推測ではない）

| 事項 | 値 | 決め方 |
|---|---|---|
| レジスタアクセス | `bmRequestType=0x40/0xc0`, `bRequest=0x05`, `wValue=reg`, 幅 1/2/4 | 捕捉した全転送で一致 |
| バルク端点 | OUT `0x05` / IN `0x84`、RX バッファ 32 KiB | 捕捉 |
| FW | ファイル先頭 64 バイトは送らない。1 転送 = 48 バイトヘッダ + 最大 4096 バイト | 転送列をバイト比較して確定 |
| FW 起動確認 | `0x0080` の bit15/14 が両方立つ（`0xc000`） | Linux ドライバの読み出し値 |
| RX | データ先頭 56 バイトは前置き。先頭 2 バイトがフレーム長、フレームは 56 バイト目から | tcpdump と同時取得してフレーム位置を照合（195/195） |

## 使い方

```bash
python3 -m pip install pyusb        # 依存はこれだけ

# 受信できるチャネル
python3 -m rtl8812bu.monitor --list

# FW を指定してモニタ受信（FW は自分で用意する。同梱しない）
python3 -m rtl8812bu.monitor --fw /path/to/rtw8822b_fw.bin --ch 6 --secs 15

# 受信フレームを JSON Lines で保存
python3 -m rtl8812bu.monitor --fw ... --ch 36 --secs 0 --jsonl /tmp/frames.jsonl
```

FW は linux-firmware の `rtw88/rtw8822b_fw.bin` を使います（Realtek の配布物で、
MIT ではないためこのリポジトリには入れていません）。既知のファイルの SHA256 は
`rtl8812bu/fw.py` の `KNOWN_SHA256` にあります。

`sudo` は通常不要です（macOS では、カーネルドライバに掴まれていない USB デバイスを
一般ユーザーのプロセスが IOKit 経由で開けるため）。他プロセスが掴んでいる場合のみ
root が必要になります。

## 構成

```
rtl8812bu/
  usb.py        転送層（pyusb）。レジスタ読み書き、バルク入出力、FW 起動確認
  fw.py         FW の読み込みと転送列の組み立て（観測されたヘッダを再生）
  init.py       初期化列とモニタ切替の再生（読み出し照合・FW ページ送信）
  chan.py       チャネル切替（観測された書き込み列の再生）
  rx.py         バルク IN の解析（56 バイト前置き + フレーム）
  ieee80211.py  フレームパーサ（802.11 仕様に基づく独自実装）
  monitor.py    モニタ受信の CLI
  tables/       自動生成。手で編集しない
tools/
  usbmon.py      usbmon テキストのパーサ
  usbmon_bin.py  usbmon バイナリのパーサ
  umon.py        Linux 上でバイナリ捕捉を書き出す（root）
  gen_init.py    捕捉 → init_seq.py / fwdl_pages.py
  gen_chan.py    捕捉 → chan_table.py
  gen_monitor.py 捕捉 → monitor_seq.py
  diff_trace.py  2 つの捕捉の書き込み列を比較
tests/
  test_offline.py  実機不要の自己テスト
docs/
  provenance.md  由来の記録
  status.md      現状とブロッカー、次の手順
  capture.md     捕捉の取り方（Linux / root Android）
```

## 自己テスト

```bash
python3 tests/test_offline.py
```

実機も FW も不要です（合成データでパーサ・表の整合性を確認します）。
実機の捕捉は同梱していません（他人のネットワークのフレームを含むため）。

## 設計上の注意

- **推測でレジスタを書かない。** 表にない値は書かない。書き足す必要が出たら、
  まず捕捉を取って生成器を通す。
- **意味付けをしない。** 「この値はきっと CCA しきい値」のような解釈はコメントに
  書かない（間違っていたときに害が大きいため）。分かったことだけ書く。
- **失敗したら状態を戻す。** 初期化の途中で失敗すると、レジスタ書き込みが
  タイムアウトする状態になることがある。USB の再列挙では戻らないので、
  実機で試すときは抜き差しできる状態で行う。

## ライセンス

MIT（`LICENSE`）。ただし **FW 本体は含みません**（Realtek の配布物）。
同梱・再配布する場合は各自でライセンスを確認してください。
