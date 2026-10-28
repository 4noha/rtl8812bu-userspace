/*
 * esp32-sniffer.ino — 2.4GHz の 802.11 管理フレームを USB シリアルに流す
 *
 * 用途: RTL8812BU を AP として動かしている間、ESP32 で「実際に何が出ているか」を
 *       観測する（1 台のアダプタでは自分の送信を見られないため）。
 *
 * 出力形式（1 行 1 フレーム）:
 *     <ch> <rssi> <hex>
 *   ch   : 受信チャネル
 *   rssi : dBm（負値）
 *   hex  : フレーム全体（FCS 4 バイトを含む。解析側で落とす）
 *
 * 使い方:
 *   1. Arduino IDE で ESP32 ボードパッケージを入れ、このスケッチを書き込む
 *   2. シリアルモニタを 921600 baud で開く（閉じてから下のコマンドで読む）
 *   3. Mac 側: python3 tools/serial_sniff.py /dev/cu.usbserial-XXXX 30
 *
 * 注意:
 *   - ESP32 は 2.4GHz のみ。5GHz の検証には使えない。
 *   - チャネルは下の CHANNEL で固定する（AP と同じチャネルに合わせる）。
 *   - 制御フレームは既定で捨てる（管理フレームのみ）。必要なら FILTER を変える。
 */

#include <Arduino.h>
#include <WiFi.h>
#include "esp_wifi.h"

static const uint8_t CHANNEL = 6;      // AP に合わせる（1/6/11 など）
static const bool DROP_CONTROL = true; // 制御フレームを捨てる

static void sniff_cb(void *buf, wifi_promiscuous_pkt_type_t type) {
  const wifi_promiscuous_pkt_t *p = (const wifi_promiscuous_pkt_t *)buf;
  const uint8_t *f = p->payload;
  int len = p->rx_ctrl.sig_len;        // FCS を含む
  if (len < 24 || len > 512) return;
  uint8_t fc0 = f[0];
  uint8_t ftype = (fc0 >> 2) & 0x03;
  if (DROP_CONTROL && ftype == 1) return;
  if (ftype == 3) return;              // 拡張タイプは対象外

  Serial.printf("%u %d ", (unsigned)p->rx_ctrl.channel, (int)p->rx_ctrl.rssi);
  for (int i = 0; i < len; i++) Serial.printf("%02x", f[i]);
  Serial.println();
}

void setup() {
  Serial.begin(921600);
  delay(300);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  delay(100);

  wifi_promiscuous_filter_t filt;
  filt.filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT;
  esp_wifi_set_promiscuous_filter(&filt);
  esp_wifi_set_promiscuous_rx_cb(&sniff_cb);
  esp_wifi_set_channel(CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(true);

  Serial.printf("# ready ch=%u (mgmt only)\n", CHANNEL);
}

void loop() {
  // シリアルからのコマンド: "c<ch>" でチャネル変更、"i" で情報表示
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.startsWith("c") || cmd.startsWith("C")) {
      int ch = cmd.substring(1).toInt();
      if (ch >= 1 && ch <= 14) {
        esp_wifi_set_channel((uint8_t)ch, WIFI_SECOND_CHAN_NONE);
        Serial.printf("# ch=%d\n", ch);
      }
    } else if (cmd == "i") {
      uint8_t prim = 0;
      wifi_second_chan_t sec;
      esp_wifi_get_channel(&prim, &sec);
      Serial.printf("# info ch=%u\n", prim);
    }
  }
  delay(20);
}
