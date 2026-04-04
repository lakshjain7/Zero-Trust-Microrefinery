/**
 * Zero-Trust Micro-Refinery — Master Node Firmware (ESP32)
 *
 * This node acts as the secure gateway. It connects to WiFi/MQTT,
 * runs the Cybersecurity verifications (HMAC-SHA256, Replay, Time Drift),
 * and routes verified commands to Slave nodes via ESP-NOW.
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <esp_now.h>
#include "mbedtls/md.h"

// ══════════════════════════════════════════════════════════════
// OPERATOR SETUP
// ══════════════════════════════════════════════════════════════
const char* WIFI_SSID     = "YOUR_HOTSPOT_SSID";
const char* WIFI_PASSWORD = "YOUR_HOTSPOT_PASSWORD";
const char* MQTT_PASSWORD = "ChangeMeAtLeast16Chars";

// TODO: Replace with the actual MAC addresses of your 3 slaved ESP32s
uint8_t slaveRedMAC[]    = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
uint8_t slaveBlueMAC[]   = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
uint8_t slaveYellowMAC[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

// ══════════════════════════════════════════════════════════════
// FIXED CONSTANTS
// ══════════════════════════════════════════════════════════════
const char* SECRET_KEY = "H4ckath0n_TrU5t_K3y_99!";
const char* MQTT_BROKER   = "192.168.137.1";
const int   MQTT_PORT     = 1883;
const char* MQTT_USERNAME = "refinery_node";
const char* CLIENT_ID     = "refinery_master";

#define TOPIC_CMD_PUMP     "refinery/cmd/pump"
#define TOPIC_CMD_SYNC     "refinery/cmd/sync"
#define TOPIC_TELEM_POWER  "refinery/telemetry/power"
#define TOPIC_TELEM_ACK    "refinery/telemetry/ack"
#define TOPIC_TELEM_HB     "refinery/telemetry/heartbeat"
#define TOPIC_TELEM_BOOT   "refinery/telemetry/boot"
#define TOPIC_TELEM_SEC    "refinery/telemetry/security"

// ══════════════════════════════════════════════════════════════
// ESP-NOW STRUCTURES
// ══════════════════════════════════════════════════════════════
typedef struct struct_cmd {
    char pump[16];
    int duration_ms;
    char packet_id[64];
    bool emergency;
} struct_cmd;

typedef struct struct_telem {
    char pump[16];
    float voltage;
    float current_mA;
    bool is_ack;
    char packet_id[64];
    char status[16];
} struct_telem;

// ══════════════════════════════════════════════════════════════
// GLOBAL STATE
// ══════════════════════════════════════════════════════════════
WiFiClient    wifiClient;
PubSubClient  mqttClient(wifiClient);

volatile uint32_t lastReceivedNonce = 0;
char executedPacketIds[20][64];
int  packetIdWriteIdx = 0;

volatile uint8_t consecutiveDriftCount = 0;
volatile bool clockSynced = false;
volatile int64_t unixTimeOffset = 0;

esp_now_peer_info_t peerInfo;

// ══════════════════════════════════════════════════════════════
// UTILITY FUNCTIONS (same as previous firmware)
// ══════════════════════════════════════════════════════════════
int64_t getCurrentUnixTime() {
    return (int64_t)(millis() / 1000) + unixTimeOffset;
}

void computeHMAC(const char* canonical, char* out_hex) {
    unsigned char hmac[32];
    mbedtls_md_context_t ctx;
    const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);

    mbedtls_md_init(&ctx);
    mbedtls_md_setup(&ctx, info, 1);
    mbedtls_md_hmac_starts(&ctx, (const unsigned char*)SECRET_KEY, strlen(SECRET_KEY));
    mbedtls_md_hmac_update(&ctx, (const unsigned char*)canonical, strlen(canonical));
    mbedtls_md_hmac_finish(&ctx, hmac);
    mbedtls_md_free(&ctx);

    for (int i = 0; i < 32; i++) {
        sprintf(out_hex + (i * 2), "%02x", hmac[i]);
    }
    out_hex[64] = '\0';
}

bool constantTimeCompare(const char* a, const char* b, size_t len) {
    unsigned char result = 0;
    for (size_t i = 0; i < len; i++) {
        result |= ((unsigned char)a[i]) ^ ((unsigned char)b[i]);
    }
    return result == 0;
}

void publishSecurityEvent(const char* event, int consecutive = 0, float delta = 0) {
    StaticJsonDocument<256> doc;
    doc["event"]       = event;
    doc["ts"]          = getCurrentUnixTime();
    doc["consecutive"] = consecutive;
    if (delta > 0) doc["delta"] = delta;
    char buf[256];
    serializeJson(doc, buf);
    mqttClient.publish(TOPIC_TELEM_SEC, buf);
}

// ══════════════════════════════════════════════════════════════
// ESP-NOW RECEIVE CALLBACK (From Slaves to Master)
// ══════════════════════════════════════════════════════════════
void OnDataRecv(const uint8_t * mac, const uint8_t *incomingData, int len) {
    struct_telem telem;
    memcpy(&telem, incomingData, sizeof(telem));
    int64_t ts = getCurrentUnixTime();

    if (telem.is_ack) {
        // Build signed ACK and forward to MQTT
        char canonical[256];
        snprintf(canonical, sizeof(canonical), "ack|%s|%s|%s|%lld", 
                 telem.packet_id, telem.pump, telem.status, ts);
        char sig[65];
        computeHMAC(canonical, sig);

        StaticJsonDocument<512> doc;
        doc["packet_id"] = telem.packet_id;
        doc["pump"]      = telem.pump;
        doc["status"]    = telem.status;
        doc["ts"]        = ts;
        doc["sig"]       = sig;
        char buf[512];
        serializeJson(doc, buf);
        mqttClient.publish(TOPIC_TELEM_ACK, buf);
    } else {
        // Forward power telemetry to MQTT
        char canonical[64];
        snprintf(canonical, sizeof(canonical), "power|%.1f|%lld", telem.current_mA, ts);
        char sig[65];
        computeHMAC(canonical, sig);

        StaticJsonDocument<128> doc;
        doc["ma"]  = telem.current_mA;
        doc["ts"]  = ts;
        doc["sig"] = sig;
        char buf[128];
        serializeJson(doc, buf);
        mqttClient.publish(TOPIC_TELEM_POWER, buf);
    }
}

// ══════════════════════════════════════════════════════════════
// SECURITY VERIFICATION
// ══════════════════════════════════════════════════════════════
int verifyPacket(JsonDocument& doc) {
    const char* pump       = doc["pump"]       | "";
    long        duration_ms= doc["duration_ms"]| 0;
    int64_t     ts         = doc["ts"]         | 0;
    uint32_t    nonce      = doc["nonce"]      | 0;
    const char* packet_id  = doc["packet_id"]  | "";
    const char* sig        = doc["sig"]        | "";

    int64_t now = getCurrentUnixTime();
    float drift = abs((float)(now - ts));
    if (drift > 2) {
        consecutiveDriftCount++;
        publishSecurityEvent("TIME_DRIFT", consecutiveDriftCount, drift);
        if (consecutiveDriftCount >= 3) publishSecurityEvent("CLOCK_FAULT");
        return 1;
    } else consecutiveDriftCount = 0;

    if (nonce <= lastReceivedNonce) {
        publishSecurityEvent("REPLAY");
        return 2;
    }

    char canonical[256];
    snprintf(canonical, sizeof(canonical), "%s|%ld|%lld|%u|%s",
             pump, duration_ms, ts, nonce, packet_id);
    char expectedSig[65];
    computeHMAC(canonical, expectedSig);

    if (!constantTimeCompare(expectedSig, sig, 64)) {
        publishSecurityEvent("HMAC_FAIL");
        return 4;
    }

    lastReceivedNonce = nonce;
    return 0;
}

// ══════════════════════════════════════════════════════════════
// MQTT CALLBACK
// ══════════════════════════════════════════════════════════════
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    StaticJsonDocument<512> doc;
    if (deserializeJson(doc, payload, length)) return;

    String topicStr(topic);
    if (topicStr == TOPIC_CMD_SYNC) {
        int64_t syncTs = doc["ts"] | 0;
        if (syncTs > 0) {
            unixTimeOffset = syncTs - (int64_t)(millis() / 1000);
            clockSynced    = true;
            Serial.printf("[SYNC] Master clock synced.\n");
        }
        return;
    }

    if (topicStr == TOPIC_CMD_PUMP) {
        if (doc["emergency"] | false) {
            // Forward emergency to all slaves
            struct_cmd cmd;
            cmd.emergency = true;
            strcpy(cmd.pump, "all");
            esp_now_send(slaveRedMAC, (uint8_t *) &cmd, sizeof(cmd));
            esp_now_send(slaveBlueMAC, (uint8_t *) &cmd, sizeof(cmd));
            esp_now_send(slaveYellowMAC, (uint8_t *) &cmd, sizeof(cmd));
            return;
        }

        if (verifyPacket(doc) == 0) {
            struct_cmd cmd;
            strncpy(cmd.pump, doc["pump"] | "", 15);
            cmd.duration_ms = doc["duration_ms"] | 0;
            strncpy(cmd.packet_id, doc["packet_id"] | "", 63);
            cmd.emergency = false;

            // Route to correct slave
            uint8_t* targetMac = nullptr;
            if (strcmp(cmd.pump, "red") == 0) targetMac = slaveRedMAC;
            else if (strcmp(cmd.pump, "blue") == 0) targetMac = slaveBlueMAC;
            else if (strcmp(cmd.pump, "yellow") == 0) targetMac = slaveYellowMAC;

            if (targetMac) {
                esp_now_send(targetMac, (uint8_t *) &cmd, sizeof(cmd));
                Serial.printf("[ROUTER] Verified command for %s sent to slave via ESP-NOW\n", cmd.pump);
            }
        }
    }
}

// ══════════════════════════════════════════════════════════════
// SETUP & LOOP
// ══════════════════════════════════════════════════════════════
void setup() {
    Serial.begin(115200);

    WiFi.mode(WIFI_AP_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
    Serial.println("\n[WIFI] Connected");

    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);

    if (esp_now_init() != ESP_OK) {
        Serial.println("[ESP-NOW] Init Failed");
        return;
    }
    esp_now_register_recv_cb(OnDataRecv);

    peerInfo.channel = 0;  
    peerInfo.encrypt = false;
    
    memcpy(peerInfo.peer_addr, slaveRedMAC, 6);
    esp_now_add_peer(&peerInfo);
    memcpy(peerInfo.peer_addr, slaveBlueMAC, 6);
    esp_now_add_peer(&peerInfo);
    memcpy(peerInfo.peer_addr, slaveYellowMAC, 6);
    esp_now_add_peer(&peerInfo);
}

unsigned long lastHB = 0;

void loop() {
    if (!mqttClient.connected()) {
        if (mqttClient.connect(CLIENT_ID, MQTT_USERNAME, MQTT_PASSWORD)) {
            mqttClient.subscribe(TOPIC_CMD_PUMP);
            mqttClient.subscribe(TOPIC_CMD_SYNC);
        }
        delay(5000);
    }
    mqttClient.loop();

    if (millis() - lastHB > 500) {
        lastHB = millis();
        int64_t ts = getCurrentUnixTime();
        char canonical[64], sig[65];
        snprintf(canonical, sizeof(canonical), "heartbeat|%lld", ts);
        computeHMAC(canonical, sig);
        
        StaticJsonDocument<128> doc;
        doc["ts"] = ts; doc["sig"] = sig;
        char buf[128]; serializeJson(doc, buf);
        mqttClient.publish(TOPIC_TELEM_HB, buf);
    }
}
