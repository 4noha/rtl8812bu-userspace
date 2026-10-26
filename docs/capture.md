# 捕捉の取り方

このリポジトリの表は、すべて **Linux 上で動く既成ドライバの USB 転送** を
観測して作る。観測するだけで、ドライバのソースは読まない。

## 0. 前提

- 8822BU（`0bda:b812`）を接続した Linux（実機でも VM でもよい）
- root（`/sys/kernel/debug/usb/usbmon` と `/dev/usbmonN` のため）
- カーネルに `CONFIG_USB_MON` があること（Debian なら標準）

```bash
sudo modprobe usbmon
ls /sys/kernel/debug/usb/usbmon/       # 0u, 1u, ... とバス番号を確認
lsusb -t | grep -i 8822bu              # Bus 001 Device 00x
```

## 1. テキスト捕捉（レジスタ書き込み列用）

```bash
sudo cat /sys/kernel/debug/usb/usbmon/1u > /tmp/reg.txt &
CPID=$!
sudo modprobe -r rtw88_8822bu; sudo modprobe rtw88_8822bu   # 初期化を走らせる
sleep 8
sudo kill $CPID
```

テキスト形式は 1 レコード 32 バイトで切られる。**レジスタ書き込み（1〜4 バイト）と
読み出し値の記録には十分**だが、FW のバルク転送や RX フレームには足りない。

## 2. バイナリ捕捉（FW 転送・RX フレーム用）

```bash
# 20 秒ぶん取る
sudo python3 tools/umon.py /dev/usbmon1 /tmp/cap.bin 20 &
sleep 1; sudo modprobe rtw88_8822bu
sleep 4; sudo ip link set wlan0 up
# 20 秒待つ（書き込みが止まってから終える。切れた捕捉は表も切れる）
wait
sudo pkill -f umon.py            # ← 自分の ssh コマンド行に当たらないよう注意
```

`sudo pkill -f "usbmon/1u"` のように自分のコマンド文字列を含むパターンで kill すると、
ssh セッションごと落ちる。PID を控えて kill すること。

## 3. チャネル切替（チャネル表用）

モニタモードにして、**基準チャネルに合わせる → 捕捉開始 → 目的チャネルへ切り替える**
を 1 チャネルずつ繰り返す。1 ファイル 1 チャネルにすると切り分けが確実。

```bash
IF=wlan0
sudo ip link set $IF down
sudo iw dev $IF set type monitor
sudo ip link set $IF up
for ch in 1 6 11 36 44 48 149 165; do
  if [ "$ch" -le 14 ]; then base=6; [ "$ch" = 6 ] && base=1; else base=44; [ "$ch" = 44 ] && base=36; fi
  sudo iw dev $IF set channel $base; sleep 1
  sudo python3 tools/umon.py /dev/usbmon1 /tmp/chan/ch$ch.bin 3 &
  sleep 1
  sudo iw dev $IF set channel $ch
  wait
done
```

## 3.5 モニタモード切替（モニタ表用）

`down`（マネージド）→ `set type monitor` → `up` を 1 手順ずつ捕捉する。
**`up` は 8 秒近くかかる**ので、窓は 20 秒取り、書き込みが止まったことを確認する。

```bash
IF=wlan0
sudo ip link set $IF down; sudo iw dev $IF set type managed
sudo ip link set $IF up; sleep 4

sudo python3 tools/umon.py /dev/usbmon1 /tmp/mon/down.bin 4 &
sleep 1; sudo ip link set $IF down; wait

sudo python3 tools/umon.py /dev/usbmon1 /tmp/mon/monitor.bin 4 &
sleep 1; sudo iw dev $IF set type monitor; wait      # 転送は 0 件になる

sudo python3 tools/umon.py /dev/usbmon1 /tmp/mon/up.bin 20 &
sleep 1; sudo ip link set $IF up; wait

# 抜く前に安全な状態へ戻す（モニタのまま QEMU から外すと固着しやすい）
sudo iw dev $IF set channel 6
sudo ip link set $IF down; sudo iw dev $IF set type managed
```

```bash
python3 tools/gen_monitor.py /tmp/mon/down.bin /tmp/mon/up.bin --out rtl8812bu/tables
```

`gen_monitor.py` は「書き込みが連続している最大の区間」だけを表にする
（その後 2 秒ごとに続く背景の書き込みは入れない）。

## 4. RX のレイアウト確認（tcpdump と同時に取る）

usbmon のバルク IN と、カーネルが解釈したフレームを突き合わせる。

```bash
sudo iw dev $IF set channel 6
sudo tcpdump -i $IF -w /tmp/mon6.pcap -s 0 -U &
sudo python3 tools/umon.py /dev/usbmon1 /tmp/rx6.bin 12
sudo kill %1
```

pcap から radiotap を除いたフレームを usbmon のデータ内で検索すると、
前置きの長さ（この機種は 56 バイト）を確定できる。

## 5. 表の生成

```bash
python3 tools/gen_init.py /tmp/cap.bin --out rtl8812bu/tables
python3 tools/gen_chan.py /tmp/chan    --out rtl8812bu/tables
python3 tests/test_offline.py
```

## Android（root）で取る場合

```sh
su -c 'mount -t debugfs none /sys/kernel/debug'
su -c 'cat /sys/kernel/debug/usb/usbmon/0u' > /sdcard/usbmon.txt
```

カーネルが `CONFIG_USB_MON` を有効にしている必要がある。
バイナリ捕捉は `tools/umon.py` を Android 上で動かせれば同じ手順で取れる。

## 注意

- 取得したファイルには**他人のネットワークのフレーム**が入る。リポジトリに入れない
  （`.gitignore` で `captures/`, `*.pcap` を除外している）。
- 同じ初期状態から取らないと、表の前提が崩れる。とくに初期化列は
  「USB を再列挙した直後」から取り直すのが望ましい。
