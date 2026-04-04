/**
 * Zero-Trust Micro-Refinery — ESP32 Firmware v3.1
 * FreeRTOS Dual-Core | HMAC-SHA256 | HC-SR04 EMA | INA219 I2C
 *
 * Module 2:  FreeRTOS dual-core firmware
 * Module 1:  C++ cryptographic transport (mbedtls HMAC-SHA256)
 * Module 5:  Emergency halt handler
 * Section D: MQTT disconnect + time drift escalation
 *
 * REQUIRED LIBRARIES (Arduino Library Manager):
 *   - PubSubClient by Nick O'Leary
 *   - ArduinoJson by Benoit Blanchon
 *   - Wire (built-in)
 *   - mbedtls (built-in with ESP32 Arduino framework)
 *
 * PHYSICAL CONSTANTS (Section A — do not change):
 *   Pump 1 Red    GPIO 25  8.5 ml/sec
 *   Pump 2 Blue   GPIO 26  9.1 ml/sec
 *   Pump 3 Yellow GPIO 27  ~8.8 ml/sec (calibrated)
 *   HC-SR04 Trig GPIO 12 | Echo GPIO 14 (voltage divider on Echo!)
 *   INA219  SDA  GPIO 21 | SCL  GPIO 22
 *   Active LOW relay: GPIO LOW = pump ON, HIGH = pump OFF
 *
 * SECURITY NOTE: SECRET_KEY is hardcoded for demo. In production
 * this key MUST live in an HSM (Microchip ATECC608A). Hardcoding a
 * shared secret is a known anti-pattern outside controlled demos.
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include "mbedtls/md.h"

// ══════════════════════════════════════════════════════════════
// OPERATOR FILLS THESE
// ══════════════════════════════════════════════════════════════
const char* WIFI_SSID     = "YOUR_HOTSPOT_SSID";      // [OPERATOR FILLS THIS]
const char* WIFI_PASSWORD = "YOUR_HOTSPOT_PASSWORD";  // [OPERATOR FILLS THIS]
const char* MQTT_PASSWORD = "ChangeMeAtLeast16Chars";  // [OPERATOR FILLS THIS]

// ══════════════════════════════════════════════════════════════
// FIXED CONSTANTS (Section A — hardcoded, non-negotiable)
// ══════════════════════════════════════════════════════════════

// HMAC shared secret
// PRODUCTION WARNING: Move to ATECC608A HSM before real deployment.
const char* SECRET_KEY = "H4ckath0n_TrU5t_K3y_99!";

// MQTT broker
const char* MQTT_BROKER   = "192.168.137.1";
const int   MQTT_PORT     = 1883;
const char* MQTT_USERNAME = "refinery_node";
const char* CLIENT_ID     = "refinery_esp32";

// GPIO pins — Active LOW relay (LOW = ON, HIGH = OFF)
#define PUMP_RED_PIN    25
#define PUMP_BLUE_PIN   26
#define PUMP_YELLOW_PIN 27
#define HC_SR04_TRIG    12
#define HC_SR04_ECHO    14   // NOTE: 5V signal — use voltage divider!
#define INA219_SDA      21
#define INA219_SCL      22
#define INA219_ADDR     0x40

// HC-SR04 state thresholds (Section A)
#define DIST_EMPTY_MIN_CM  10.0f
#define DIST_EMPTY_MAX_CM  12.0f
#define DIST_FULL_MAX_CM    5.0f
#define DIST_NO_CUP_MIN_CM 15.0f

// EMA filter constants (Section A: α=0.2, 30% jitter rejection)
#define EMA_ALPHA          0.2f
#define EMA_JITTER_THRESH  0.3f   // 30% deviation → reject reading

// Time window for HMAC verification
#define CMD_TIME_WINDOW_S    2    // Reject commands older than 2s
#define DRIFT_ESCALATE_COUNT 3    // 3 consecutive = clock fault

// Telemetry interval
#define TELEM_INTERVAL_MS    500

// MQTT reconnect interval (Section D: try every 5 seconds while disconnected)
#define MQTT_RECONNECT_INTERVAL_MS 5000

// Rate limiter: max 5 messages/second on cmd/pump
#define RATE_LIMIT_MAX_MSG   5
#define RATE_LIMIT_WINDOW_MS 1000

// RTOS queue and task config
#define CMD_QUEUE_DEPTH      1
#define TASK_STACK_SIZE      8192

// Circular buffer size for executed packet IDs (idempotency)
#define PACKET_ID_BUFFER_SIZE 20

// ══════════════════════════════════════════════════════════════
// MQTT TOPICS
// ══════════════════════════════════════════════════════════════
#define TOPIC_CMD_PUMP     "refinery/cmd/pump"
#define TOPIC_CMD_SYNC     "refinery/cmd/sync"
#define TOPIC_TELEM_POWER  "refinery/telemetry/power"
#define TOPIC_TELEM_DIST   "refinery/telemetry/distance"
#define TOPIC_TELEM_ACK    "refinery/telemetry/ack"
#define TOPIC_TELEM_HB     "refinery/telemetry/heartbeat"
#define TOPIC_TELEM_BOOT   "refinery/telemetry/boot"
#define TOPIC_TELEM_SEC    "refinery/telemetry/security"

// ══════════════════════════════════════════════════════════════
// COMMAND STRUCT — passed through RTOS queue
// ══════════════════════════════════════════════════════════════
struct PumpCommand {
    char pump[16];
    int  duration_ms;
    char packet_id[64];
    bool emergency;
};

// ══════════════════════════════════════════════════════════════
// GLOBAL STATE
// ══════════════════════════════════════════════════════════════
WiFiClient    wifiClient;
PubSubClient  mqttClient(wifiClient);
QueueHandle_t cmdQueue;

// Replay/nonce protection
volatile uint32_t lastReceivedNonce = 0;

// Idempotency: circular buffer of executed packet IDs
char executedPacketIds[PACKET_ID_BUFFER_SIZE][64];
int  packetIdWriteIdx = 0;

// Section D: Time drift escalation
volatile uint8_t consecutiveDriftCount = 0;
volatile bool    clockSynced = false;
volatile int64_t unixTimeOffset = 0;  // millis() offset to approximate Unix time

// Execution lock (belt-and-suspenders with RTOS queue depth=1)
volatile bool isExecuting = false;

// Rate limiter state
volatile unsigned long rateLimitWindowStart = 0;
volatile int rateLimitMsgCount = 0;

// Sensor state
volatile float emaDistance = 12.0f;  // Start at EMPTY_CUP distance
volatile float lastValidEma = 12.0f;

// Section D: MQTT disconnect state
volatile bool mqttDisconnected = false;
volatile unsigned long lastMqttReconnectAttempt = 0;

// ══════════════════════════════════════════════════════════════
// UTILITY: Get current Unix time approximation
// ══════════════════════════════════════════════════════════════
int64_t getCurrentUnixTime() {
    return (int64_t)(millis() / 1000) + unixTimeOffset;
}

// ══════════════════════════════════════════════════════════════
// UTILITY: Compute HMAC-SHA256 using mbedtls (built-in)
// Returns hex string in out_hex (must be 65 bytes)
// ══════════════════════════════════════════════════════════════
void computeHMAC(const char* canonical, char* out_hex) {
    unsigned char hmac[32];
    mbedtls_md_context_t ctx;
    const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);

    mbedtls_md_init(&ctx);
    mbedtls_md_setup(&ctx, info, 1);  // 1 = HMAC mode
    mbedtls_md_hmac_starts(&ctx,
        (const unsigned char*)SECRET_KEY, strlen(SECRET_KEY));
    mbedtls_md_hmac_update(&ctx,
        (const unsigned char*)canonical, strlen(canonical));
    mbedtls_md_hmac_finish(&ctx, hmac);
    mbedtls_md_free(&ctx);

    for (int i = 0; i < 32; i++) {
        sprintf(out_hex + (i * 2), "%02x", hmac[i]);
    }
    out_hex[64] = '\0';
}

// ══════════════════════════════════════════════════════════════
// UTILITY: Constant-time string comparison (prevent timing attacks)
// ══════════════════════════════════════════════════════════════
bool constantTimeCompare(const char* a, const char* b, size_t len) {
    unsigned char result = 0;
    for (size_t i = 0; i < len; i++) {
        result |= ((unsigned char)a[i]) ^ ((unsigned char)b[i]);
    }
    return result == 0;
}

// ══════════════════════════════════════════════════════════════
// UTILITY: Sign a telemetry payload (canonical string → HMAC hex)
// ══════════════════════════════════════════════════════════════
void signTelemetry(const char* type, const char* data, int64_t ts,
                   char* out_sig) {
    char canonical[256];
    snprintf(canonical, sizeof(canonical), "%s|%s|%lld", type, data, ts);
    computeHMAC(canonical, out_sig);
}

// ══════════════════════════════════════════════════════════════
// SECURITY: Check executed_packet_ids buffer (idempotency)
// ══════════════════════════════════════════════════════════════
bool isPacketAlreadyExecuted(const char* packet_id) {
    for (int i = 0; i < PACKET_ID_BUFFER_SIZE; i++) {
        if (strlen(executedPacketIds[i]) > 0 &&
            strcmp(executedPacketIds[i], packet_id) == 0) {
            return true;
        }
    }
    return false;
}

void addPacketToBuffer(const char* packet_id) {
    strncpy(executedPacketIds[packetIdWriteIdx], packet_id, 63);
    executedPacketIds[packetIdWriteIdx][63] = '\0';
    packetIdWriteIdx = (packetIdWriteIdx + 1) % PACKET_ID_BUFFER_SIZE;
}

// ══════════════════════════════════════════════════════════════
// SECURITY: Force all relays OFF (fail-safe)
// HIGH = relay deactivated = pump OFF (Active LOW relay)
// ══════════════════════════════════════════════════════════════
void allPumpsOff() {
    digitalWrite(PUMP_RED_PIN,    HIGH);
    digitalWrite(PUMP_BLUE_PIN,   HIGH);
    digitalWrite(PUMP_YELLOW_PIN, HIGH);
}

// ══════════════════════════════════════════════════════════════
// SECURITY: Publish security event to Python orchestrator
// ══════════════════════════════════════════════════════════════
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
// Module 1 C++: VERIFY INCOMING COMMAND
// Returns: 0=valid, 1=time_drift, 2=replay_nonce,
//          3=packet_id_dup, 4=hmac_fail
// ══════════════════════════════════════════════════════════════
int verifyPacket(JsonDocument& doc) {
    // Extract fields
    const char* pump       = doc["pump"]       | "";
    long        duration_ms= doc["duration_ms"]| 0;
    int64_t     ts         = doc["ts"]         | 0;
    uint32_t    nonce      = doc["nonce"]      | 0;
    const char* packet_id  = doc["packet_id"]  | "";
    const char* sig        = doc["sig"]        | "";

    // ── TIME WINDOW CHECK (Section D) ──
    int64_t now   = getCurrentUnixTime();
    float   drift = abs((float)(now - ts));

    if (drift > CMD_TIME_WINDOW_S) {
        consecutiveDriftCount++;

        // Publish TIME_DRIFT event with delta and consecutive count
        publishSecurityEvent("TIME_DRIFT", consecutiveDriftCount, drift);

        if (consecutiveDriftCount >= DRIFT_ESCALATE_COUNT) {
            // Section D: Clock fault — halt everything, await re-sync
            allPumpsOff();
            publishSecurityEvent("CLOCK_FAULT");
            // Clear queue — stale commands must not execute after re-sync
            xQueueReset(cmdQueue);
        }
        return 1;  // Time drift
    } else {
        consecutiveDriftCount = 0;
    }

    // ── NONCE CHECK (replay prevention) ──
    // SECURITY RATIONALE: Monotonically increasing nonce prevents replay attacks.
    // An attacker who captures a valid packet cannot replay it because the nonce
    // would be ≤ lastReceivedNonce, failing this check.
    if (nonce <= lastReceivedNonce) {
        publishSecurityEvent("REPLAY");
        return 2;  // Replay / nonce too low
    }

    // ── PACKET ID IDEMPOTENCY CHECK ──
    // SECURITY RATIONALE: Even if the nonce check passes (e.g., out-of-order
    // delivery), a packet_id we've already executed is dropped. This prevents
    // the same physical pump actuation from happening twice due to broker retries.
    if (isPacketAlreadyExecuted(packet_id)) {
        // Publish ALREADY_EXECUTED ack so Python doesn't count it as a strike
        StaticJsonDocument<256> ackDoc;
        ackDoc["packet_id"] = packet_id;
        ackDoc["status"]    = "ALREADY_EXECUTED";
        ackDoc["ts"]        = getCurrentUnixTime();
        char ackBuf[256];
        serializeJson(ackDoc, ackBuf);
        mqttClient.publish(TOPIC_TELEM_ACK, ackBuf);
        return 3;  // Duplicate
    }

    // ── HMAC VERIFICATION ──
    // Reconstruct the exact canonical string Python signed.
    // Format: "{pump}|{duration_ms}|{ts}|{nonce}|{packet_id}"
    // This string is agreed between Python and C++. Any mismatch fails here.
    char canonical[256];
    snprintf(canonical, sizeof(canonical),
             "%s|%ld|%lld|%u|%s",
             pump, duration_ms, ts, nonce, packet_id);

    char expectedSig[65];
    computeHMAC(canonical, expectedSig);

    // Constant-time comparison — prevents timing side-channel attacks
    if (!constantTimeCompare(expectedSig, sig, 64)) {
        publishSecurityEvent("HMAC_FAIL");
        return 4;  // HMAC mismatch
    }

    // All checks passed — update nonce
    lastReceivedNonce = nonce;
    return 0;  // Valid
}

// ══════════════════════════════════════════════════════════════
// SENSOR: Read HC-SR04 with EMA filter
// ══════════════════════════════════════════════════════════════
float readUltrasonicEMA() {
    // Trigger pulse
    digitalWrite(HC_SR04_TRIG, LOW);
    delayMicroseconds(2);
    digitalWrite(HC_SR04_TRIG, HIGH);
    delayMicroseconds(10);
    digitalWrite(HC_SR04_TRIG, LOW);

    // Measure echo (timeout 25ms → ~430cm max range, well beyond our use case)
    long duration = pulseIn(HC_SR04_ECHO, HIGH, 25000);
    if (duration == 0) return emaDistance;  // Timeout — return last EMA

    float rawCm = (duration * 0.034f) / 2.0f;

    // Sanity bounds: 0–400cm
    if (rawCm < 0 || rawCm > 400) return emaDistance;

    // Jitter rejection: if raw deviates > 30% from EMA, discard
    // SECURITY RATIONALE: Raw HC-SR04 over water is noisy due to ripple
    // scatter. A single bad reading could falsely signal EMPTY_CUP or FULL_CUP,
    // causing the AI to fire pumps at the wrong time. EMA + jitter rejection
    // ensures only stable, consistent readings reach state logic.
    if (lastValidEma > 0) {
        float deviation = abs(rawCm - lastValidEma) / lastValidEma;
        if (deviation > EMA_JITTER_THRESH) {
            return emaDistance;  // Reject jitter artifact
        }
    }

    // Apply EMA: ema = α * raw + (1-α) * ema_prev
    emaDistance   = EMA_ALPHA * rawCm + (1.0f - EMA_ALPHA) * emaDistance;
    lastValidEma  = emaDistance;
    return emaDistance;
}

// Classify distance into cup state string
const char* classifyCupState(float ema_cm) {
    if (ema_cm < DIST_FULL_MAX_CM)                          return "FULL_CUP";
    if (ema_cm >= DIST_EMPTY_MIN_CM && ema_cm <= DIST_EMPTY_MAX_CM) return "EMPTY_CUP";
    if (ema_cm > DIST_NO_CUP_MIN_CM)                        return "NO_CUP";
    return "EMPTY_CUP";  // Default: treat ambiguous as EMPTY (conservative)
}

// ══════════════════════════════════════════════════════════════
// SENSOR: Read INA219 current (simplified I2C read)
// Full Adafruit INA219 library avoided to keep dependencies minimal.
// ══════════════════════════════════════════════════════════════
float readINA219mA() {
    // Register 0x04 = Current register
    Wire.beginTransmission(INA219_ADDR);
    Wire.write(0x04);
    Wire.endTransmission(false);

    Wire.requestFrom(INA219_ADDR, (uint8_t)2);
    if (Wire.available() < 2) return -1.0f;  // I2C error

    int16_t raw = (Wire.read() << 8) | Wire.read();

    // INA219 default: 1 LSB = 0.1mA (calibration register at default)
    // For more accuracy, calibrate the INA219 for your shunt resistor.
    float mA = raw * 0.1f;

    // Sanity bounds (Section A)
    if (mA < 0.0f || mA > 600.0f) return -1.0f;  // SENSOR_ERROR
    return mA;
}

// ══════════════════════════════════════════════════════════════
// MQTT: Publish signed ACK
// ══════════════════════════════════════════════════════════════
void publishAck(const char* packet_id, const char* pump_name, const char* status) {
    int64_t ts = getCurrentUnixTime();

    // Sign the ACK: canonical = "ack|{packet_id}|{pump}|{status}|{ts}"
    char canonical[256];
    snprintf(canonical, sizeof(canonical),
             "ack|%s|%s|%s|%lld", packet_id, pump_name, status, ts);
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
// MQTT CALLBACK (Core 0 — Security Watchdog)
// ══════════════════════════════════════════════════════════════
void mqttCallback(char* topic, byte* payload_bytes, unsigned int length) {
    // ── RATE LIMITER: > 5 msg/sec = drop + log ──
    // SECURITY RATIONALE: Rate limiting prevents flooding attacks where an
    // adversary sends thousands of crafted packets trying to find timing
    // vulnerabilities or exhaust the HMAC verification path.
    unsigned long now = millis();
    if (now - rateLimitWindowStart < RATE_LIMIT_WINDOW_MS) {
        rateLimitMsgCount++;
        if (rateLimitMsgCount > RATE_LIMIT_MAX_MSG) {
            publishSecurityEvent("RATE_LIMIT_HIT");
            return;
        }
    } else {
        rateLimitWindowStart = now;
        rateLimitMsgCount    = 1;
    }

    // ── PARSE JSON ──
    StaticJsonDocument<512> doc;
    DeserializationError err = deserializeJson(doc, payload_bytes, length);
    if (err) return;  // Malformed JSON — drop silently

    String topicStr(topic);

    // ── CLOCK SYNC ──
    if (topicStr == TOPIC_CMD_SYNC) {
        // Python sends a signed timestamp for clock synchronization
        int64_t syncTs = doc["ts"] | 0;
        if (syncTs > 0) {
            // Calculate offset so getCurrentUnixTime() returns approximate Unix time
            unixTimeOffset = syncTs - (int64_t)(millis() / 1000);
            clockSynced    = true;
            consecutiveDriftCount = 0;  // Reset drift counter after sync
            Serial.printf("[SYNC] Clock synchronized. Offset: %lld\n", unixTimeOffset);
        }
        return;
    }

    // ── COMMAND ──
    if (topicStr == TOPIC_CMD_PUMP) {
        // Check for emergency halt first — highest priority path
        bool isEmergency = doc["emergency"] | false;

        if (isEmergency) {
            // Still verify HMAC on emergency commands — zero-trust always
            // Canonical for emergency: "all|0|{ts}|{nonce}|{packet_id}"
            int64_t     ts        = doc["ts"]        | 0;
            uint32_t    nonce     = doc["nonce"]      | 0;
            const char* packet_id = doc["packet_id"]  | "";
            const char* sig       = doc["sig"]        | "";

            char canonical[256];
            snprintf(canonical, sizeof(canonical),
                     "all|0|%lld|%u|%s", ts, nonce, packet_id);
            char expectedSig[65];
            computeHMAC(canonical, expectedSig);

            if (constantTimeCompare(expectedSig, sig, 64)) {
                Serial.println("[EMERGENCY] Signed emergency halt received. All pumps OFF.");
                allPumpsOff();
                xQueueReset(cmdQueue);  // Clear any queued commands
                publishAck(packet_id, "all", "EMERGENCY_HALT");
            } else {
                // Unsigned emergency — reject (could be DoS attempt)
                publishSecurityEvent("HMAC_FAIL");
                Serial.println("[SECURITY] Unsigned emergency halt REJECTED.");
            }
            return;
        }

        // ── NORMAL COMMAND VERIFICATION ──
        int verifyResult = verifyPacket(doc);

        if (verifyResult != 0) {
            // Failed verification — do not actuate
            Serial.printf("[SECURITY] Packet rejected (code=%d)\n", verifyResult);
            return;
        }

        // ── QUEUE VERIFIED COMMAND ──
        PumpCommand cmd;
        strncpy(cmd.pump,      doc["pump"]      | "", sizeof(cmd.pump) - 1);
        cmd.duration_ms = doc["duration_ms"] | 0;
        strncpy(cmd.packet_id, doc["packet_id"] | "", sizeof(cmd.packet_id) - 1);
        cmd.emergency = false;

        // Non-blocking send to queue depth=1
        // If queue is full, drop (is_executing is true on Core 1)
        if (xQueueSend(cmdQueue, &cmd, 0) != pdTRUE) {
            Serial.println("[QUEUE] Queue full — command dropped (execution in progress)");
        }
    }
}

// ══════════════════════════════════════════════════════════════
// MQTT: Reconnect (Section D)
// ══════════════════════════════════════════════════════════════
void reconnectMQTT() {
    if (millis() - lastMqttReconnectAttempt < MQTT_RECONNECT_INTERVAL_MS) return;
    lastMqttReconnectAttempt = millis();

    Serial.printf("[MQTT] Attempting reconnect to %s:%d...\n", MQTT_BROKER, MQTT_PORT);
    if (mqttClient.connect(CLIENT_ID, MQTT_USERNAME, MQTT_PASSWORD)) {
        Serial.println("[MQTT] Reconnected.");
        mqttDisconnected = false;

        // Re-subscribe — PubSubClient does NOT auto-resubscribe after reconnect
        mqttClient.subscribe(TOPIC_CMD_PUMP);
        mqttClient.subscribe(TOPIC_CMD_SYNC);

        // Notify Python of reconnect
        StaticJsonDocument<128> doc;
        doc["reset_reason"] = "MQTT_RECONNECT";
        doc["ip"]           = WiFi.localIP().toString();
        doc["mac"]          = WiFi.macAddress();
        doc["ts"]           = getCurrentUnixTime();
        char sig[65];
        char canonical[128];
        snprintf(canonical, sizeof(canonical), "boot|MQTT_RECONNECT|%s|%lld",
                 WiFi.localIP().toString().c_str(), getCurrentUnixTime());
        computeHMAC(canonical, sig);
        doc["sig"] = sig;
        char buf[256];
        serializeJson(doc, buf);
        mqttClient.publish(TOPIC_TELEM_BOOT, buf);

    } else {
        Serial.printf("[MQTT] Reconnect failed (rc=%d). Will retry in %dms.\n",
                      mqttClient.state(), MQTT_RECONNECT_INTERVAL_MS);
    }
}

// ══════════════════════════════════════════════════════════════
// CORE 0 TASK — Security Watchdog
// Runs on CPU 0. Handles MQTT, heartbeat, rate limiting, HMAC.
// NEVER calls delay() — only vTaskDelay() (non-blocking)
// ══════════════════════════════════════════════════════════════
void core0_security_watchdog(void* param) {
    unsigned long lastHeartbeat = 0;

    while (true) {
        // ── MQTT RECONNECT LOOP (Section D) ──
        if (!mqttClient.connected()) {
            if (!mqttDisconnected) {
                // First disconnect detection
                mqttDisconnected = true;
                allPumpsOff();           // Fail safe: pumps off while disconnected
                xQueueReset(cmdQueue);   // Clear stale commands
                Serial.println("[MQTT] Disconnected. All pumps OFF. Queue cleared.");
            }
            reconnectMQTT();
        }

        mqttClient.loop();  // Process incoming MQTT messages

        // ── 500ms HEARTBEAT ──
        unsigned long now = millis();
        if (now - lastHeartbeat >= TELEM_INTERVAL_MS) {
            lastHeartbeat = now;
            int64_t ts = getCurrentUnixTime();

            char canonical[64];
            snprintf(canonical, sizeof(canonical), "heartbeat|%lld", ts);
            char sig[65];
            computeHMAC(canonical, sig);

            StaticJsonDocument<128> hbDoc;
            hbDoc["ts"]  = ts;
            hbDoc["sig"] = sig;
            char hbBuf[128];
            serializeJson(hbDoc, hbBuf);
            mqttClient.publish(TOPIC_TELEM_HB, hbBuf);
        }

        vTaskDelay(10 / portTICK_PERIOD_MS);  // Yield — never delay()
    }
}

// ══════════════════════════════════════════════════════════════
// CORE 1 TASK — Physical Worker
// Runs on CPU 1. Executes verified pump commands, polls sensors.
// ══════════════════════════════════════════════════════════════
void core1_physical_worker(void* param) {
    PumpCommand  cmd;
    unsigned long lastSensorPoll = 0;

    while (true) {
        unsigned long now = millis();

        // ── SENSOR POLLING (500ms) ──
        if (now - lastSensorPoll >= TELEM_INTERVAL_MS) {
            lastSensorPoll = now;
            int64_t ts = getCurrentUnixTime();

            // ── HC-SR04 ──
            float ema_cm  = readUltrasonicEMA();
            const char* state = classifyCupState(ema_cm);

            // Build canonical: "distance|{raw}|{ema}|{state}|{ts}"
            // (raw is same as ema here since raw is pre-filtered)
            char distCanonical[128];
            snprintf(distCanonical, sizeof(distCanonical),
                     "distance|%.2f|%.2f|%s|%lld",
                     ema_cm, ema_cm, state, ts);
            char distSig[65];
            computeHMAC(distCanonical, distSig);

            StaticJsonDocument<256> distDoc;
            distDoc["raw_cm"] = ema_cm;
            distDoc["ema_cm"] = ema_cm;
            distDoc["state"]  = state;
            distDoc["ts"]     = ts;
            distDoc["sig"]    = distSig;
            char distBuf[256];
            serializeJson(distDoc, distBuf);
            mqttClient.publish(TOPIC_TELEM_DIST, distBuf);

            // ── INA219 ──
            float mA = readINA219mA();

            if (mA < 0) {
                // Sensor error — publish SENSOR_ERROR flag
                StaticJsonDocument<128> errDoc;
                errDoc["ma"]    = -1;
                errDoc["error"] = "SENSOR_ERROR";
                errDoc["ts"]    = ts;
                char errBuf[128];
                serializeJson(errDoc, errBuf);
                mqttClient.publish(TOPIC_TELEM_POWER, errBuf);
            } else {
                // Build canonical: "power|{ma}|{ts}"
                char pwrCanonical[64];
                snprintf(pwrCanonical, sizeof(pwrCanonical),
                         "power|%.1f|%lld", mA, ts);
                char pwrSig[65];
                computeHMAC(pwrCanonical, pwrSig);

                StaticJsonDocument<128> pwrDoc;
                pwrDoc["ma"]  = mA;
                pwrDoc["ts"]  = ts;
                pwrDoc["sig"] = pwrSig;
                char pwrBuf[128];
                serializeJson(pwrDoc, pwrBuf);
                mqttClient.publish(TOPIC_TELEM_POWER, pwrBuf);
            }
        }

        // ── PUMP COMMAND EXECUTION ──
        // xQueueReceive blocks until a command arrives (or 10ms timeout)
        if (xQueueReceive(cmdQueue, &cmd, pdMS_TO_TICKS(10)) == pdTRUE) {

            // EXECUTION LOCK — only one pump fires at a time
            if (isExecuting) {
                Serial.println("[WORKER] Already executing — command queued.");
                // Put it back? No — queue depth=1 enforces single in-flight
                continue;
            }

            isExecuting = true;

            // Map pump name to GPIO
            int gpio_pin = -1;
            if (strcmp(cmd.pump, "red")    == 0) gpio_pin = PUMP_RED_PIN;
            else if (strcmp(cmd.pump, "blue")   == 0) gpio_pin = PUMP_BLUE_PIN;
            else if (strcmp(cmd.pump, "yellow") == 0) gpio_pin = PUMP_YELLOW_PIN;

            if (gpio_pin < 0) {
                Serial.printf("[WORKER] Unknown pump: %s\n", cmd.pump);
                isExecuting = false;
                continue;
            }

            Serial.printf("[WORKER] Firing %s for %dms (packet=%s)\n",
                          cmd.pump, cmd.duration_ms, cmd.packet_id);

            // ── GPIO LOW = pump ON (Active LOW relay) ──
            digitalWrite(gpio_pin, LOW);
            vTaskDelay(cmd.duration_ms / portTICK_PERIOD_MS);
            // ── GPIO HIGH = pump OFF ──
            digitalWrite(gpio_pin, HIGH);

            isExecuting = false;

            // Record in idempotency buffer
            addPacketToBuffer(cmd.packet_id);

            // Publish signed ACK
            publishAck(cmd.packet_id, cmd.pump, "EXECUTED");
            Serial.printf("[WORKER] ACK sent for %s\n", cmd.packet_id);
        }

        vTaskDelay(1 / portTICK_PERIOD_MS);  // Yield CPU
    }
}

// ══════════════════════════════════════════════════════════════
// SETUP
// ══════════════════════════════════════════════════════════════
void setup() {
    Serial.begin(115200);
    Serial.println("\n[BOOT] Zero-Trust Micro-Refinery v3.1");

    // ── FAIL-SAFE: All pumps OFF immediately on boot ──
    // SECURITY RATIONALE: Ensure relays are deactivated before any other
    // setup. If setup() crashes after this point, pumps remain off.
    pinMode(PUMP_RED_PIN,    OUTPUT); digitalWrite(PUMP_RED_PIN,    HIGH);
    pinMode(PUMP_BLUE_PIN,   OUTPUT); digitalWrite(PUMP_BLUE_PIN,   HIGH);
    pinMode(PUMP_YELLOW_PIN, OUTPUT); digitalWrite(PUMP_YELLOW_PIN, HIGH);

    // HC-SR04 pins
    pinMode(HC_SR04_TRIG, OUTPUT); digitalWrite(HC_SR04_TRIG, LOW);
    pinMode(HC_SR04_ECHO, INPUT);

    // INA219 I2C
    Wire.begin(INA219_SDA, INA219_SCL);

    // Initialize packet ID buffer
    memset(executedPacketIds, 0, sizeof(executedPacketIds));

    // ── WIFI ──
    Serial.printf("[WIFI] Connecting to %s...\n", WIFI_SSID);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    int wifiAttempts = 0;
    while (WiFi.status() != WL_CONNECTED && wifiAttempts < 20) {
        vTaskDelay(500 / portTICK_PERIOD_MS);
        Serial.print(".");
        wifiAttempts++;
    }

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("\n[WIFI] Failed to connect. System halted.");
        while (true) { vTaskDelay(1000 / portTICK_PERIOD_MS); }
    }
    Serial.printf("\n[WIFI] Connected. IP: %s\n", WiFi.localIP().toString().c_str());

    // ── MQTT ──
    mqttClient.setServer(MQTT_BROKER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);
    mqttClient.setBufferSize(512);  // Ensure enough for signed payloads

    // Initial connect attempt
    if (mqttClient.connect(CLIENT_ID, MQTT_USERNAME, MQTT_PASSWORD)) {
        Serial.println("[MQTT] Connected to broker.");
        mqttClient.subscribe(TOPIC_CMD_PUMP);
        mqttClient.subscribe(TOPIC_CMD_SYNC);
    } else {
        Serial.printf("[MQTT] Connect failed (rc=%d). Will retry in loop.\n",
                      mqttClient.state());
    }

    // ── CLOCK SYNC: Wait up to 10s for Python timestamp ──
    Serial.println("[SYNC] Waiting for clock sync from Python (10s timeout)...");
    unsigned long syncWaitStart = millis();
    while (!clockSynced && (millis() - syncWaitStart) < 10000) {
        mqttClient.loop();
        vTaskDelay(100 / portTICK_PERIOD_MS);
    }
    if (!clockSynced) {
        Serial.println("[SYNC] No sync received. Using millis()-based relative time.");
        Serial.println("[SYNC] WARNING: Replay window is relative — absolute replay attacks possible.");
    }

    // ── PUBLISH BOOT EVENT ──
    esp_reset_reason_t reason = esp_reset_reason();
    const char* resetStr = "UNKNOWN";
    switch (reason) {
        case ESP_RST_POWERON:   resetStr = "POWER_ON";  break;
        case ESP_RST_SW:        resetStr = "SOFTWARE";  break;
        case ESP_RST_BROWNOUT:  resetStr = "BROWNOUT";  break;  // Python must push ERROR on this
        case ESP_RST_WDT:       resetStr = "WATCHDOG";  break;
        case ESP_RST_DEEPSLEEP: resetStr = "DEEPSLEEP"; break;
        default: break;
    }
    Serial.printf("[BOOT] Reset reason: %s\n", resetStr);

    // Sign the boot event
    int64_t ts = getCurrentUnixTime();
    char bootCanonical[128];
    snprintf(bootCanonical, sizeof(bootCanonical),
             "boot|%s|%s|%lld",
             resetStr, WiFi.localIP().toString().c_str(), ts);
    char bootSig[65];
    computeHMAC(bootCanonical, bootSig);

    StaticJsonDocument<256> bootDoc;
    bootDoc["reset_reason"] = resetStr;
    bootDoc["ip"]           = WiFi.localIP().toString();
    bootDoc["mac"]          = WiFi.macAddress();
    bootDoc["ts"]           = ts;
    bootDoc["sig"]          = bootSig;
    char bootBuf[256];
    serializeJson(bootDoc, bootBuf);
    mqttClient.publish(TOPIC_TELEM_BOOT, bootBuf);

    // ── RTOS COMMAND QUEUE ──
    cmdQueue = xQueueCreate(CMD_QUEUE_DEPTH, sizeof(PumpCommand));
    if (cmdQueue == NULL) {
        Serial.println("[ERROR] Failed to create RTOS queue. Halting.");
        while (true) { vTaskDelay(1000 / portTICK_PERIOD_MS); }
    }

    // ── LAUNCH FREERTOS TASKS ──
    // Core 0: Security Watchdog (priority 2 — higher than physical worker)
    xTaskCreatePinnedToCore(
        core0_security_watchdog, "SecWatchdog",
        TASK_STACK_SIZE, NULL, 2, NULL, 0
    );

    // Core 1: Physical Worker (priority 1)
    xTaskCreatePinnedToCore(
        core1_physical_worker, "PhysWorker",
        TASK_STACK_SIZE, NULL, 1, NULL, 1
    );

    Serial.println("[BOOT] FreeRTOS tasks launched. System operational.");
}

// ══════════════════════════════════════════════════════════════
// LOOP — Minimal (tasks handle everything via FreeRTOS)
// ══════════════════════════════════════════════════════════════
void loop() {
    // FreeRTOS tasks own the CPU. loop() just yields.
    vTaskDelay(1000 / portTICK_PERIOD_MS);
}
