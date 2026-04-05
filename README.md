# Zero-Trust Agentic Micro-Refinery v3.1
### Master Architecture & Operational Guide
*System integrity maintained via deterministic safety gates and HMAC-signed telemetry.*

---

## 🚀 Quick Start
1. **Install Dependencies:** `pip install langchain langchain-openai langgraph fastapi "uvicorn[standard]" websockets paho-mqtt aiosqlite python-dotenv colorama`
2. **Configure Environment:** Copy `.env.example` to `.env` and add your `OPENAI_API_KEY` and `MQTT_PASSWORD`.
3. **Start the System:** Run `python main.py` (or `python main.py --mock` for simulation).
4. **Access Dashboard:** Open `http://localhost:8000` in your browser.

---

## 📖 Table of Contents
1. [Executive Summary](#1-executive-summary)
2. [Directory Structure](#2-directory-structure)
3. [Technology Stack & Configuration](#3-technology-stack--configuration)
4. [System Architecture](#4-system-architecture)
5. [Hardware & Embedded Layer](#5-hardware--embedded-layer)
6. [Cybersecurity Architecture](#6-cybersecurity-architecture)
7. [Backend System & State Machine](#7-backend-system--state-machine)
8. [Agentic AI System](#8-agentic-ai-system)
9. [Frontend System (UI/UX)](#9-frontend-system-uiux)
10. [Animation & Motion System](#10-animation--motion-system)
11. [Simulation vs Real Hardware](#11-simulation-vs-real-hardware)
12. [End-to-End Data Flow](#12-end-to-end-data-flow)
13. [Variable & Constraint Flow](#13-variable--constraint-flow)
14. [Failure & Edge Case Handling](#14-failure--edge-case-handling)
15. [Demo Breakpoint Analysis](#15-demo-breakpoint-analysis)
16. [Gaps & Inconsistencies](#16-gaps--inconsistencies)
17. [Final Evaluation](#17-final-evaluation)
18. [Rebuild Blueprint (FULL SETUP)](#18-rebuild-blueprint)

---

## 1. Executive Summary

**System name:** Zero-Trust Agentic Micro-Refinery v3.1

**Core idea:** A hackathon-grade demonstration that a Large Language Model can safely control
physical industrial hardware when wrapped in a deterministic zero-trust security stack. Natural
language commands (`"pour 50ml purple"`) enter through a web dashboard, pass through a LangGraph
ReAct AI agent, are vetted by a Safety Governor, signed with HMAC-SHA256, published over MQTT to
one or more ESP32 nodes, executed by a relay-controlled pump, and confirmed via a signed
acknowledgement — all while the dashboard live-streams every reasoning step and security event.

**High-level overview:**
- **3 physical pumps** (red/blue/yellow) controlled by relay modules on GPIO 25/26/27
- **INA219 current sensor** measures pump draw over I2C (validates power budget)
- **HC-SR04 ultrasonic sensor** detects cup fill state (EMPTY / FULL / NO_CUP)
- **One Python process** on the operator's laptop runs: FastAPI WebSocket server, LangChain AI
  agent, Safety Governor, MQTT client, Dead Man's Switch, and SQLite audit log
- **One or more ESP32 nodes** each running `esp32_direct_node.ino`, connected to the same
  hotspot and MQTT broker, independently verifying every command with HMAC-SHA256 before
  actuating any relay
- **React 19 dashboard** served from the same FastAPI process, connects over WebSocket, renders
  real-time power graphs, reasoning stream, security feed, and pump animations

---

## 2. Directory Structure

```
hail_marry/
│
├── main.py                     # Entry point — boots all subsystems in order
├── websocket_server.py         # FastAPI WebSocket bridge + static file server
├── orchestrator.py             # LangGraph ReAct agent + tool definitions
├── safety_governor.py          # Deterministic safety gate (pre-MQTT)
├── crypto_transport.py         # HMAC-SHA256 sign/verify + nonce management
├── mqtt_client.py              # paho-mqtt wrapper, telemetry routing, reconnect
├── system_state.py             # Shared singleton state + WS broadcast hub
├── dead_mans_switch.py         # Background heartbeat watchdog (3s timeout)
├── audit_db.py                 # aiosqlite audit log + crash recovery
├── attack_demo.py              # Standalone attacker simulation script
│
├── .env                        # Runtime secrets and configuration
├── .env.example                # Template for configuration
│
├── dashboard/                  # React 19 + Vite frontend
│   ├── src/
│   │   ├── main.jsx            # React entry point
│   │   ├── App.jsx             # Root component (thin wrapper)
│   │   └── Dashboard.jsx       # Entire UI (~700 lines, single component)
│   ├── dist/                   # Pre-built static files served by FastAPI
│   └── package.json
│
└── firmware/
    ├── esp32_direct_node.ino   # ACTIVE firmware — direct MQTT topology
    ├── refinery_firmware.ino   # Monolithic single-node (older, not deployed)
    ├── master_node.ino         # ESP-NOW master topology (not deployed)
    └── slave_node.ino          # ESP-NOW slave topology (not deployed)
```

**Module index:**

| File | Module # | Responsibility |
|---|---|---|
| `crypto_transport.py` | 1 | HMAC signing, telemetry verification |
| `orchestrator.py` | 3 | AI reasoning, tool execution |
| `safety_governor.py` | 4 | Physical safety gate |
| `dead_mans_switch.py` | 5 | Heartbeat watchdog |
| `audit_db.py` | 7 | SQLite persistence + crash recovery |
| `websocket_server.py` | 8 | WebSocket API + static file serving |
| `attack_demo.py` | 9 | Red-team demonstration script |

---

## 3. Technology Stack & Configuration

### Backend
| Component | Technology |
|---|---|
| Language | Python 3.10+ |
| Web framework | FastAPI 0.110+ |
| ASGI server | Uvicorn (standard, WebSocket support) |
| MQTT client | paho-mqtt 2.0+ (MQTTv5) |
| AI framework | LangChain 0.2+ / LangGraph 1.x |
| LLM | OpenAI GPT-4o via `langchain-openai` |
| Database | SQLite via aiosqlite (WAL mode) |
| Async | Python asyncio (two loops: main + uvicorn) |
| Environment | python-dotenv |
| Terminal | colorama (cross-platform ANSI) |

### Frontend
| Component | Technology |
|---|---|
| Framework | React 19.2 |
| Build tool | Vite 8.0 |
| Bundling | ESM modules |
| State management | React hooks (useState, useCallback, useEffect, useRef) |
| Transport | Native browser WebSocket API |
| Graphics | HTML5 Canvas (power chart), CSS animations |
| Fonts | Google Fonts: Space Grotesk, Inter, JetBrains Mono |

### Firmware (ESP32)
| Component | Technology |
|---|---|
| Platform | ESP32 (FreeRTOS, dual-core) |
| Framework | Arduino + ESP-IDF |
| MQTT | PubSubClient by Nick O'Leary |
| JSON | ArduinoJson by Benoit Blanchon |
| Cryptography | mbedtls HMAC-SHA256 (built-in ESP32 Arduino) |
| Current sensor | Adafruit INA219 library |
| Multitasking | FreeRTOS xTaskCreatePinnedToCore |

### MQTT Broker
- **Mosquitto 2.1.2** running on the operator laptop
- Port 1883, no TLS (demo environment)
- Password authentication enabled (`pwfile`)

### Environment Variables (`.env`)

| Variable | Value | Used By |
|---|---|---|
| `OPENAI_API_KEY` | `sk-proj-...` | orchestrator.py — LLM API calls |
| `HOTSPOT_SSID` | `laksh's A35` | Reference only (ESP32 hardcoded) |
| `HOTSPOT_PASSWORD` | `lakshjain7` | Reference only (ESP32 hardcoded) |
| `MQTT_BROKER_IP` | `172.20.86.22` | mqtt_client.py |
| `MQTT_BROKER_PORT` | `1883` | mqtt_client.py |
| `MQTT_USERNAME` | `refinery_node` | mqtt_client.py + firmware |
| `MQTT_PASSWORD` | `ChangeMeAtLeast16Chars` | mqtt_client.py (**mismatch — see §16**) |
| `YELLOW_FLOW_RATE_ML_PER_SEC` | `8.8` | safety_governor.py, orchestrator.py |
| `POWER_CONSTRAINT_MA` | `300` | safety_governor.py |
| `MOCK_MODE` | `false` | websocket_server.py |

### Python Dependencies (Core Requirements)
To run the system, install the following packages:
```bash
# Core framework
pip install langchain>=0.2.0 langchain-openai>=0.1.0 langgraph>=0.1.0

# Server & Infrastructure
pip install fastapi>=0.110.0 uvicorn[standard]>=0.29.0 websockets>=12.0 paho-mqtt>=2.0.0

# Utilities
pip install aiosqlite>=0.20.0 python-dotenv>=1.0.0 colorama>=0.4.6
```
> [!NOTE]
> The original `requirements.txt` has been replaced by this comprehensive list. Ensure `langgraph` is installed for orchestrator functionality.

---

## 4. System Architecture

### Component Breakdown

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          OPERATOR LAPTOP                                │
│                                                                         │
│  ┌─────────────────┐    ┌──────────────────────────────────────────┐   │
│  │  React Dashboard │    │              main.py                     │   │
│  │  (port 8000)     │◄──►│                                          │   │
│  │  Dashboard.jsx   │WS  │  ┌────────────┐   ┌─────────────────┐   │   │
│  └─────────────────┘    │  │websocket_  │   │  Orchestrator   │   │   │
│                          │  │server.py   │──►│  (LangGraph     │   │   │
│                          │  │(Uvicorn    │   │   GPT-4o)       │   │   │
│                          │  │ loop)      │   └────────┬────────┘   │   │
│                          │  └─────┬──────┘            │            │   │
│                          │        │broadcast           │tools       │   │
│                          │        │                    ▼            │   │
│                          │  ┌─────┴──────┐   ┌─────────────────┐   │   │
│                          │  │system_state│   │Safety Governor  │   │   │
│                          │  │  shared    │◄──│(6 rules gate)   │   │   │
│                          │  └─────┬──────┘   └────────┬────────┘   │   │
│                          │        │                    │sign+pub    │   │
│                          │  ┌─────┴──────┐   ┌────────┴────────┐   │   │
│                          │  │  audit_db  │   │  mqtt_client    │   │   │
│                          │  │  (SQLite)  │   │  (paho-mqtt)    │   │   │
│                          │  └────────────┘   └────────┬────────┘   │   │
│                          │                            │             │   │
│  ┌─────────────────┐     │  ┌────────────┐           │             │   │
│  │  Mosquitto 2.1.2│◄────┼──│dead_mans_  │           │             │   │
│  │  MQTT Broker    │     │  │switch.py   │           │             │   │
│  │  :1883          │     │  └────────────┘           │             │   │
│  └────────┬────────┘     └───────────────────────────┼─────────────┘   │
│           │                                          │                  │
└───────────┼──────────────────────────────────────────┼──────────────────┘
            │ MQTT over Wi-Fi                          │
            │ (172.20.86.0/? subnet)                   │
            ▼                                          │
┌─────────────────────────────────────────────────────────────────────────┐
│               ESP32 NODE(S) — esp32_direct_node.ino                     │
│                                                                         │
│  ┌──────────────────────────┐  ┌──────────────────────────────────┐    │
│  │  CORE 0 (Security/Comms) │  │  CORE 1 (Physical Worker)        │    │
│  │                          │  │                                  │    │
│  │  - WiFi + MQTT loop      │  │  - INA219 poll every 500ms       │    │
│  │  - Heartbeat publish     │  │  - Power telemetry publish       │    │
│  │  - mqttCallback():       │  │  - Queue receive                 │    │
│  │    Gate 0: JSON parse    │  │  - GPIO RELAY_PIN fire           │    │
│  │    Gate 1: MQTT status   │  │  - vTaskDelay(duration_ms)       │    │
│  │    Gate 2: clock synced  │  │  - publishAck()                  │    │
│  │    Gate 3: time drift    │  │                                  │    │
│  │    Gate 4: nonce replay  │  └──────────────────────────────────┘    │
│  │    Gate 5: idempotency   │                                           │
│  │    Gate 6: HMAC verify   │  Sensors:                                 │
│  │    Gate 7: node filter   │  - INA219 (I2C, SDA=21, SCL=22, 0x40)    │
│  │    → xQueueSend          │  - GPIO 25 = red pump relay               │
│  └──────────────────────────┘  (NODE_PUMP_ID determines which pump)    │
└─────────────────────────────────────────────────────────────────────────┘
```

### Interaction Diagram (Event-Driven)

```
User types "pour 50ml blue"
        │
        ▼
Dashboard.jsx sendCommand()
  wsRef.send({channel:"command", text:"pour 50ml blue"})
        │
        ▼ WebSocket
websocket_server.py websocket_endpoint()
  asyncio.create_task(_dispatch_command(...))
        │
        ▼
_dispatch_command()
  asyncio.run_coroutine_threadsafe(orchestrator.run(...), _main_loop)
        │
        ▼ (main event loop)
orchestrator.run()
  shared.transition(EXECUTING)  ──────────────────► UI: state_change EXECUTING
  shared.push(reasoning_stream) ──────────────────► UI: "Starting plan..."
  run_in_executor(agent.invoke)
        │
        ▼ (thread executor)
LangGraph ReAct agent
  on_chat_model_start callback  ──────────────────► UI: "AI reasoning..."
  LLM thinks → calls get_current_power_draw()
  on_tool_start callback        ──────────────────► UI: "Calling: get_current_power..."
  get_current_power_draw()
    returns shared.last_power (from INA219 telemetry)
  on_tool_end callback          ──────────────────► UI: "→ {ma: 18.2, ...}"
  LLM thinks → calls actuate_pump("blue", 5494)
  actuate_pump() → safety_governor.approve_and_execute()
        │
        ▼ (asyncio on main loop)
SafetyGovernor.approve_and_execute()
  Rule 0: state != BREACH/ERROR ✓
  Rule 0b: execution_blocked == False ✓
  Rule 1: duration_ms < 10000 ✓
  Rule 2: pump_id in {red,blue,yellow} ✓
  Rule 3: telemetry sanity (0 < mA < 600) ✓
  Rule 5: baseline + 200mA <= 300mA ✓
  → crypto.sign_payload("blue", 5494)
  → mqtt_client.publish("refinery/cmd/pump", signed_payload)
  → shared.push(pump_event ON) ──────────────────► UI: pump_event "blue ON"
  → wait shared.ack_event (3s timeout)
        │
        ▼ MQTT
ESP32 node_blue receives refinery/cmd/pump
  Gate 0–7 pass ✓
  xQueueSend(cmdQueue, cmd)
  Core 1: digitalWrite(RELAY_ON)
  Core 1: vTaskDelay(5494ms)
  Core 1: digitalWrite(RELAY_OFF)
  Core 1: publishAck(packet_id, "blue", "EXECUTED")
        │
        ▼ MQTT
mqtt_client._handle_ack()
  crypto.verify_telemetry(ack, "ack") ✓
  shared.ack_event.set()
  shared.push(ack_event) ──────────────────────► UI: ack_event mint glow
        │
        ▼
SafetyGovernor: ACK received
  shared.push(pump_event OFF) ──────────────────► UI: pump_event "blue OFF"
  return {status: "EXECUTED"}
        │
        ▼
orchestrator.run()
  shared.transition(IDLE) ──────────────────────► UI: state_change IDLE
  shared.push(plan_complete) ──────────────────► UI: "✓ Completed."
```

---

## 5. Hardware & Embedded Layer

### GPIO Mappings (`esp32_direct_node.ino`)

| Pin | Function | Notes |
|---|---|---|
| GPIO 25 | Relay control (pump) | Active LOW: LOW=ON, HIGH=OFF |
| GPIO 21 | INA219 SDA | I2C data |
| GPIO 22 | INA219 SCL | I2C clock |

The HC-SR04 (distance sensor) is defined in `refinery_firmware.ino` (Trig=12, Echo=14) but is **not present** in `esp32_direct_node.ino`. Distance telemetry (`refinery/telemetry/distance`) is never published in the deployed firmware.

### Relay Polarity
```cpp
#define RELAY_ON  LOW   // Relay coil energised → contacts CLOSED → pump ON
#define RELAY_OFF HIGH  // Relay coil de-energised → contacts OPEN → pump OFF
```
If the relay module is normally-closed (some cheap optocoupler boards), these defines must be swapped.

### Sensors & Actuators

**INA219 (current/voltage monitor):**
- I2C address: 0x40
- Polls every 500ms on Core 1
- Publishes `refinery/telemetry/power` with `{pump, ma, ts, sig}`
- If `ina219.begin()` fails, telemetry will read 0mA (warning printed, no crash)

**Relay module:**
- Connected to `RELAY_PIN` (GPIO 25 by default)
- Set to `RELAY_OFF` (HIGH) at boot — fail-safe
- Set to `RELAY_OFF` on MQTT disconnect (Core 0 detects within 10ms loop)

**HC-SR04 (ultrasonic distance):**
- Present only in `refinery_firmware.ino` and referenced in `Dashboard.jsx`
- **Not implemented in the deployed `esp32_direct_node.ino`**
- The dashboard shows a distance readout but will never receive live data from this firmware

### FreeRTOS Dual-Core Architecture

```
Core 0 — Security Watchdog (stack: 8192 bytes, priority: 1)
  Loop every 10ms:
    - Check MQTT connection; reconnect if lost; set RELAY_OFF during disconnect
    - Call mqttClient.loop() for message dispatch
    - Every 500ms: publish signed heartbeat to refinery/telemetry/heartbeat
    - On message receipt: run 8-gate security pipeline

Core 1 — Physical Worker (stack: 8192 bytes, priority: 1)
  Loop every 10ms:
    - Every 500ms: read INA219, publish signed power telemetry
    - Non-blocking xQueueReceive (depth=1 queue)
    - On command received: fire relay, delay, release relay, publish signed ACK
```

### Power Considerations

- Each pump draws ~200mA under load
- INA219 measures total bus current (all active devices)
- Python Safety Governor enforces `baseline + 200mA ≤ 300mA` (configurable via `POWER_CONSTRAINT_MA`)
- Two pumps running simultaneously would draw ~400mA → governor enforces sequential execution
- Hard cap: `MAX_DURATION_MS = 10,000ms` per command (prevents runaway dispense)

### Flow Rates (Physical Constants, Section A)

| Pump | GPIO | Flow Rate | Calculated at 50ml |
|---|---|---|---|
| red | 25 | 8.5 ml/sec | 5882ms |
| blue | 26 | 9.1 ml/sec | 5494ms |
| yellow | 27 | 8.8 ml/sec (calibrated) | 5681ms |

Formula: `duration_ms = (volume_ml / flow_rate) * 1000`

---

## 6. Cybersecurity Architecture

### Threat Model

| Threat | Mitigation |
|---|---|
| MQTT broker compromised — attacker publishes commands | HMAC-SHA256 on each command; ESP32 rejects without valid sig |
| Replay attack — valid old message resent | Monotonic nonce; ESP32 rejects nonce ≤ last seen |
| Timing attack — stale command replayed | 2-second time window on ESP32; 5-second window on Python side |
| Forged telemetry (false sensor readings) | All telemetry is HMAC-signed; unsigned telemetry dropped |
| Prompt injection — malicious LLM output | Safety Governor is deterministic; LLM cannot bypass |
| Concurrent pump commands — race condition | asyncio.Lock (Python) + FreeRTOS queue depth=1 (ESP32) |
| ESP32 crashes / goes offline | Dead Man's Switch fires ERROR + signed emergency halt after 3s |
| Process crash with pump stuck ON | Crash recovery: last state checked on startup; ERROR if EXECUTING |
| Intrusion escalation | 3 HMAC_FAIL/REPLAY events → BREACH state, execution locked |

### HMAC-SHA256 Signing

**Shared secret:** `H4ckath0n_TrU5t_K3y_99!` (hardcoded, identical in Python and firmware)

**Canonical string format (commands):**
```
{pump}|{duration_ms}|{ts}|{nonce}|{packet_id}
```

**Canonical string format (telemetry):**
```
heartbeat|{ts}
power|{pump}|{ma:.1f}|{ts}
ack|{packet_id}|{pump}|{status}|{ts}
boot|{reset_reason}|{ip}|{ts}
security|{event}|{ts}
sync|{ts}|{nonce}
emergency: all|0|{ts}|{nonce}|{packet_id}
```

JSON is **never** signed directly — only the canonical string. This avoids serialization
divergence between Python `json.dumps` and Arduino's `ArduinoJson`.

HMAC comparison uses constant-time functions on both sides:
- Python: `hmac.compare_digest()`
- C++: bitwise XOR loop

### ESP32 Security Gate Pipeline (8 Gates)

```
Gate 0: JSON parse valid?
Gate 1: MQTT still connected?
Gate 2: Clock synchronized? (received refinery/cmd/sync at least once)
Gate 3: |now - packet_ts| ≤ 5s? (CMD_TIME_WINDOW_S)
         → 3 consecutive failures → CLOCK_FAULT published
Gate 4: nonce > lastReceivedNonce? (replay prevention)
Gate 5: packet_id not already executed? (idempotency ring buffer, depth 20)
Gate 6: HMAC-SHA256 verified?
Gate 7: pump field matches NODE_PUMP_ID?
Gate 8: FreeRTOS queue not full?
```

### Python Safety Governor (6 Rules)

```
Rule 0:  shared.state not in {BREACH, ERROR}
Rule 0b: shared.execution_blocked == False
Rule 1:  duration_ms < 10,000ms
Rule 2:  pump_id in {red, blue, yellow}
Rule 3:  last_power.ma in [0, 600] mA (sensor sanity)
Rule 5:  baseline_ma + 200 ≤ POWER_CONSTRAINT_MA (300 default)
```

### Nonce and Replay Protection

- Python maintains a session-scoped monotonic nonce counter (thread-safe, `threading.Lock`)
- Each new command gets `nonce = prev_nonce + 1`; never resets within a session
- ESP32 tracks `lastReceivedNonce` in volatile memory; resets on power cycle
- After ESP32 reboot, nonce counter restarts → Python's new session starts at 1 → accepted

### Rate Limiting

- ESP32 publishes `RATE_LIMIT_HIT` security events (mentioned in `mqtt_client.py` handler)
- Python registers it as a security event and increments `intrusion_count`
- No explicit rate limit implementation found in `esp32_direct_node.ino` (referenced but unimplemented)

### BREACH Escalation

- `intrusion_count > 3` → `shared.transition(State.BREACH)`
- BREACH state blocks all execution
- Requires manual process restart to clear (no `reset` command implemented)

### Emergency Halt

- Dead Man's Switch publishes signed `{emergency: true, pump: "all", duration_ms: 0}`
- ESP32 Gate 6 verifies HMAC of `all|0|{ts}|{nonce}|{packet_id}`
- On success: `digitalWrite(RELAY_OFF)`, queue cleared
- Unsigned emergency packets are rejected (prevents DoS via fake halt)

---

## 7. Backend System & State Machine

### System States

| State | Meaning | Entry Trigger | Exit Trigger |
|---|---|---|---|
| `IDLE` | Ready for commands | Boot / plan complete / MQTT reconnect | Command received |
| `EXECUTING` | AI agent + pump active | `orchestrator.run()` starts | Plan complete |
| `PAUSED` | Waiting for cup swap | HC-SR04 FULL_CUP detected | Empty cup detected |
| `ERROR` | Fault — execution blocked | MQTT disconnect, ACK×3, DMS fire, crash recovery, sensor error | Manual reset only |
| `BREACH` | Security violation | intrusion_count > 3 | Manual reset only |

### State Transitions

```python
# Stored in SQLite: state_transitions table
# Every transition broadcasts a state_change WebSocket message

IDLE    → EXECUTING  : orchestrator.run() starts
EXECUTING → IDLE     : plan complete (no fault)
EXECUTING → ERROR    : 3 ACK timeouts, exception in governor
EXECUTING → PAUSED   : alert_human() tool called (cup full detection)
PAUSED  → EXECUTING  : empty cup detected (auto-resume logic)
ANY     → ERROR      : MQTT disconnect, DMS timeout, sensor error, crash recovery
ANY     → BREACH     : intrusion_count > 3
ERROR   → IDLE       : MQTT reconnect (execution_blocked remains True — manual reset needed)
```

### Concurrency Model

The system runs **two asyncio event loops** in two threads:

| Loop | Thread | Owns |
|---|---|---|
| `_main_loop` | Main thread | Orchestrator, Safety Governor, system_state, Dead Man's Switch callbacks |
| `_uvicorn_loop` | `WSServer` daemon thread | FastAPI WebSocket endpoint, broadcast to clients |

Cross-loop communication uses `asyncio.run_coroutine_threadsafe()`. The broadcast bridge
(`thread_safe_broadcast`) schedules `broadcast()` on `_uvicorn_loop` from the main loop.

paho-mqtt runs its own daemon thread (`loop_start()`); callbacks schedule coroutines on
`_main_loop` via `asyncio.run_coroutine_threadsafe()`.

Dead Man's Switch runs in a third daemon thread, also scheduling on `_main_loop`.

### Telemetry Handling

All incoming telemetry is HMAC-verified before any state update:

```
refinery/telemetry/heartbeat → verify → update last_heartbeat_time, push latency_update
refinery/telemetry/power     → verify → update shared.last_power, push telemetry_power
refinery/telemetry/ack       → verify → match packet_id, set ack_event
refinery/telemetry/boot      → verify (best effort) → check BROWNOUT
refinery/telemetry/security  → route TIME_DRIFT / CLOCK_FAULT / RATE_LIMIT_HIT / HMAC_FAIL
```

### ACK Retry Logic

```
For strike in [1, 2, 3]:
    sign fresh payload (new nonce, new ts, new packet_id)
    log_command(APPROVED)
    publish to refinery/cmd/pump
    wait ack_event (3s timeout)
    if ACK received → return EXECUTED
    else → increment consecutive_ack_failures

All 3 failed → transition(ERROR, "ACK_FAILURE_x3")
```

### SQLite Schema (5 Tables)

```sql
commands           -- Every actuate_pump() call (approved or rejected)
security_events    -- HMAC_FAIL, REPLAY, BREACH_ESCALATION, RATE_LIMIT_HIT
state_transitions  -- Every IDLE↔EXECUTING↔ERROR↔BREACH transition
ai_reasoning       -- Every LangGraph step, grouped by plan_id (UUID4)
connectivity_events-- MQTT_DISCONNECT, MQTT_RECONNECT, DRIFT_ESCALATION, CLOCK_FAULT
```

WAL mode + PRAGMA synchronous=NORMAL ensures safe concurrent reads during telemetry writes.

Crash recovery: on startup, reads last `state_transitions` row. If state was `EXECUTING` or
`PAUSED`, immediately sets `shared._state = ERROR` — pump may be physically stuck ON.

---

## 8. Agentic AI System

### System Prompt

Injected into `create_react_agent()` as `prompt=`. The LLM receives:
- Physical constants (flow rates, pump names)
- Volume calculation formula: `duration_ms = (volume_ml / flow_rate) * 1000`
- Color mixing map: Purple=Red+Blue, Green=Blue+Yellow, Orange=Red+Yellow, Brown=all
- Power constraints: ~200mA per pump, never parallel if total > 300mA
- Mandatory pre-execution checklist: always call `get_current_power_draw()` first

### Tools Available to LLM

```python
get_current_power_draw() -> str
    # Reads shared.last_power
    # Raises ToolException if no telemetry or reading > 2s old
    # Returns JSON: {ma, timestamp, age_ms}

actuate_pump(pump_id: str, duration_ms: int) -> str
    # Validates pump_id in {red, blue, yellow}
    # Calls SafetyGovernor.approve_and_execute()
    # Raises ToolException on REJECTED or exception
    # Returns JSON: {status, packet_id, actual_duration_ms, reason}
```

**Note:** `check_receptacle_state()` and `alert_human()` are documented in the module docstring
but are not implemented in `_build_tools()`.

### Decision Flow

```
LangGraph ReAct loop:
  1. LLM receives system prompt + user command
  2. LLM reasons (Thought)
  3. LLM calls get_current_power_draw() → validates power baseline
  4. LLM calculates duration_ms for each pump
  5. LLM calls actuate_pump() sequentially (power constraint enforces sequential)
  6. On ToolException → LLM may retry or report failure
  7. LLM outputs final message
  8. orchestrator.run() transitions back to IDLE
```

### Reasoning Stream

`ReasoningStreamHandler(BaseCallbackHandler)` intercepts every LangGraph event:
- `on_chat_model_start` → push `"AI reasoning..."`
- `on_llm_end` → push Thought text (first line starting with "Thought:")
- `on_tool_start` → push `"Calling: {tool_name}({args})"`
- `on_tool_end` → push `"→ {result[:200]}"`
- `on_tool_error` / `on_llm_error` → push error text

All pushes use `asyncio.run_coroutine_threadsafe(shared.push(payload), loop)` since callbacks
fire in the thread executor.

### Guardrails & Validation Layers

```
Layer 1: System prompt — instructs LLM on constraints
Layer 2: ToolException — pump validation in actuate_pump() tool
Layer 3: Safety Governor — 6 deterministic physical rules (LLM cannot bypass)
Layer 4: CryptoTransport — HMAC signs every command (attacker cannot forge)
Layer 5: ESP32 8-gate pipeline — firmware verifies independently of Python
```

---

## 9. Frontend System (UI/UX)

### Component Architecture

The entire UI is a **single React component** (`Dashboard.jsx`, ~700 lines). No routing, no
context providers, no state management library.

```
Dashboard (default export)
├── OdometerDigit      — animated rolling digit display
├── PulseDot           — animated status indicator
├── GlassPanel         — frosted glass card with focus/dim states
└── [inline JSX]       — all panels, status bar, animations
```

### State Variables (React useState)

| Variable | Type | Purpose |
|---|---|---|
| `systemState` | string | IDLE/EXECUTING/PAUSED/BREACH/ERROR |
| `mA` | number | Current power draw |
| `peakMA` | number | Session peak current |
| `distance` | number | HC-SR04 EMA in cm (mock only) |
| `packetsVerified` | number | MQTT log counter |
| `packetsDropped` | number | MQTT log counter |
| `uptime` | number | Seconds since load |
| `latency` | number | WS round-trip estimate |
| `focusPanel` | string | "left"/"center"/"right"/null |
| `powerHistory` | number[60] | Sliding window for canvas chart |
| `mqttLog` | array | Right panel log entries (max 25) |
| `aiLog` | array | Left panel reasoning stream (max 15) |
| `activePumps` | {red,blue,yellow} | Boolean per pump (controls animation) |
| `cupFill` | number | Fill % for cup SVG animation |
| `cupColor` | string | Hex color for cup (blend logic) |
| `planId` | string | 8-char UUID prefix for active plan |
| `mockMode` | boolean | Shows simulation badge |
| `ackGlow` | {red,blue,yellow} | 300ms mint flash on ACK |
| `driftWarning` | string | "escalated"/"clock_fault"/null |
| `governorCard` | object | REJECTED reason toast (8s auto-dismiss) |
| `brokerConnected` | boolean | Broker status badge |

### WebSocket Integration

Connection at: `ws://{window.location.host}/ws`

- Auto-reconnects every 2 seconds on disconnect
- On open: receives `state_change` with current state
- Incoming channel routing in `ws.onmessage`:

| Channel | Action |
|---|---|
| `state_change` | setSystemState, triggerSweep, setFocusPanel |
| `reasoning_stream` | setPlanId, addAi (left panel) |
| `pump_event` | setActivePumps, addLog |
| `ack_event` | triggerAckGlow, addLog |
| `governor_event` | addLog, setGovernorCard (if REJECTED) |
| `telemetry_power` | setMA, setPeakMA, setPowerHistory |
| `telemetry_distance` | setDistance, setSensorError |
| `security_event` | addLog (HMAC_FAIL red / REPLAY amber) |
| `connectivity_event` | setBrokerConnected, setDriftWarning, addLog |
| `system_metrics` | setPacketsVerified, setPacketsDropped, setUptime |
| `mock_mode` | setMockMode(true) |
| `latency_update` | setLatency |

### Demo Buttons (Hardcoded Sequences)

The UI contains three hardcoded demo sequences that animate independently of the Python backend:

| Button | Test Case | Description |
|---|---|---|
| Test A | Mix 50ml Purple / ≤300mA | Sequential Red + Blue, power constraint demo |
| Test B | 2× Green / Cup Swap | Pause/resume on cup detection |
| Test C | Attack Demo | BREACH state + HMAC_FAIL visual |

These set local state directly, bypassing WebSocket. They serve as a demo fallback if the
backend is unavailable.

---

## 10. Animation & Motion System

### CSS Keyframes

```css
pulse         — opacity 1↔0.4 (2s, status dots, state badge at IDLE)
pulseAmber    — opacity + amber glow (mock/broker-lost badges)
scan          — vertical sweep line on panels
flow          — horizontal particle drift on pump pipes
heartbeat     — box-shadow pulse on state badge (IDLE only)
glitch        — micro-jitter on BREACH (x/y translate)
sweepLTR      — full-screen gradient sweep left→right (EXECUTING, RESTORE)
sweepRTL      — full-screen gradient sweep right→left (IDLE, PAUSED)
sonar         — radial ring expand (pump active indicator)
fadeSlideUp   — entry animation for log rows
breathe       — ambient background opacity (8s cycle)
attackPulse   — attack button flash
mintFlash     — broker-restored green pill fade
```

### Event → Animation Mapping

| Backend Event | Animation |
|---|---|
| `state_change: EXECUTING` | sweepLTR (cyan), focusPanel="left" |
| `state_change: IDLE` | sweepRTL (green), focusPanel=null |
| `state_change: BREACH` | sweepRTL (red), red vignette flash × 3 |
| `state_change: ERROR` | persistent orange vignette pulse |
| `pump_event: ON` | activePumps[pump]=true → pipe flow animation, cup fill, color blend |
| `pump_event: OFF` | activePumps[pump]=false |
| `ack_event` | ackGlow[pump]=true for 300ms (mint border flash on relay icon) |
| `reasoning_stream` | addAi() → left panel entry fadeSlideUp, active→dim after 3s |
| `governor_event: REJECTED` | governorCard shown for 8s |
| `connectivity_event: MQTT_DISCONNECT` | brokerFlash="lost", amber pill |
| `connectivity_event: DRIFT_ESCALATION` | driftWarning="escalated", yellow pill |

### Cup Color Blend Logic

```javascript
if (red && blue && yellow) → "#7C4B2A" // Brown
else if (red && blue)      → "#8B5CF6" // Purple
else if (blue && yellow)   → "#00FF9D" // Green
else if (red && yellow)    → "#FF6B35" // Orange
else if (red)              → "#FF2A2A"
else if (blue)             → "#3B82F6"
else if (yellow)           → "#FFD166"
// Preserves last color when all pumps off
```

### Power Chart (Canvas 2D)

- 60-point sliding window, rendered on every `powerHistory` state change
- Red threshold line at 300mA
- Line color: green (≤250mA), amber (250-300mA), red (>300mA)
- Gradient fill under the line with matching color + alpha
- Retina-quality: canvas scaled ×2, CSS dimensions halved

---

## 11. Simulation vs Real Hardware

### Mock Mode

Activated with `python main.py --mock` (sets `MOCK_MODE=true`).

In mock mode:
- MQTT broker connection is **skipped**
- `shared.mqtt_connected` is set to `True` directly
- A synthetic telemetry loop runs in the uvicorn event loop:
  - Power: sine wave 150–280mA, spike to 312mA every 30s
  - Distance: EMPTY(11.5cm) → FULL(3.2cm) → NO_CUP(19.8cm) on 20s cycle
  - Heartbeat: synthetic, seeds `shared.last_heartbeat_time`
  - HMAC_FAIL: every 45s (fake security event for demo)
  - MQTT_DISCONNECT/RECONNECT: every 60s (with 3s reconnect simulation)
  - DRIFT_ESCALATION + CLOCK_FAULT: once at t≈90s
- The LLM still calls the **real OpenAI API**
- `actuate_pump()` calls the Safety Governor but since no real MQTT publish occurs, no ACK will arrive → governor times out after 3 strikes → ERROR state

**Mock mode limitation:** The system enters ERROR after the first command because the mock
telemetry loop does not publish to MQTT and the governor ACK mechanism expects a real ESP32
response. The `actuate_pump` tool needs a mock ACK injection to function end-to-end.

### Real Hardware Expectations

- ESP32 connects to `MQTT_BROKER` = `172.20.86.22:1883`
- ESP32 authenticates with `MQTT_USERNAME=refinery_node`, `MQTT_PASSWORD=lakshlaabh1`
- Python must connect to the same broker
- Clock sync (`refinery/cmd/sync`) is sent by Python on MQTT connect
- ESP32 gates all commands until `clockSynced = true`
- Commands older than 5 seconds are rejected (time window)
- If `ina219.begin()` fails (sensor not found), power telemetry reads 0mA
  - Governor's `last_power` will eventually stale out (>2s threshold)
  - `get_current_power_draw()` tool raises ToolException → LLM may abort

---

## 12. End-to-End Data Flow

### Full Command Pipeline

```
Step 1: USER INPUT
  User types "pour 50ml blue" in Dashboard.jsx input field
  sendCommand() checks wsRef.current.readyState === OPEN
  wsRef.send({channel: "command", text: "pour 50ml blue"})

Step 2: WEBSOCKET RECEIVE
  websocket_server.py receives raw text
  json.loads() → msg.channel == "command"
  asyncio.create_task(_dispatch_command(cmd_text, websocket))

Step 3: GUARD CHECKS
  _dispatch_command() checks:
    - _orchestrator is not None
    - shared.state not in {ERROR, BREACH}
  run_coroutine_threadsafe(orchestrator.run("pour 50ml blue"), _main_loop)

Step 4: ORCHESTRATOR SETUP
  orchestrator.run() on main loop:
    - Generates plan_id (UUID4), e.g. "a3f9c12d"
    - shared.transition(EXECUTING) → WebSocket broadcast state_change
    - shared.push(reasoning_stream, "Starting plan a3f9c12d: pour 50ml blue")
    - Creates ReasoningStreamHandler for this plan
    - run_in_executor(agent.invoke(..., callbacks=[handler]))

Step 5: AI REASONING (thread executor)
  LangGraph ReAct:
    - handler.on_chat_model_start → push "AI reasoning..."
    - LLM: "blue pump, 9.1 ml/sec. 50ml / 9.1 = 5494ms. Check power first."
    - LLM calls get_current_power_draw()
    - handler.on_tool_start → push "Calling: get_current_power_draw()"
    - Tool reads shared.last_power → returns {ma: 18.2, age_ms: 340}
    - handler.on_tool_end → push "→ {ma: 18.2, ...}"
    - LLM: "baseline 18mA + 200mA = 218mA < 300mA. Safe."
    - LLM calls actuate_pump("blue", 5494)
    - handler.on_tool_start → push "Calling: actuate_pump(blue, 5494ms)"

Step 6: SAFETY GOVERNOR (main loop, via awaited tool)
  approve_and_execute("blue", 5494):
    Rule 0: IDLE ✓
    Rule 0b: not blocked ✓
    Rule 1: 5494 < 10000 ✓
    Rule 2: "blue" ∈ {red,blue,yellow} ✓
    Rule 3: 18.2mA in [0, 600] ✓
    Rule 5: 18.2 + 200 = 218.2 ≤ 300 ✓
    → log_command(APPROVED)
    → shared.push_governor_event(APPROVED)
    → shared.push(pump_event ON, "blue")

Step 7: SIGNING
  crypto.sign_payload("blue", 5494):
    nonce = _nonce++ (thread-safe)
    ts = int(time.time())
    packet_id = UUID4()
    canonical = "blue|5494|1712345678|42|<uuid>"
    sig = HMAC-SHA256(SECRET_KEY, canonical)
    returns {pump, duration_ms, ts, nonce, packet_id, sig}

Step 8: MQTT PUBLISH
  mqtt_client.publish("refinery/cmd/pump", payload)
  paho publishes to Mosquitto broker
  Mosquitto routes to all subscribers of refinery/cmd/pump

Step 9: ESP32 RECEIVE
  Core 0 mqttClient.loop() receives message on topic refinery/cmd/pump
  mqttCallback() runs:
    Gate 0: JSON parse ✓
    Gate 1: MQTT connected ✓
    Gate 2: clockSynced ✓ (sync was sent on Python connect)
    Gate 3: |getCurrentUnixTime() - 1712345678| ≤ 5s ✓
    Gate 4: nonce 42 > lastReceivedNonce 41 ✓
    Gate 5: packet_id not in ring buffer ✓
    Gate 6: HMAC verified ✓
    Gate 7: "blue" == NODE_PUMP_ID "blue" ✓
    Gate 8: xQueueSend(cmdQueue, cmd) ✓

Step 10: PHYSICAL ACTUATION
  Core 1 xQueueReceive(cmdQueue) gets cmd
  digitalWrite(RELAY_ON)  — pump ON
  vTaskDelay(5494)         — 5.494 seconds
  digitalWrite(RELAY_OFF) — pump OFF
  addPacketToBuffer(packet_id)
  publishAck(packet_id, "blue", "EXECUTED")

Step 11: ACK RETURN
  Mosquitto routes refinery/telemetry/ack to Python subscriber
  mqtt_client._on_message → json.loads → _schedule(_route_message)
  _handle_ack():
    crypto.verify_telemetry(ack, "ack") → HMAC verified ✓
    packet_id matches pending_ack_packet_id ✓
    shared.ack_event.set()
    shared.push(ack_event, "blue", "EXECUTED")

Step 12: GOVERNOR COMPLETION
  asyncio.wait_for(shared.ack_event.wait(), 3.0) returns
  shared.push(pump_event OFF, "blue")
  return {status: "EXECUTED", ...}

Step 13: LLM COMPLETION
  actuate_pump returns JSON result
  handler.on_tool_end → push "→ {status: EXECUTED, ...}"
  LLM: "Task complete. 50ml blue dispensed."
  agent.invoke() returns

Step 14: PLAN CLOSE
  orchestrator.run():
    shared.transition(IDLE)
    shared.push(reasoning_stream, "✓ 50ml blue dispensed.")
    return "Task complete."
```

---

## 13. Variable & Constraint Flow

### Where Key Variables Originate

| Variable | Origin | Propagates To |
|---|---|---|
| `POWER_CONSTRAINT_MA` | `.env` → `safety_governor.py` | Governor Rule 5 rejection |
| `YELLOW_FLOW_RATE_ML_PER_SEC` | `.env` → `orchestrator.py` + `safety_governor.py` | LLM system prompt, FLOW_RATES dict |
| `SECRET_KEY` | hardcoded in `crypto_transport.py` + firmware | Every sign/verify operation |
| `shared.last_power` | INA219 via ESP32 → MQTT → `mqtt_client._handle_power()` | Governor Rule 3/5, `get_current_power_draw()` tool |
| `shared.last_heartbeat_time` | ESP32 heartbeat → MQTT → `_handle_heartbeat()` | Dead Man's Switch |
| `shared.state` | `shared.transition()` | All guards, WebSocket broadcast |
| `shared.mqtt_connected` | `mqtt_client._on_connect/disconnect` | DMS skip condition |
| `crypto.intrusion_count` | `verify_telemetry()` HMAC failures | BREACH escalation |
| `clock_synced` (ESP32) | `refinery/cmd/sync` receipt | Gate 2 of security pipeline |

### How Constraints Propagate

```
.env POWER_CONSTRAINT_MA=300
  → safety_governor.POWER_CONSTRAINT_MA
    → approve_and_execute() Rule 5: baseline + 200 ≤ 300
      → ToolException → LLM rewrites plan (sequential)

.env YELLOW_FLOW_RATE_ML_PER_SEC=8.8
  → orchestrator.SYSTEM_PROMPT (injected)
    → LLM calculates correct duration_ms
  → safety_governor.FLOW_RATES["yellow"]
    → Rule 2 validation (pump ID check)

INA219 reading
  → mqtt_client._handle_power() → shared.last_power
    → governor._get_baseline_ma() → Rule 5
    → get_current_power_draw() tool → LLM context
    → governor._telemetry_sanity_check() → Rule 3
```

---

## 14. Failure & Edge Case Handling

### Sensor Failures

| Failure | Detection | Response |
|---|---|---|
| INA219 not found at boot | `ina219.begin()` returns false | Warning to Serial; power reads 0mA; system continues |
| INA219 returns negative mA | Core 1 clamps to 0 | Non-critical; 0mA passes governor |
| Power reading > 600mA | `mqtt_client._handle_power()` | `consecutive_sensor_errors++`; at 3 → ERROR state |
| Power reading stale (>2s) | `get_current_power_draw()` tool | ToolException → LLM aborts or retries |
| HC-SR04 absent | Never implemented in deployed firmware | `telemetry_distance` never arrives; dashboard shows static 12cm |

### Network Failures

| Failure | Detection | Response |
|---|---|---|
| MQTT broker disconnect | `paho on_disconnect` callback | Immediate ERROR state + execution_blocked = True; exponential backoff reconnect (0.5s, 1s, 2s) |
| All 3 reconnect attempts fail | After 3.5s of retries | MQTT_UNREACHABLE logged; ERROR state persists; manual restart required |
| ESP32 heartbeat timeout | Dead Man's Switch (3s window) | ERROR state + signed emergency halt published |
| ESP32 MQTT disconnect | Core 0 detects `!mqttClient.connected()` | RELAY_OFF immediately; reconnect loop every 5s |
| ACK not received | Governor 3s wait_for timeout | Retry with fresh signature up to 3 times; then ERROR |

### Auth Failures

| Failure | Detection | Response |
|---|---|---|
| Wrong MQTT password | `paho on_connect rc != 0` | Print error; mqtt_connected stays False; system continues without hardware |
| HMAC_FAIL on telemetry | `crypto.verify_telemetry()` | `intrusion_count++`; BREACH at > 3 |
| HMAC_FAIL on command (ESP32) | Gate 6 | `publishSecurityEvent("HMAC_FAIL")`; packet dropped; pump does not fire |
| Replay attack (nonce) | Gate 4 | `publishSecurityEvent("REPLAY")`; packet dropped |
| Time drift | Gate 3 | `publishSecurityEvent("TIME_DRIFT")` or "CLOCK_FAULT" at 3 consecutive |

### Constraint Violations

| Violation | Detection | Response |
|---|---|---|
| duration_ms ≥ 10,000ms | Governor Rule 1 | REJECTED → ToolException → LLM rewrites plan |
| Power over budget | Governor Rule 5 | REJECTED → LLM must use sequential execution |
| Unknown pump_id | Governor Rule 2 | REJECTED → LLM retries with valid ID |
| System in ERROR/BREACH | Governor Rule 0 | REJECTED → UI shows governor card; manual reset required |

### AI Failures

| Failure | Detection | Response |
|---|---|---|
| LLM API key invalid | `ChatOpenAI.__init__` or first invoke | Exception caught; orch = None; commands ignored |
| LLM hallucinates pump_id | `actuate_pump` tool validator | ToolException("Invalid pump_id") → LLM gets feedback |
| LLM exceeds duration cap | Governor Rule 1 | ToolException("Duration ≥ 10000ms") → LLM recalculates |
| LangGraph exception | `except Exception` in `run()` | output = "Agent error: ..." ; state transitions back to IDLE |
| LLM ignores power constraint | Governor Rule 5 | REJECTED; LLM must retry sequentially |

---

## 15. Demo Breakpoint Analysis

### What Will Fail During Demo

| Scenario | Failure Mode | Visibility |
|---|---|---|
| Backend not started | UI loads but WS shows "not connected"; commands silently dropped | Visible |
| OpenAI key invalid / quota | "Orchestrator failed to initialize" in terminal; commands do nothing | Terminal only; UI blank |
| ESP32 not connected to broker | No heartbeat → DMS fires after 3s (+ 15s warmup) → ERROR | Visible after 18s |
| HC-SR04 sensor missing | Distance always shows 12cm; Test B cup-swap demo won't auto-trigger in real mode | Subtle |
| First command after boot | Clock sync must arrive before Gate 2 passes. If Python sends sync but no ACK comes (e.g., ESP32 not yet subscribed), commands fail Gate 2 | Visible if ESP32 boots slow |
| Power telemetry absent | `get_current_power_draw()` raises ToolException → LLM cannot proceed | Left panel shows tool error |
| MQTT password mismatch | Python fails to connect; broker shows auth error; ESP32 connects, Python doesn't | Terminal shows connect rc≠0 |

### Dead UI States

1. **Reasoning panel blank:** orchestrator not injected (`_orchestrator is None`), or
   `shared.push()` missing (now fixed), or `import sys` missing in orchestrator.py (unfixed).
2. **State badge stuck at IDLE during execution:** `shared.transition()` not called because
   `orchestrator.run()` crashed before reaching that line (e.g., missing `sys` import).
3. **Pump animation never triggers:** `pump_event` pushed via `shared.push()` which required the
   now-fixed `push()` method.
4. **Power graph flat-lines:** `telemetry_power` WebSocket messages not reaching UI; either
   `shared.push()` was missing (fixed) or INA219 not responding.
5. **ACK glow never fires:** ACK HMAC verification fails (clock drift, or wrong canonical
   string format mismatch between Python and firmware).

### Missing Feedback Loops

- No confirmation that the signed command was received by the broker (publish QoS=1 but no `on_publish` callback checked)
- No UI indicator when orchestrator is initializing (boot gap can be 5-15s)
- No "reset" button in UI for ERROR/BREACH state — user must restart Python process
- No UI indicator if OpenAI API call is in-flight vs. actually reasoning

---

## 16. Gaps & Inconsistencies

### Critical Bugs (Found During This Session)

| # | File | Bug | Status |
|---|---|---|---|
| 1 | `websocket_server.py` | Missing `import sys` — crashes WS handler on any message | **FIXED** |
| 2 | `websocket_server.py` | Missing `from typing import Optional` — crashes at import time | **FIXED** |
| 3 | `system_state.py` | Missing `push()` method — all channel broadcasts silent | **FIXED** |
| 4 | `system_state.py` | Double broadcast in `transition()` — state_change sent twice | **FIXED** |
| 5 | `orchestrator.py` | Missing `import sys` — `run()` crashes on first line | **UNFIXED** |

**Bug 5 detail:** `orchestrator.py` uses `sys.stdout.write(...)` on lines 244-251 but has no
`import sys`. When `orchestrator.run()` is scheduled on the main loop, it immediately raises
`NameError: name 'sys' is not defined`, silently (the future's exception is not awaited).
The reasoning stream, state transitions, and pump actuation never execute.
**Fix:** Add `import sys` to `orchestrator.py` imports.

### MQTT Password Mismatch

- `.env` line 14: `MQTT_PASSWORD=ChangeMeAtLeast16Chars`
- `esp32_direct_node.ino` line 25: `const char *MQTT_PASSWORD = "lakshlaabh1"`

The broker's `pwfile` must contain the correct password. Since the ESP32 connects successfully,
the broker accepts `"lakshlaabh1"`. Python uses `"ChangeMeAtLeast16Chars"` → likely fails to
authenticate. Fix: set `.env` `MQTT_PASSWORD=lakshlaabh1`.

### Architecture Issues

- **`actuate_pump` tool** in orchestrator.py is defined as a sync `@tool` but uses `await`
  internally. In newer LangChain, this requires `async def` decorated with `@tool` — or the
  code will have a syntax error. Verify the actual file has `async def actuate_pump`.
- **`alert_human()` and `check_receptacle_state()` tools** are documented in the module docstring
  and the system prompt references cup state, but neither tool is implemented in `_build_tools()`.
  Test Case B (cup swap) only works in demo mode (hardcoded UI sequence), not in real mode.
- **`langgraph` not in requirements.txt** — must be installed manually.
- **`aiosqlite` opens a new connection per write** — for high-frequency telemetry (2Hz × N nodes)
  this creates many short-lived connections. Not a problem at demo scale.
- **`power_by_node` attribute** set in `_handle_power()` with `hasattr` guard — this is
  monkey-patched onto `SharedState` rather than declared in `__init__`. Works but is fragile.

### Security Gaps (Demo-Acceptable, Not Production-Ready)

- **SECRET_KEY hardcoded** in both Python and firmware. In production: ATECC608A HSM.
- **No TLS on MQTT** — all traffic including HMAC signatures is plaintext. On a captured hotspot,
  an attacker can observe signatures (though not forge them, as HMAC prevents that).
- **No TLS on WebSocket** — dashboard traffic is unencrypted.
- **`attack_demo.py` uses `attack_demo` MQTT user** which requires a separate `pwfile` entry —
  this user must exist in Mosquitto for the demo to run.
- **No dashboard authentication** — anyone on the network can access `http://172.20.86.22:8000`
  and send commands.

### UI/Backend Mismatch

- Dashboard has a `distance` state variable and sensor error indicator, but `esp32_direct_node.ino`
  never publishes `refinery/telemetry/distance`. Distance display always shows the initial value
  (12cm) in real hardware mode.
- `latency_update` channel expects `msg.latency_ms` but `mqtt_client._handle_heartbeat()` pushes
  `msg.ms` — the dashboard reads `msg.latency_ms ?? 0` with nullish coalescing, so latency will
  always display 0 from real hardware. The field name is `ms` not `latency_ms`.
- `system_metrics` uptime is computed as `int(time.time())` (Unix epoch) in mock mode, but the
  dashboard treats it as seconds-since-start. In real mode, `system_metrics` is never pushed (only
  in the mock telemetry loop), so uptime relies on the client-side `setInterval` counter.

---

## 17. Final Evaluation

### Strengths

1. **Defense-in-depth at every layer** — LLM prompt, Python Governor, HMAC signing, 8-gate ESP32
   pipeline, ACK confirmation, Dead Man's Switch. Passing one layer is not sufficient to actuate
   hardware.
2. **Zero-trust even for safety commands** — emergency halt is HMAC-signed, preventing DoS via
   unsigned halt injection.
3. **Deterministic safety layer** separates AI reasoning from physical execution. The LLM cannot
   override the Governor regardless of prompt engineering.
4. **Comprehensive audit trail** — every command (approved or rejected), security event, state
   transition, and AI reasoning step is persisted to SQLite with timestamps.
5. **FreeRTOS dual-core isolation** — security verification on Core 0 never blocks the physical
   worker on Core 1. Relay actuation is deterministic.
6. **Crash recovery** — on process restart, last known state is read from SQLite; any crash during
   EXECUTING triggers ERROR rather than blind resume.
7. **Professional React dashboard** — single-component but well-structured; animations are
   event-driven, not polling-based.

### Weaknesses

1. **`import sys` missing from `orchestrator.py`** — the most critical unfixed bug. The system
   appears to work (WS connected, command dispatched) but no AI reasoning or pump actuation occurs.
2. **`check_receptacle_state` and `alert_human` unimplemented** — the system prompt tells the LLM
   it can check cup state, but the tool doesn't exist. The LLM will fail or hallucinate.
3. **Mock mode doesn't simulate ACK** — every command in mock mode will time out after 3 ACK
   strikes and enter ERROR state, making the mock mode nearly non-functional for command demos.
4. **Password mismatch** prevents Python from connecting to MQTT broker in live mode.
5. **No reset mechanism** in the UI — ERROR/BREACH require process restart.
6. **HC-SR04 absent from deployed firmware** — a significant documented feature that is not
   available in real hardware mode.
7. **Single monolithic component** (Dashboard.jsx) — difficult to maintain or extend beyond the
   current feature set.

### System Maturity

| Dimension | Rating | Notes |
|---|---|---|
| Security architecture | ★★★★☆ | Solid for a hackathon; hardcoded key is the main gap |
| Hardware reliability | ★★★☆☆ | Relay + INA219 work; HC-SR04 missing in live firmware |
| AI integration | ★★★☆☆ | Architecture sound; missing import prevents execution |
| UI polish | ★★★★★ | Professional animations, real-time stream, well-designed |
| Code quality | ★★★☆☆ | Several missing imports, unimplemented tools, password mismatch |
| Production readiness | ★★☆☆☆ | Demo-grade; no TLS, no auth, hardcoded secrets |

### Must Fix Urgently (Before Demo)

1. Add `import sys` to `orchestrator.py`
2. Set `MQTT_PASSWORD=lakshlaabh1` in `.env`
3. Add `langgraph` to `requirements.txt`
4. Decide whether to stub `check_receptacle_state` and `alert_human` or remove from system prompt

---

## 18. Rebuild Blueprint

### Prerequisites

```
Hardware:
  - 1-3 ESP32 development boards
  - 1 relay module per board (active LOW preferred)
  - 1 INA219 current sensor per board (I2C, 0x40)
  - Pumps wired to relay NO contacts
  - Hotspot phone or Wi-Fi router

Software:
  - Python 3.10+
  - Arduino IDE 2.x with ESP32 board support
  - Node.js 18+ (for dashboard build)
  - Mosquitto 2.x MQTT broker
```

### Step 1 — Clone and Install

```bash
# Python dependencies
pip install langchain langchain-openai langgraph paho-mqtt fastapi "uvicorn[standard]" \
            websockets aiosqlite python-dotenv colorama

# Frontend build (only needed if dist/ is not pre-built)
cd dashboard
npm install
npm run build
cd ..
```

### Step 2 — Configure Environment

```bash
# Copy and fill .env
cp .env.example .env
```

Edit `.env`:
```
OPENAI_API_KEY=sk-...            # Your OpenAI key
MQTT_BROKER_IP=<broker IP>       # IP of the machine running Mosquitto
MQTT_PASSWORD=<your password>    # Must match Mosquitto pwfile AND firmware
YELLOW_FLOW_RATE_ML_PER_SEC=8.8  # Calibrate with graduated cup test
POWER_CONSTRAINT_MA=300
MOCK_MODE=false
```

### Step 3 — Configure Mosquitto

Create `mosquitto.conf`:
```
listener 1883
allow_anonymous false
password_file /path/to/pwfile
```

Create `pwfile` entries:
```bash
mosquitto_passwd -c pwfile refinery_node       # enter MQTT_PASSWORD
mosquitto_passwd pwfile attack_demo            # enter attack_demo_password (for demo script)
```

Start broker:
```bash
mosquitto -c mosquitto.conf -v
```

### Step 4 — Flash ESP32 Firmware

For each ESP32:

1. Open `firmware/esp32_direct_node.ino` in Arduino IDE
2. Edit the operator configuration section:
```cpp
#define NODE_PUMP_ID "red"          // "red", "blue", or "yellow" — unique per board

const char *WIFI_SSID     = "laksh's A35";
const char *WIFI_PASSWORD = "lakshjain7";
const char *MQTT_PASSWORD = "<same as .env MQTT_PASSWORD>";
// Fixed constants — do not change:
const char *MQTT_BROKER   = "<same as MQTT_BROKER_IP in .env>";
const char *SECRET_KEY    = "H4ckath0n_TrU5t_K3y_99!";
```
3. Install libraries via Library Manager:
   - PubSubClient (Nick O'Leary)
   - ArduinoJson (Benoit Blanchon)
   - Adafruit INA219
4. Select board: `ESP32 Dev Module`
5. Flash. Open Serial Monitor at 115200 baud.
6. Verify output: `[WiFi] Connected! IP: ...` then `[MQTT] Connected successfully!`
7. Repeat for each board, changing only `NODE_PUMP_ID`.

### Step 5 — Fix Remaining Code Bug

Add `import sys` to `orchestrator.py`:

```python
# In orchestrator.py, add to the import block:
import sys
```

### Step 6 — Start Python System

```bash
# Normal mode (requires hardware + broker running)
python main.py

# Simulated mode (no hardware needed — note: ACK timeouts will enter ERROR)
python main.py --mock
```

Expected boot sequence:
```
[INIT] Initializing audit database...
[INIT] Starting WebSocket server on ws://localhost:8000/ws
[INIT] Connecting to MQTT broker 172.20.86.22:1883...
[MQTT] Connected to broker 172.20.86.22:1883
[DMS] Dead Man's Switch armed. Timeout: 3.0s
[BOOT] Step 6: Initializing AI Orchestrator (LangChain/OpenAI)...
[BOOT] Step 6: SUCCESS (Orchestrator ready)
[READY] ZERO-TRUST REFINERY ONLINE
[READY] Dashboard: http://localhost:8000
```

### Step 7 — Open Dashboard

Navigate to `http://localhost:8000` in a browser.

Verify:
- Status bar shows `WS: CONNECTED`
- System state shows `IDLE`
- After ~1s: power telemetry graph starts updating (real hardware only)

### Step 8 — Test Commands

```
"pour 50ml blue"
"mix 25ml purple"
"fill 30ml green"
```

Expected behavior:
1. Left panel shows reasoning steps
2. Center panel shows pump animation + cup fill
3. Mosquitto logs show PUBLISH to refinery/cmd/pump
4. ESP32 Serial shows Gate 0-7 PASS then `[RELAY] ACTUATING`
5. Right panel shows ACK_OK entry

### Step 9 — Attack Demo

With broker running:
```bash
python attack_demo.py --broker 172.20.86.22 --port 1883
```

Expected: `✓ ATTACK RESULT: ESP32 REJECTED. PUMP DID NOT FIRE.`

### Step 10 — Verify Audit Log

```bash
sqlite3 refinery_audit.db
.tables
SELECT * FROM commands;
SELECT * FROM security_events;
SELECT * FROM state_transitions;
SELECT * FROM ai_reasoning;
```

---

*End of MASTER_SYSTEM_ARCHITECTURE.md*
*Document covers: 9 Python modules, 4 firmware files, 1 React component, 1 MQTT broker, 5 SQLite tables.*
