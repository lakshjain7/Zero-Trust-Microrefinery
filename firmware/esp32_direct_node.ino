/**
 * Zero-Trust Micro-Refinery — Direct MQTT Node Firmware
 * FreeRTOS Dual-Core | HMAC-SHA256 | INA219 | Laptop Star Topology
 *
 * Each ESP32 connects directly to the laptop's MQTT broker.
 * All nodes receive all commands over `refinery/cmd/pump`.
 * Core 0 verifies the HMAC, and checks if `pump` == `NODE_PUMP_ID`.
 * If it matches, Core 1 executes the relay and reads power.
 */

#include "mbedtls/md.h"
#include <Adafruit_INA219.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFi.h>
#include <Wire.h>

// ══════════════════════════════════════════════════════════════
// OPERATOR CONFIGURATION (Change per Node!)
// ══════════════════════════════════════════════════════════════
#define NODE_PUMP_ID "red" // "red", "blue", "yellow", or "purple"

const char *WIFI_SSID     = "<YOUR_HOTSPOT_SSID>";
const char *WIFI_PASSWORD = "<YOUR_HOTSPOT_PASSWORD>";
const char *MQTT_PASSWORD = "<YOUR_MQTT_PASSWORD>";

// ══════════════════════════════════════════════════════════════
// FIXED CONSTANTS
// ══════════════════════════════════════════════════════════════
const char *SECRET_KEY    = "<YOUR_SECRET_KEY>";
const char *MQTT_BROKER   = "<YOUR_BROKER_IP>";
const int   MQTT_PORT     = 1883;
const char *MQTT_USERNAME = "refinery_node";
char        CLIENT_ID[32];

// Hardware Pins
#define RELAY_PIN    25
#define INA219_SDA   21
#define INA219_SCL   22

// ── RELAY POLARITY ──────────────────────────────────────────
// Most inexpensive optocoupler relay modules are ACTIVE LOW:
//   LOW  → relay coil energised → contacts CLOSED  → pump ON
//   HIGH → relay coil de-energised → contacts OPEN  → pump OFF
// If your relay clicks at boot (with HIGH) you have a normally-closed
// board; swap the defines below.
#define RELAY_ON  LOW
#define RELAY_OFF HIGH
// ─────────────────────────────────────────────────────────────

#define CMD_TIME_WINDOW_S 5  // increased to 5s to tolerate minor clock drift

// MQTT Topics
#define TOPIC_CMD_PUMP   "refinery/cmd/pump"
#define TOPIC_CMD_SYNC   "refinery/cmd/sync"
#define TOPIC_TELEM_POWER "refinery/telemetry/power"
#define TOPIC_TELEM_DIST  "refinery/telemetry/distance"
#define TOPIC_TELEM_ACK   "refinery/telemetry/ack"
#define TOPIC_TELEM_HB    "refinery/telemetry/heartbeat"
#define TOPIC_TELEM_BOOT  "refinery/telemetry/boot"
#define TOPIC_TELEM_SEC   "refinery/telemetry/security"

// ══════════════════════════════════════════════════════════════
// STATE
// ══════════════════════════════════════════════════════════════
WiFiClient    wifiClient;
PubSubClient  mqttClient(wifiClient);
Adafruit_INA219 ina219;
QueueHandle_t cmdQueue;

struct PumpCommand {
  char pump[16];
  int  duration_ms;
  char packet_id[64];
  bool emergency;
};

volatile uint32_t lastReceivedNonce  = 0;
char executedPacketIds[20][64];
int  packetIdWriteIdx = 0;

volatile uint8_t consecutiveDriftCount = 0;
volatile bool    clockSynced           = false;
volatile int64_t unixTimeOffset        = 0;
volatile bool    isExecuting           = false;

// ══════════════════════════════════════════════════════════════
// UTILITY
// ══════════════════════════════════════════════════════════════
int64_t getCurrentUnixTime() {
  return (int64_t)(millis() / 1000) + unixTimeOffset;
}

void computeHMAC(const char *canonical, char *out_hex) {
  unsigned char hmac[32];
  mbedtls_md_context_t ctx;
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 1);
  mbedtls_md_hmac_starts(&ctx, (const unsigned char *)SECRET_KEY, strlen(SECRET_KEY));
  mbedtls_md_hmac_update(&ctx, (const unsigned char *)canonical, strlen(canonical));
  mbedtls_md_hmac_finish(&ctx, hmac);
  mbedtls_md_free(&ctx);
  for (int i = 0; i < 32; i++) sprintf(out_hex + (i * 2), "%02x", hmac[i]);
  out_hex[64] = '\0';
}

bool constantTimeCompare(const char *a, const char *b, size_t len) {
  unsigned char result = 0;
  for (size_t i = 0; i < len; i++) result |= ((unsigned char)a[i]) ^ ((unsigned char)b[i]);
  return result == 0;
}

bool isPacketAlreadyExecuted(const char *packet_id) {
  for (int i = 0; i < 20; i++)
    if (strlen(executedPacketIds[i]) > 0 && strcmp(executedPacketIds[i], packet_id) == 0)
      return true;
  return false;
}

void addPacketToBuffer(const char *packet_id) {
  strncpy(executedPacketIds[packetIdWriteIdx], packet_id, 63);
  packetIdWriteIdx = (packetIdWriteIdx + 1) % 20;
}

void publishSecurityEvent(const char *event, int consecutive = 0, float delta = 0) {
  StaticJsonDocument<256> doc;
  doc["event"]       = event;
  doc["ts"]          = getCurrentUnixTime();
  doc["consecutive"] = consecutive;
  if (delta > 0) doc["delta"] = delta;
  char buf[256];
  serializeJson(doc, buf);
  mqttClient.publish(TOPIC_TELEM_SEC, buf);
}

void publishAck(const char *packet_id, const char *pump_name, const char *status) {
  int64_t ts = getCurrentUnixTime();
  char canonical[256];
  snprintf(canonical, sizeof(canonical), "ack|%s|%s|%s|%lld", packet_id, pump_name, status, ts);
  char sig[65];
  computeHMAC(canonical, sig);

  StaticJsonDocument<512> doc;
  doc["packet_id"] = packet_id;
  doc["pump"]      = pump_name;
  doc["status"]    = status;
  doc["ts"]        = ts;
  doc["sig"]       = sig;
  char buf[512];
  serializeJson(doc, buf);
  mqttClient.publish(TOPIC_TELEM_ACK, buf);
}

// ══════════════════════════════════════════════════════════════
// MQTT CALLBACK (Core 0 - Full Security Gate Logging)
// ══════════════════════════════════════════════════════════════
void mqttCallback(char *topic, byte *payload, unsigned int length) {
  // ── GATE 0: Valid JSON ──────────────────────────────────────
  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, payload, length);
  if (err) {
    Serial.printf("[DROP] GATE 0 - JSON PARSE FAIL: %s\n", err.c_str());
    return;
  }
  String topicStr(topic);
  Serial.printf("\n[CMD] Received on topic: %s\n", topic);

  if (topicStr == TOPIC_CMD_SYNC) {
    int64_t syncTs = doc["ts"] | 0;
    if (syncTs > 0) {
      unixTimeOffset = syncTs - (int64_t)(millis() / 1000);
      clockSynced = true;
      consecutiveDriftCount = 0;
      Serial.printf("[SYNC] Clock synchronized. Offset=%lld, Now=%lld\n", 
                    unixTimeOffset, getCurrentUnixTime());
    } else {
      Serial.println("[SYNC] WARN: sync packet had ts=0, ignoring.");
    }
    return;
  }

  if (topicStr != TOPIC_CMD_PUMP) {
    Serial.printf("[INFO] Ignored message on unknown topic: %s\n", topic);
    return;
  }

  // ── GATE 1: MQTT connected ──────────────────────────────────
  if (!mqttClient.connected()) {
    Serial.println("[DROP] GATE 1 - MQTT DISCONNECTED");
    return;
  }
  Serial.println("[GATE 1] MQTT connected: PASS");

  bool      isEmergency = doc["emergency"] | false;
  const char *pump      = doc["pump"]      | "";
  long    duration_ms   = doc["duration_ms"] | 0;
  int64_t ts            = doc["ts"]   | 0;
  uint32_t nonce        = doc["nonce"] | 0;
  const char *packet_id = doc["packet_id"] | "";
  const char *sig       = doc["sig"]       | "";

  Serial.printf("[CMD] pump='%s' duration=%ldms ts=%lld nonce=%u pkt=%.8s...\n",
                pump, duration_ms, ts, nonce, packet_id);

  // ── EMERGENCY HALT PATH ─────────────────────────────────────
  if (isEmergency) {
    Serial.println("[EMERGENCY] Halt received — verifying...");
    char canonical[256];
    snprintf(canonical, sizeof(canonical), "all|0|%lld|%u|%s", ts, nonce, packet_id);
    char expectedSig[65];
    computeHMAC(canonical, expectedSig);
    if (constantTimeCompare(expectedSig, sig, 64)) {
      Serial.println("[EMERGENCY] HMAC OK — engaging fail-safe RELAY_OFF");
      digitalWrite(RELAY_PIN, RELAY_OFF);
      xQueueReset(cmdQueue);
      publishAck(packet_id, "all", "EMERGENCY_HALT");
    } else {
      Serial.println("[EMERGENCY] HMAC FAIL — ignoring spoofed halt");
      publishSecurityEvent("HMAC_FAIL");
    }
    return;
  }

  // ── GATE 2: Clock sync status ───────────────────────────────
  if (!clockSynced) {
    Serial.println("[DROP] GATE 2 - CLOCK NOT YET SYNCED (waiting for refinery/cmd/sync)");
    return;
  }
  Serial.println("[GATE 2] Clock synced: PASS");

  // ── GATE 3: Time drift ──────────────────────────────────────
  int64_t now   = getCurrentUnixTime();
  float   drift = abs((float)(now - ts));
  if (drift > CMD_TIME_WINDOW_S) {
    Serial.printf("[DROP] GATE 3 - TIME DRIFT=%.1fs > %ds | local=%lld pkt=%lld\n",
                  drift, CMD_TIME_WINDOW_S, now, ts);
    if (++consecutiveDriftCount >= 3) {
      digitalWrite(RELAY_PIN, RELAY_OFF);
      publishSecurityEvent("CLOCK_FAULT");
    } else {
      publishSecurityEvent("TIME_DRIFT");
    }
    return;
  }
  consecutiveDriftCount = 0;
  Serial.printf("[GATE 3] Time drift=%.1fs: PASS\n", drift);

  // ── GATE 4: Replay (nonce) ──────────────────────────────────
  if (nonce <= lastReceivedNonce) {
    Serial.printf("[DROP] GATE 4 - REPLAY: nonce %u <= last %u\n", nonce, lastReceivedNonce);
    publishSecurityEvent("REPLAY");
    return;
  }
  Serial.printf("[GATE 4] Nonce %u > %u: PASS\n", nonce, lastReceivedNonce);

  // ── GATE 5: Idempotency ─────────────────────────────────────
  if (isPacketAlreadyExecuted(packet_id)) {
    Serial.printf("[DROP] GATE 5 - DUPLICATE packet_id=%s\n", packet_id);
    return;
  }
  Serial.println("[GATE 5] Idempotency: PASS");

  // ── GATE 6: HMAC ────────────────────────────────────────────
  char canonical[256], expectedSig[65];
  snprintf(canonical, sizeof(canonical), "%s|%ld|%lld|%u|%s",
           pump, duration_ms, ts, nonce, packet_id);
  Serial.printf("[GATE 6] Verifying canonical: '%s'\n", canonical);
  computeHMAC(canonical, expectedSig);
  if (!constantTimeCompare(expectedSig, sig, 64)) {
    Serial.printf("[DROP] GATE 6 - HMAC FAIL\n  Expected: %.16s...\n  Got:      %.16s...\n",
                  expectedSig, sig);
    publishSecurityEvent("HMAC_FAIL");
    return;
  }
  lastReceivedNonce = nonce;
  Serial.println("[GATE 6] HMAC verified: PASS");

  // ── GATE 7: Node filter ─────────────────────────────────────
  if (strcasecmp(pump, NODE_PUMP_ID) != 0) {
    Serial.printf("[DROP] GATE 7 - WRONG NODE: cmd for '%s', I am '%s'\n",
                  pump, NODE_PUMP_ID);
    return;  // correct behaviour — not an error, just not for us
  }
  Serial.printf("[GATE 7] Node ID '%s' matches: PASS\n", NODE_PUMP_ID);

  // ── GATE 8: Queue ───────────────────────────────────────────
  PumpCommand cmd;
  strncpy(cmd.pump,      pump,      15);
  cmd.duration_ms = duration_ms;
  strncpy(cmd.packet_id, packet_id, 63);
  cmd.emergency = false;

  if (xQueueSend(cmdQueue, &cmd, 0) != pdTRUE) {
    Serial.println("[DROP] GATE 8 - QUEUE FULL (previous command still running)");
    return;
  }
  Serial.printf("[GATE 8] Queued %ldms for '%s' — relay will fire shortly!\n",
                duration_ms, pump);
}

// ══════════════════════════════════════════════════════════════
// CORE 0: Security Watchdog & Comms
// ══════════════════════════════════════════════════════════════
void core0_security_watchdog(void *param) {
  unsigned long lastHB = 0;
  while (true) {
    if (!mqttClient.connected()) {
      digitalWrite(RELAY_PIN, RELAY_OFF); // Fail-safe relay stays OFF when disconnected
      Serial.printf("[MQTT] Connecting to %s...\n", MQTT_BROKER);
      if (mqttClient.connect(CLIENT_ID, MQTT_USERNAME, MQTT_PASSWORD)) {
        Serial.println("[MQTT] Connected successfully!");
        mqttClient.subscribe(TOPIC_CMD_PUMP);
        mqttClient.subscribe(TOPIC_CMD_SYNC);
      } else {
        Serial.printf("[MQTT] FAILED. State: %d\n", mqttClient.state());
        vTaskDelay(5000 / portTICK_PERIOD_MS);
      }
    }
    mqttClient.loop();

    if (millis() - lastHB >= 500 && mqttClient.connected()) {
      lastHB = millis();
      int64_t ts = getCurrentUnixTime();
      char canonical[64], sig[65];
      snprintf(canonical, sizeof(canonical), "heartbeat|%lld", ts);
      computeHMAC(canonical, sig);
      StaticJsonDocument<128> doc;
      doc["ts"]  = ts;
      doc["sig"] = sig;
      char buf[128];
      serializeJson(doc, buf);
      mqttClient.publish(TOPIC_TELEM_HB, buf);
    }
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

// ══════════════════════════════════════════════════════════════
// CORE 1: Physical Worker (Opto-Relay & INA219)
// ══════════════════════════════════════════════════════════════
void core1_physical_worker(void *param) {
  PumpCommand cmd;
  unsigned long lastSensorPoll = 0;

  while (true) {
    if (millis() - lastSensorPoll >= 500) {
      lastSensorPoll = millis();
      int64_t ts = getCurrentUnixTime();

      float mA = ina219.getCurrent_mA();
      if (mA < 0) mA = 0;

      char pC[128], pS[65];
      snprintf(pC, sizeof(pC), "power|%s|%.1f|%lld", NODE_PUMP_ID, mA, ts);
      computeHMAC(pC, pS);

      StaticJsonDocument<256> pDoc;
      pDoc["pump"] = NODE_PUMP_ID;
      pDoc["ma"]   = mA;
      pDoc["ts"]   = ts;
      pDoc["sig"]  = pS;
      char pB[256];
      serializeJson(pDoc, pB);
      mqttClient.publish(TOPIC_TELEM_POWER, pB);
    }

    if (xQueueReceive(cmdQueue, &cmd, pdMS_TO_TICKS(10)) == pdTRUE) {
      Serial.printf("[RELAY] ACTUATING: Pump '%s' for %ldms (packet: %.8s...)\n", 
                    cmd.pump, cmd.duration_ms, cmd.packet_id);
      isExecuting = true;
      digitalWrite(RELAY_PIN, RELAY_ON);
      vTaskDelay(cmd.duration_ms / portTICK_PERIOD_MS);
      digitalWrite(RELAY_PIN, RELAY_OFF);
      isExecuting = false;
      Serial.printf("[RELAY] COMPLETED: Pump '%s' execution finished.\n", cmd.pump);
      
      addPacketToBuffer(cmd.packet_id);
      publishAck(cmd.packet_id, cmd.pump, "EXECUTED");
      Serial.println("[ACK] Published EXECUTED status to broker");
    }
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

// ══════════════════════════════════════════════════════════════
// SETUP & RTOS INIT
// ══════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  Serial.println("\n\n--- ESP32 BOOTING ---");
  snprintf(CLIENT_ID, sizeof(CLIENT_ID), "node_%s", NODE_PUMP_ID);
  Serial.printf("[NODE] ID: %s\n", CLIENT_ID);
  Serial.printf("[NODE] Pump target: '%s'\n", NODE_PUMP_ID);
  Serial.printf("[RELAY] Polarity: ON=%s OFF=%s\n",
                RELAY_ON == LOW ? "LOW" : "HIGH",
                RELAY_OFF == LOW ? "LOW" : "HIGH");

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, RELAY_OFF); // Fail-safe: relay OFF at boot

  Wire.begin(INA219_SDA, INA219_SCL);
  if (!ina219.begin())
    Serial.println("[WARN] INA219 not found — power telemetry will read 0mA");

  Serial.printf("Connecting to Wi-Fi: %s\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());

  mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setBufferSize(512);

  cmdQueue = xQueueCreate(1, sizeof(PumpCommand));

  xTaskCreatePinnedToCore(core0_security_watchdog, "Core0", 8192, NULL, 1, NULL, 0);
  xTaskCreatePinnedToCore(core1_physical_worker,   "Core1", 8192, NULL, 1, NULL, 1);
}

void loop() { vTaskDelete(NULL); }
