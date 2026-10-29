// ESP32-C3 を STA として AP に接続し、60606 に HTTP を投げる（TCP/HTTP の疎通試験）。
#include <Arduino.h>
#include <WiFi.h>
#include <string.h>

static const char *SSID = "UN15TD8-TEST";
static const char *PSK = "12345678";
static const char *HOST = "192.168.0.201";
static const uint16_t PORT = 60020;
static const char *CGI = "/cgi-bin/remote_read_system_data.cgi";

static void try_once() {
  WiFiClient c;
  c.setTimeout(5);
  Serial.printf("[sta] connect %s:%u ...\n", HOST, PORT);
  if (!c.connect(HOST, PORT)) {
    Serial.println("[sta] connect NG");
    return;
  }
  Serial.println("[sta] connected");
  // モニターと同じ FAST ペアリング要求を送る
  const char *body =
      "<s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\""
      " s:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\">"
      "<s:Body><u:X_p9E_PairingRequest"
      " xmlns:u=\"urn:schemas-upnp-org:service:ConnectionManager:1\">"
      "<ControlPointUDN>uuid:4D454930-0500-1000-8000-80C755A054C2</ControlPointUDN>"
      "<DeviceUDN>uuid:4D454930-0100-1000-8000-80C755A17CED</DeviceUDN>"
      "</u:X_p9E_PairingRequest></s:Body></s:Envelope>";
  c.print("POST /cgi-bin/remote_read_system_data.cgi HTTP/1.1\r\n"
          "Host: 192.168.0.201:60606\r\n"
          "CONTENT-TYPE: text/xml; charset=\"utf-8\"\r\n"
          "SOAPACTION: \"urn:schemas-upnp-org:service:ConnectionManager:1#X_p9E_PairingRequest\"\r\n");
  c.printf("Content-Length: %u\r\nConnection: close\r\n\r\n%s", (unsigned)strlen(body), body);
  c.flush();

  unsigned long t0 = millis();
  size_t n = 0;
  while (millis() - t0 < 5000) {
    while (c.available()) {
      char buf[128];
      int k = c.readBytes(buf, sizeof(buf) - 1);
      if (k > 0) {
        buf[k] = 0;
        n += k;
        Serial.printf("[rx] %s", buf);
      }
    }
    if (!c.connected() && !c.available()) break;
    delay(10);
  }
  Serial.printf("\n[sta] total %u bytes, connected=%d\n", (unsigned)n, c.connected());
  c.stop();
}

void setup() {
  Serial.begin(921600);
  delay(300);
  Serial.println("\n[sta] boot");
  WiFi.mode(WIFI_STA);
  WiFi.begin(SSID, PSK);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.printf("[sta] wifi status=%d\n", WiFi.status());
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[sta] wifi NG");
    return;
  }
  Serial.printf("[sta] ip=%s rssi=%d ch=%d\n", WiFi.localIP().toString().c_str(),
                WiFi.RSSI(), WiFi.channel());
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) try_once();
  delay(5000);
}
