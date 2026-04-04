/**
 * Zero-Trust Micro-Refinery — Slave Node Firmware (ESP32)
 *
 * Runs on Core 1 for physical execution while Core 0 handles ESP-NOW.
 * Receives verified commands from Master, fires relay, monitors INA219,
 * and sends telemetry back.
 */

#include <esp_now.h>
#include <WiFi.h>
#include <Wire.h>
#include <Adafruit_INA219.h>

// ══════════════════════════════════════════════════════════════
// OPERATOR SETUP (Change per slave)
// ══════════════════════════════════════════════════════════════
// Set this to "red", "blue", or "yellow" depending on which slave this is
const char* SLAVE_PUMP_ID = "red"; 

// TODO: Replace with the MAC address of the MASTER ESP32
uint8_t masterMAC[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

#define RELAY_PIN 25

// ══════════════════════════════════════════════════════════════
// STATE & COMPONENTS
// ══════════════════════════════════════════════════════════════
Adafruit_INA219 ina219;
QueueHandle_t cmdQueue;
volatile bool isExecuting = false;
esp_now_peer_info_t peerInfo;

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
// ESP-NOW CALLBACKS (Core 0)
// ══════════════════════════════════════════════════════════════
void OnDataRecv(const uint8_t * mac, const uint8_t *incomingData, int len) {
    struct_cmd cmd;
    memcpy(&cmd, incomingData, sizeof(cmd));

    if (cmd.emergency) {
        digitalWrite(RELAY_PIN, HIGH);
        Serial.println("[EMERGENCY] Halt received. Pump OFF.");
        return;
    }

    if (xQueueSend(cmdQueue, &cmd, 0) != pdTRUE) {
        Serial.println("[QUEUE] Full - dropping command");
    }
}

void OnDataSent(const uint8_t *mac_addr, esp_now_send_status_t status) {
    // Optional: add retry logic here if status != ESP_NOW_SEND_SUCCESS
}

// ══════════════════════════════════════════════════════════════
// CORE 1 TASK: Physical Worker
// ══════════════════════════════════════════════════════════════
void core1_worker(void * param) {
    struct_cmd cmd;
    unsigned long lastINA = 0;

    while(1) {
        // Poll INA219 every 500ms
        if (millis() - lastINA > 500) {
            lastINA = millis();
            float v = ina219.getBusVoltage_V();
            float c = ina219.getCurrent_mA();
            
            struct_telem t;
            strcpy(t.pump, SLAVE_PUMP_ID);
            t.voltage = v;
            t.current_mA = c;
            t.is_ack = false;
            
            esp_now_send(masterMAC, (uint8_t *) &t, sizeof(t));
        }

        // Execute Command
        if (xQueueReceive(cmdQueue, &cmd, pdMS_TO_TICKS(10)) == pdTRUE) {
            isExecuting = true;
            Serial.printf("[WORKER] Firing %s for %dms\n", SLAVE_PUMP_ID, cmd.duration_ms);

            digitalWrite(RELAY_PIN, LOW); // ON
            vTaskDelay(cmd.duration_ms / portTICK_PERIOD_MS);
            digitalWrite(RELAY_PIN, HIGH); // OFF

            isExecuting = false;

            // Send ACK via ESP-NOW
            struct_telem ack;
            strcpy(ack.pump, SLAVE_PUMP_ID);
            ack.is_ack = true;
            strcpy(ack.packet_id, cmd.packet_id);
            strcpy(ack.status, "EXECUTED");

            esp_now_send(masterMAC, (uint8_t *) &ack, sizeof(ack));
            Serial.println("[WORKER] ACK Sent to Master");
        }
        vTaskDelay(1 / portTICK_PERIOD_MS);
    }
}

// ══════════════════════════════════════════════════════════════
// SETUP
// ══════════════════════════════════════════════════════════════
void setup() {
    Serial.begin(115200);

    pinMode(RELAY_PIN, OUTPUT);
    digitalWrite(RELAY_PIN, HIGH); // Fail-safe OFF

    if (!ina219.begin()) {
        Serial.println("INA219 Failed");
    }

    // Initialize ESP-NOW
    WiFi.mode(WIFI_STA); // Must be STA for ESP-NOW
    if (esp_now_init() != ESP_OK) {
        Serial.println("ESP-NOW Init Failed");
        return;
    }

    esp_now_register_recv_cb(OnDataRecv);
    esp_now_register_send_cb(OnDataSent);

    // Register Master Peer
    peerInfo.channel = 0;  
    peerInfo.encrypt = false;
    memcpy(peerInfo.peer_addr, masterMAC, 6);
    esp_now_add_peer(&peerInfo);

    cmdQueue = xQueueCreate(1, sizeof(struct_cmd));

    // Pin task strictly to Core 1
    xTaskCreatePinnedToCore(
        core1_worker,
        "WorkerTask",
        4096,
        NULL,
        1,
        NULL,
        1
    );

    Serial.printf("[BOOT] Slave Node %s Ready\n", SLAVE_PUMP_ID);
}

void loop() {
    // Core 0 loop remains empty. Handled by WiFi/ESP-NOW interrupts.
    delay(1000);
}
