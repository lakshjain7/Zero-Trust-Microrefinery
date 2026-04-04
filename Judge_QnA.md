# ZERO-TRUST AGENTIC MICRO-REFINERY — Judge Q&A Reference
### Complete Technical Breakdown in Plain English

---

## TABLE OF CONTENTS

1. [What Is This Project? (30-Second Pitch)](#1-what-is-this-project)
2. [The Big Picture — How Everything Connects](#2-the-big-picture)
3. [The Hardware Layer](#3-the-hardware-layer)
4. [The Firmware Layer (ESP32)](#4-the-firmware-layer)
5. [The Communication Layer (MQTT)](#5-the-communication-layer)
6. [The Cybersecurity Layer (Zero-Trust + HMAC)](#6-the-cybersecurity-layer)
7. [The Agentic AI Layer (LangChain Orchestrator)](#7-the-agentic-ai-layer)
8. [The Safety Governor (Middleware)](#8-the-safety-governor)
9. [The Backend (Python + FastAPI)](#9-the-backend)
10. [The Frontend Dashboard (React)](#10-the-frontend-dashboard)
11. [The Three Demo Scenarios](#11-the-three-demo-scenarios)
12. [Judge Questions & Answers](#12-judge-questions--answers)

---

## 1. What Is This Project?

**In one sentence:** A physical liquid-mixing machine that is controlled by an AI brain, protected by military-grade cryptography, and monitored through a real-time industrial dashboard — all built in 48 hours.

**What it physically does:**
- There are 3 pumps connected to cups of colored liquid (Red, Blue, Yellow)
- You type a command like *"Mix 50ml of Purple using less than 300mA of power"*
- An AI figures out that Purple = Red + Blue, calculates exactly how long each pump needs to run, checks if running both at once would use too much electricity, decides to run them one at a time, and physically activates the pumps
- Every command is cryptographically signed — if anyone tries to hack the system and send a fake command, the hardware itself rejects it without the pump ever firing

**Why this is impressive:**
- It combines AI decision-making + physical hardware control + cybersecurity in one working system
- The AI doesn't just follow hardcoded rules — it *reasons* about physics constraints in real time
- The security isn't just software — it's enforced at the microcontroller level (the chip itself is the last line of defense)
- Every single decision is logged forever in a tamper-evident database

---

## 2. The Big Picture

Here is the complete flow from a human typing a command to a pump physically turning on:

```
YOU TYPE A COMMAND IN THE DASHBOARD
            │
            ▼
    [React Dashboard]  ←── WebSocket ──→  [FastAPI Python Backend]
                                                    │
                                          [LangChain AI Agent]
                                          "What does Purple mean?"
                                          "How long for 25ml?"
                                          "Will power exceed 300mA?"
                                                    │
                                          [Safety Governor]
                                          "Is the cup empty?"
                                          "Is current draw safe?"
                                          "Is duration under 10s?"
                                                    │
                                          [HMAC Crypto Signer]
                                          Signs command with secret key
                                          Adds timestamp + unique ID
                                                    │
                                              [MQTT Broker]
                                          (Message relay over WiFi)
                                                    │
                                           [ESP32 Microcontroller]
                                           Core 0: "Is this signature valid?"
                                           Core 0: "Is this a replay attack?"
                                           Core 0: "Is the timestamp fresh?"
                                                    │
                                           Core 1: GPIO pin drops LOW
                                                    │
                                              [RELAY MODULE]
                                                    │
                                                [PUMP FIRES]
                                                    │
                                            [Liquid flows into cup]
                                                    │
                                            [Sensor confirms fill]
                                                    │
                                          [ACK sent back to Python]
                                                    │
                                        [Dashboard updates in real time]
```

Every arrow in this diagram is secured, logged, and verified. If anything fails at any step, the system defaults to ALL PUMPS OFF.

---

## 3. The Hardware Layer

### What physical components are there?

| Component | What It Does | Location |
|-----------|-------------|----------|
| ESP32 Microcontroller | The brain of the physical system. Runs two tasks simultaneously on two CPU cores. | Central hub |
| 3 Peristaltic Pumps | Physically push liquid through tubes. Red (GPIO 25), Blue (GPIO 26), Yellow (GPIO 27) | Source side |
| 4-Channel Relay Module | Acts like a light switch. When the ESP32 drops a GPIO pin LOW, the relay closes and the pump turns on | Between ESP32 and pumps |
| HC-SR04 Ultrasonic Sensor | Measures how full the target cup is by bouncing sound waves. Like a bat's echolocation. | Above target cup |
| INA219 Current Sensor | Measures how many milliamps the pumps are drawing in real time. Like an electricity meter | On the power rail |
| Source Cups (3) | Hold the colored liquids (Red, Blue, Yellow) | Left side |
| Target Cup (1) | The receptacle where the mixed liquid collects | Right side |

### How are the pumps physically controlled?

The relay module is **Active LOW** — this is a key safety design:
- By **default**, all GPIO pins are HIGH → all relays OPEN → all pumps OFF
- To turn a pump ON, the ESP32 drops a GPIO pin to LOW → relay closes → pump activates
- If the ESP32 loses power, crashes, or loses WiFi — pins go HIGH by default → pumps automatically stop
- This means the **fail-safe state is always "off"** — you have to actively keep pumps running

### Why are there two power rails?

- **Logic Rail:** USB power from laptop → runs ESP32 and sensors only (low current, 500mA max)
- **Load Rail:** External 5V/2A wall adapter → runs the pumps and relay coils (high current)
- The INA219 sensor sits on the Load Rail and measures pump current
- Common ground between both → they share a reference point
- This isolation protects the ESP32 from power spikes when pumps start/stop

### Flow rates (physical constants, never estimated):
- Red pump: **8.5 ml/second**
- Blue pump: **9.1 ml/second**
- Yellow pump: **8.8 ml/second** (calibrated per unit)
- Each pump draws approximately **200mA** under load

---

## 4. The Firmware Layer

**File:** `firmware/refinery_firmware.ino`

### What is FreeRTOS?

FreeRTOS is a real-time operating system for microcontrollers. It lets the ESP32 run multiple tasks truly in parallel on its two CPU cores — like having two separate mini-computers inside one chip.

### Why split across two cores?

**This is the core security architecture of the entire system.**

| Core 0 — Security Watchdog | Core 1 — Physical Worker |
|---------------------------|--------------------------|
| Higher priority (2) | Lower priority (1) |
| Handles all incoming MQTT messages | Controls the physical pumps |
| Verifies every cryptographic signature | Reads sensors (distance, current) |
| Checks timestamps and replay attacks | Publishes telemetry back to Python |
| **Never touches GPIO pins** | **Never reads MQTT directly** |
| Sends approved commands to a queue | Reads from queue and fires pumps |

**Why this matters:** Core 0 acts as a bouncer. No command ever reaches the physical hardware (Core 1) without first being cryptographically verified by Core 0. They communicate through a FreeRTOS queue with depth 1 — meaning only ONE command can be in-flight at any time.

### What does the boot sequence look like?

1. Connect to WiFi hotspot
2. Wait up to 10 seconds for a timestamp sync from Python (to calibrate the internal clock)
3. Publish a boot event to Python — including why it booted (power-on, brownout, crash, etc.)
4. If brownout is detected → Python immediately puts the system in ERROR state (a brownout means the hardware may be in an unknown physical state)
5. Launch Core 0 and Core 1 tasks

### How does the HC-SR04 sensor work reliably?

Raw ultrasonic readings over a liquid surface are garbage — the liquid ripples scatter the sound waves and give junk readings. The firmware solves this with:

1. **Exponential Moving Average (EMA):** Each new reading is blended with history: `ema = 0.2 × new + 0.8 × previous`. This smooths out noise.
2. **Jitter rejection:** If a reading deviates more than 30% from the current EMA, it's thrown out entirely — it's a scatter artifact, not reality.
3. **State classification:**
   - Distance 10–12cm → `EMPTY_CUP` (safe to fill)
   - Distance < 5cm → `FULL_CUP` (stop immediately)
   - Distance > 15cm → `NO_CUP` (no receptacle present)

---

## 5. The Communication Layer

**Protocol:** MQTT (Message Queue Telemetry Transport)
**Broker:** Mosquitto running on the Windows laptop at `192.168.137.1:1883`
**Network:** Local WiFi hotspot created by the laptop

### What is MQTT?

MQTT is a lightweight publish/subscribe messaging protocol designed for IoT devices. Think of it like a post office:
- Publishers drop messages into named "topics" (like mailboxes)
- Subscribers register interest in topics and receive messages automatically
- The broker (Mosquitto) is the post office that routes everything

### Topic Schema (the contract between all components):

```
Python → ESP32 (Commands):
  refinery/cmd/pump          ← pump actuation commands
  refinery/cmd/sync          ← timestamp synchronization

ESP32 → Python (Telemetry):
  refinery/telemetry/power      ← INA219 current readings every 500ms
  refinery/telemetry/distance   ← HC-SR04 EMA distance every 500ms
  refinery/telemetry/ack        ← "I executed your command" confirmation
  refinery/telemetry/heartbeat  ← "I'm still alive" pulse every 500ms
  refinery/telemetry/boot       ← startup event with reset reason
  refinery/telemetry/security   ← HMAC failures, replay attempts, drift warnings
```

### What is the Dead Man's Switch?

Python monitors the heartbeat topic. If no heartbeat arrives within **3 seconds**, Python assumes the ESP32 is dead or disconnected, immediately transitions to ERROR state, and sends a signed emergency halt command. This prevents a scenario where the ESP32 silently crashes while a pump is running.

---

## 6. The Cybersecurity Layer

**This is the centerpiece of the "Zero-Trust" claim.**

### What does "Zero-Trust" mean here?

Traditional security: "If you're on our network, we trust you."
Zero-Trust: "We trust nobody. Every single command must prove its authenticity cryptographically, regardless of where it comes from."

In this system: even if an attacker has valid MQTT broker credentials and can connect to the broker — the ESP32 will still reject their commands because the commands aren't signed with the correct key.

### What is HMAC-SHA256?

HMAC (Hash-based Message Authentication Code) using SHA-256 is a cryptographic algorithm that:
1. Takes a **secret key** + a **message** and produces a unique 64-character "fingerprint" (the signature)
2. The same key + same message always produces the same fingerprint
3. Without the secret key, you cannot produce a valid fingerprint — even if you can see every message on the network
4. Changing even one character of the message produces a completely different fingerprint (avalanche effect)

**Our secret key:** `H4ckath0n_TrU5t_K3y_99!` (hardcoded for demo — in production this lives in a hardware security module)

### What exactly gets signed?

The signature is NOT computed over the JSON payload (that would be vulnerable to formatting attacks). Instead, a **canonical string** is constructed:

```
"{pump}|{duration_ms}|{timestamp}|{nonce}|{packet_id}"
```

Example:
```
"red|2940|1711234567|42|f47ac10b-58cc-4372-a567-0e02b2c3d479"
```

This canonical format is an agreed contract between Python and the ESP32. Both sides compute the HMAC over exactly this string.

### What are the 9 layers of defense?

**Layer 1 — MQTT Authentication (Outer Perimeter)**
- Mosquitto broker requires username/password to connect
- ACL file restricts which users can publish to which topics
- But this is NOT the real security — it just slows down casual attackers

**Layer 2 — HMAC-SHA256 Signature (The Real Gate)**
- ESP32 Core 0 recomputes the HMAC for every incoming command
- If the computed HMAC doesn't match the received signature → packet dropped, security event fired
- Without the secret key, no valid signature can be forged
- Uses constant-time comparison to prevent timing side-channel attacks

**Layer 3 — Nonce-Based Replay Prevention**
- Every command has a nonce: a monotonically increasing number (1, 2, 3, ...)
- ESP32 tracks the last seen nonce. Any incoming nonce ≤ last seen → REPLAY attack, rejected
- Prevents "capture and replay" attacks where an attacker records a valid signed command and sends it again later

**Layer 4 — Time Window Enforcement**
- Every command carries a Unix timestamp
- ESP32 rejects any command where `|current_time - command_time| > 2 seconds`
- Python rejects telemetry where `|current_time - telemetry_time| > 5 seconds`
- Prevents using captured commands hours or days later

**Layer 5 — Packet ID Idempotency**
- Every command has a unique UUID (e.g., `f47ac10b-58cc-4372-a567-0e02b2c3d479`)
- ESP32 keeps a circular buffer of the last 20 executed packet IDs
- Even if a command somehow passes nonce and time checks, if its ID was already executed → rejected
- Prevents double-actuation from broker retries

**Layer 6 — Rate Limiting**
- Max 5 messages per second on the command topic (Core 0)
- Flooding attacks are detected and logged

**Layer 7 — Intrusion Escalation**
- Python counts every HMAC_FAIL and REPLAY event
- After 3 intrusions → BREACH state → ALL pumps forced off → manual reset required
- This is irreversible without physical operator intervention

**Layer 8 — Time Drift Escalation (Section D)**
- A single packet outside the time window = bad packet, drop and log
- THREE consecutive packets outside the window = clock fault, not just a bad packet
- Clock fault → ERROR state → attempt re-sync → block all execution until clock is confirmed good
- Catches scenarios where the ESP32's clock drifts (hardware failure, NTP failure)

**Layer 9 — Fail-Safe Hardware Defaults**
- All GPIO pins default HIGH (pumps OFF) on boot
- If MQTT disconnects → ESP32 forces all GPIOs HIGH immediately, clears command queue
- Dead Man's Switch fires if heartbeat stops → emergency halt published (still HMAC-signed)
- Emergency halt command bypasses the normal queue — highest priority path in firmware

### Why not use AES encryption?

HMAC-SHA256 provides **authentication and integrity** — it proves the message came from someone with the secret key and hasn't been tampered with. AES would add **confidentiality** — hiding the content of messages from eavesdroppers.

For this threat model (controlling pumps over a local network), the concern is:
- Someone injecting fake commands → HMAC stops this ✓
- Someone replaying captured commands → nonce + timestamp stops this ✓
- Someone reading which pump fired → this is not a meaningful security concern

AES key exchange would require 10+ hours of additional build time with zero additional demo value. The threat model doesn't require confidentiality, only integrity and authentication.

### How does the attack demo work?

`attack_demo.py` connects to the MQTT broker with valid credentials (username: `attack_demo`) and publishes:

```json
{
  "pump": "red",
  "duration_ms": 10000,
  "ts": 1711000000,
  "nonce": 1,
  "packet_id": "ATTACK-DEMO-PACKET-001",
  "sig": "FAKE_HASH_abc123_INVALID"
}
```

The ESP32 Core 0 rejects this for THREE simultaneous reasons:
1. Timestamp is ~200 days old (way outside 2-second window)
2. Nonce is 1 (less than last seen nonce, replay attack)
3. HMAC signature is invalid (doesn't match computed hash)

The pump never fires. The right panel of the dashboard shows the red `✗ HMAC_FAIL` row. This demonstrates that MQTT credentials alone are not enough — cryptography is the real gate.

---

## 7. The Agentic AI Layer

**File:** `orchestrator.py`

### What makes this "agentic"?

Regular software follows hardcoded rules: `if color == "purple": run_red(); run_blue()`.

An **agentic** system gives an AI a set of tools and a goal, then lets it **reason** about how to achieve that goal using those tools — without being told step by step what to do.

Our AI:
- Receives a natural language command: *"Mix 50ml of purple, stay under 300mA"*
- Has access to 4 tools it can call
- **Reasons** through the physics: Purple = Red + Blue. Red at 8.5ml/s for 25ml = 2,941ms. Blue at 9.1ml/s for 25ml = 2,747ms. Running both simultaneously = ~400mA, exceeds 300mA limit. Must run sequentially.
- **Decides** to call `actuate_pump("red", 2941)` then `actuate_pump("blue", 2747)`
- There is NO code that says "if purple, do sequential" — the AI figured this out from physics

### What LLM powers it?

GPT-4o via OpenAI API, called through LangChain with temperature=0 (deterministic, no creativity). The LLM is given a physics-injected system prompt with the real hardware constants.

### What are the 4 tools the AI can call?

**Tool 1: `check_receptacle_state()`**
- Reads the latest HC-SR04 EMA distance from the telemetry buffer
- Returns: `EMPTY_CUP` / `FULL_CUP` / `NO_CUP` / `SENSOR_ERROR`
- Raises an error if the last reading is older than 2 seconds (stale data)
- The AI MUST call this before every pump actuation (enforced in system prompt)

**Tool 2: `get_current_power_draw()`**
- Reads the latest INA219 milliamp reading
- Returns current mA + timestamp + age in milliseconds
- The AI uses this as a baseline before calculating if an additional pump would exceed the power limit

**Tool 3: `actuate_pump(pump_id, duration_ms)`**
- This is the most important tool — it's what actually fires a pump
- But it doesn't go directly to hardware. It goes: AI → Safety Governor → HMAC signer → MQTT → ESP32
- Returns `EXECUTED` / `REJECTED` / `ERROR` with the packet ID

**Tool 4: `alert_human(message)`**
- Called by the AI when it detects a situation requiring human intervention (e.g., cup is full)
- Transitions the system to PAUSED state
- Starts an autonomous monitoring loop: every second, check if the cup was replaced
- When sensor reads EMPTY_CUP again → automatically resumes execution without asking the human again
- This demonstrates **continuous autonomous operation** across a state boundary

### What is the ReAct pattern?

The AI uses the **ReAct** (Reasoning + Acting) framework:
1. **Reason:** "I need to check if the cup is ready before doing anything"
2. **Act:** Call `check_receptacle_state()`
3. **Observe:** "Returns EMPTY_CUP — good to proceed"
4. **Reason:** "Purple requires Red + Blue. Let me calculate durations..."
5. **Act:** Call `get_current_power_draw()`
6. **Observe:** "Baseline is 15mA. Adding 200mA for one pump = 215mA, safe."
7. **Reason:** "Running both parallel = 400mA > 300mA limit. Must be sequential."
8. **Act:** Call `actuate_pump("red", 2941)`
9. **Observe:** "EXECUTED, ACK received."
10. **Reason:** "Check cup state before second pump..."
...and so on

This reasoning stream is broadcast live to the LEFT panel of the dashboard as it happens.

### What is Plan Versioning?

Every time the LangChain agent is invoked, it receives a UUID4 plan ID (e.g., `a3f9c12d`). Every reasoning step, tool call, and command logged to the SQLite database is tagged with this plan ID. The dashboard displays the current plan ID in the AI panel header. This creates a complete, auditable trace of every AI decision.

### Is the AI trusted?

**No. Deliberately.** The LLM is treated as an untrusted component. This is realistic — LLMs can hallucinate, be prompt-injected, or misinterpret constraints. The Safety Governor (next section) is deterministic code that the LLM cannot override.

---

## 8. The Safety Governor

**File:** `safety_governor.py`

### What is it?

A deterministic middleware layer that sits between the AI and the hardware. Every `actuate_pump()` call from the AI must pass through 8 checks before it's allowed to proceed. The Governor always wins — the AI cannot argue with it.

### The 8 checks (in order):

1. **System State Guard:** If the system is in ERROR or BREACH state, reject. No exceptions.
2. **Execution Blocked Guard:** If MQTT just reconnected, execution is blocked until a human resets. Physical state is unknown after a comms gap.
3. **Duration Cap:** `duration_ms < 10,000ms` (10 seconds). Any pump running for more than 10 seconds is an overflow hazard. Hard reject.
4. **Pump ID Validation:** Must be exactly `"red"`, `"blue"`, or `"yellow"`. Reject anything else (prevents prompt injection like `"red; rm -rf /"`)
5. **Telemetry Sanity:** Last sensor readings must be within physical bounds (0–600mA, 0–400cm) and recent (< 2 seconds old).
6. **Receptacle State:** FULL_CUP → reject (overflow). NO_CUP → reject (no target). SENSOR_ERROR → reject (can't verify).
7. **Power Prediction:** `current_baseline_mA + 200mA > constraint` → reject and instruct AI to replan sequentially.
8. **Idempotency Check:** If this packet ID was already acknowledged, return the cached result without republishing.

### What happens after approval?

If all 8 checks pass:
1. The Governor acquires an `asyncio.Lock()` — only ONE command can be in-flight globally at any time
2. The HMAC crypto layer signs the payload
3. The command is published to MQTT
4. The Governor waits up to 3 seconds for an ACK from the ESP32
5. If no ACK: retry up to 3 times (3-strike system)
6. After 3 failures: ERROR state
7. Every decision (APPROVED or REJECTED) is logged to SQLite with the reason

### Why is this separate from the AI?

Because deterministic safety logic should never be reasoned about — it should just run. The AI might (incorrectly) reason that a slightly longer duration is fine, or that the power draw will be manageable. The Governor doesn't reason — it compares numbers and enforces hard limits.

---

## 9. The Backend

### File structure:

| File | Role |
|------|------|
| `main.py` | Entry point. Starts everything in order, runs the CLI. |
| `orchestrator.py` | LangChain AI agent + tool definitions + reasoning stream |
| `mqtt_client.py` | MQTT connection management, telemetry routing, security verification |
| `crypto_transport.py` | HMAC signing (outbound) and verification (inbound) |
| `safety_governor.py` | Deterministic safety gate between AI and hardware |
| `system_state.py` | Single source of truth for system state — thread-safe |
| `websocket_server.py` | FastAPI WebSocket bridge between backend and React dashboard |
| `audit_db.py` | SQLite database — logs everything forever |
| `dead_mans_switch.py` | Background watchdog — kills system if heartbeat stops |
| `attack_demo.py` | Standalone script to demonstrate the cyber breach scenario |

### What is the startup sequence?

1. Load `.env` (API keys, MQTT credentials, pump calibration)
2. Initialize SQLite audit database (WAL mode for concurrent reads)
3. **Crash recovery check:** Read last state from database. If it was EXECUTING or PAUSED → go to ERROR immediately (pump may be stuck on, physical state unknown)
4. Start FastAPI WebSocket server on port 8000 (background thread)
5. Connect to MQTT broker (skip in mock mode)
6. Arm Dead Man's Switch
7. Inject orchestrator reference into WebSocket server (enables dashboard command dispatch)
8. Start interactive command loop

### What is the audit database?

SQLite database (`refinery_audit.db`) running in WAL (Write-Ahead Log) mode. Five tables:

- **`commands`:** Every pump actuation — approved or rejected — with the Governor's reason, the ACK status, and the state at execution time
- **`security_events`:** Every HMAC_FAIL, REPLAY, RATE_LIMIT_HIT — never stores full signatures, only hash previews
- **`state_transitions`:** Every state change (IDLE→EXECUTING, etc.) with the trigger and an AI reasoning snippet
- **`ai_reasoning`:** Every LangChain reasoning step, grouped by plan_id
- **`connectivity_events`:** MQTT disconnects, reconnects, clock drift escalations

This database is the forensic record of the entire session. In a real incident, you could reconstruct exactly what happened, why, and when.

### What is MOCK mode?

Running `python main.py --mock` activates synthetic telemetry:
- Power: sine wave 150–280mA, spikes to 310mA every 30 seconds (shows threshold breach)
- Distance: cycles EMPTY→FULL→NO_CUP every 20 seconds (shows state transitions)
- HMAC_FAIL: fires every 45 seconds (shows security events)
- MQTT disconnect: fires every 60 seconds (shows connectivity resilience)
- Clock drift: DRIFT_ESCALATION at t=90s, CLOCK_FAULT at t=100s

The LLM still calls the real OpenAI API. Pump actuations are simulated (no GPIO). This lets the full demo run without hardware present.

---

## 10. The Frontend Dashboard

**File:** `zero-trust-dashboard.jsx`

### Three-panel layout:

**LEFT PANEL — AI Orchestrator (30% width)**
- Live reasoning stream: every LangChain step appears as a card sliding in from the bottom
- Tool call pills: show exactly which tool the AI is calling (`⚙ actuate_pump("red", 2941ms)`)
- Governor rejection card: amber warning when the Safety Governor overrides the AI
- Plan ID badge: current UUID showing which execution plan is active
- Natural language input: type commands directly, dispatched via WebSocket to Python backend
- Three preset buttons for the demo scenarios

**CENTER PANEL — Physical System (40% width)**
- Live power chart: 60-point rolling Bezier curve, color shifts green→yellow→red as it approaches 300mA threshold
- Physical simulation: SVG diagram of all 3 source cups, tubes, relay indicators, target cup with animated liquid fill
- Cup color updates in real time based on which pumps are active (Red+Blue = Purple fill, Blue+Yellow = Green fill, etc.)
- HC-SR04 sensor with sonar ring animation, scan line, NO_CUP warning triangle, red X on sensor error
- INA219 sensor with live mA reading
- Relay indicators that glow when GPIO drops LOW, mint-green ACK pulse when command is confirmed
- Start/Pause/Halt action bar

**RIGHT PANEL — HMAC Watchdog (30% width)**
- Live security log: every MQTT packet with timestamp, topic, status, and hash preview
  - Green left border: verified packet
  - Red row: HMAC_FAIL
  - Amber row: REPLAY with time-delta displayed
  - Amber row: Governor decisions
  - Yellow row: Time drift escalation
  - Orange row: Clock fault
- Threat panel: appears during BREACH with intrusion details
- Crypto stats: packets verified, dropped, uptime, shared key (masked), broker status, peak mA

### How does the dashboard connect to the backend?

WebSocket at `ws://localhost:8000/ws`. The connection:
- Auto-reconnects every 2 seconds if dropped
- Receives typed events (state changes, telemetry, AI reasoning, security events, etc.)
- Sends commands back to backend when user types in the input and hits Send/Enter

### What are the visual state indicators?

| State | Visual |
|-------|--------|
| IDLE | Gray heartbeat pulse, all panels at 60% brightness |
| EXECUTING | Mint green status badge, AI cards streaming, pump animations active |
| PAUSED | Amber overlay on center, flashing banner, all pump controls locked |
| BREACH | Hard-cut 3-beat red vignette, right panel glitch effect, red X on all relays |
| ERROR | Orange pulsing vignette, error banner in center |

State transitions trigger **sweep animations**: a translucent gradient wipes across all panels:
- IDLE→EXECUTING: left-to-right cyan sweep (system waking up)
- EXECUTING→PAUSED: right-to-left amber sweep (momentum interrupted)
- PAUSED→EXECUTING (resume): left-to-right mint sweep, faster (snapping back)
- ANY→BREACH: NO sweep — instant hard cut (breach has no warning)

---

## 11. The Three Demo Scenarios

### Test Case A — AI Constraint Reasoning (Agency)
**Command:** "Mix 50ml Purple / ≤300mA"

1. AI focuses left panel — reasoning stream begins
2. AI reasons: Purple = Red + Blue
3. AI calculates: Red 2,941ms for 25ml, Blue 2,747ms for 25ml
4. AI detects: running both simultaneously = ~400mA > 300mA limit
5. AI **rewrites the plan** to sequential execution
6. Focus shifts to center — Red pump fires (relay glow, fluid animation)
7. ACK received — mint green pulse on relay icon
8. Check cup state — still EMPTY_CUP → proceed
9. Blue pump fires
10. ACK received — power graph stays below 300mA line throughout
11. AI logs: "50ml Purple dispensed. Constraint satisfied." → IDLE

**What this proves:** The AI reasons about physical constraints without hardcoded logic.

### Test Case B — Continuous Batching + State Machine (Safety)
**Command:** "2x Green / Cup Swap"

1. Batch 1 begins: Blue + Yellow pump (Green mixture)
2. HC-SR04 reads 3cm → FULL_CUP detected
3. System transitions to PAUSED — amber sweep right-to-left
4. Amber banner: "RECEPTACLE FULL — REPLACE CUP TO RESUME"
5. All pump controls lock — nothing can fire
6. Demonstrator physically removes cup → sensor reads >15cm → NO_CUP warning
7. Demonstrator places empty cup → sensor reads 12cm → EMPTY_CUP
8. System **automatically resumes** — no new command needed — mint sweep left-to-right
9. Batch 2 fires
10. System returns to IDLE

**What this proves:** The AI monitors physical state autonomously and safely manages multi-batch operations across human interventions.

### Test Case C — Zero-Trust Cyber Breach (Cybersecurity)
**Action:** Cybersecurity teammate runs `python attack_demo.py`

1. Attack script connects with valid MQTT credentials
2. Publishes fake pump command with invalid signature, stale timestamp, replayed nonce
3. Dashboard: **no sweep** — instant hard-cut red vignette (3-beat pulse)
4. Right panel takes focus — harsh red row: `✗ HMAC_FAIL  FAKE_HASH_abc123`
5. Right panel glitch animation fires
6. Center panel: relay icons show red X overlays — no pump movement
7. Left panel AI log: "Unauthorized actuation attempt blocked. Core 0 cryptographic verification failed."
8. Attack script terminal: "ATTACK RESULT: ESP32 REJECTED. PUMP DID NOT FIRE."
9. Slow return to IDLE — threat neutralized sweep

**What this proves:** Network access alone is not enough. The ESP32 itself enforces cryptographic integrity at the hardware level.

---

## 12. Judge Questions & Answers

---

### GENERAL UNDERSTANDING

**Q: What problem does this project solve?**

A: Industrial control systems (pumps, valves, actuators) are increasingly connected to networks for remote monitoring. This creates an attack surface — malicious actors can send fake commands to physical hardware. Most IoT systems authenticate at the network level only: if you're on the WiFi, you're trusted. This project demonstrates a zero-trust model where the physical hardware itself performs cryptographic verification — network access alone is never sufficient to actuate anything.

---

**Q: What does "zero-trust" actually mean in your context?**

A: It means the system trusts no message by default, regardless of source. Even if a message arrives on the correct MQTT topic from a device on the same local network with valid broker credentials — it is still rejected unless it carries a valid HMAC-SHA256 signature computed with the shared secret key. Trust must be cryptographically proven with every single command.

---

**Q: Is this a real working system or a simulation?**

A: It is a real working system. The pumps physically fire when commanded, the HC-SR04 sensor genuinely reads the liquid level, and the INA219 genuinely measures current draw. Running `--mock` simulates hardware for demo purposes but the AI, cryptography, state machine, audit log, and all security layers are real and running in all modes.

---

### CYBERSECURITY QUESTIONS

**Q: What attack vectors does this defend against?**

A:
- **Command injection / spoofing:** Attacker sends fake pump commands → HMAC fails, rejected at ESP32
- **Replay attacks:** Attacker captures a valid command and resends it later → nonce tracking + timestamp window reject it
- **Man-in-the-middle:** Attacker intercepts and modifies commands in transit → HMAC detects tampering (any bit change = different hash)
- **Flooding/DoS:** Attacker sends thousands of messages to overwhelm the system → rate limiter (5 msg/sec) detects and logs it
- **Timing side-channel attacks:** Attacker measures how long HMAC comparison takes to deduce the key → constant-time comparison eliminates this
- **Crash-then-resume exploits:** Attacker triggers a crash hoping to resume in a known state → crash recovery always boots to ERROR, requires manual reset

---

**Q: What is HMAC-SHA256 in simple terms?**

A: Imagine you and a friend agree on a secret password. You both have a machine that: takes any message + the password, and produces a unique 64-character code (the HMAC). Anyone watching your messages can see the code but cannot figure out the password from it, and cannot produce a valid code for a new message without the password. When you receive a message, you compute the code yourself. If it matches what was sent, the message is authentic and untampered.

---

**Q: The secret key is hardcoded in the source code — isn't that insecure?**

A: Yes, and we acknowledge this explicitly in code comments. In a production deployment, the key would live in an ATECC608A Hardware Security Module (a tamper-resistant chip that stores secrets in fused memory that cannot be read out, even by the chip's own firmware). For a 48-hour hackathon demo, the hardcoded key is appropriate and the limitation is documented. The cryptographic architecture (HMAC, nonce, timestamp, idempotency) is production-grade — only the key storage is demo-grade.

---

**Q: What happens when 3 HMAC failures are detected?**

A: The `intrusion_count` counter increments on every HMAC_FAIL or REPLAY event. When it exceeds 3, the system transitions to BREACH state: all GPIO pins are forced HIGH (pumps off), execution is permanently blocked, the dashboard shows the red vignette, and only a manual operator reset (physical presence at the terminal) can restore IDLE state. This cannot be triggered remotely.

---

**Q: Why does the ESP32 reject commands independently? Can't Python just not send bad ones?**

A: Defense in depth. If the Python orchestrator is compromised (e.g., malicious code injected through a supply chain attack, or the system prompt is manipulated), an attacker could try to send commands directly to the MQTT broker. The ESP32's independent verification means the hardware is the last line of defense that cannot be bypassed regardless of what happens in software above it. Physical actuation requires cryptographic proof at the chip level.

---

### AGENTIC AI QUESTIONS

**Q: What makes this system "agentic" vs just a chatbot with buttons?**

A: An agentic system uses an LLM to reason about goals and autonomously decide what actions to take using available tools — without a human specifying each step. In this system:
- The user says "Mix 50ml Purple under 300mA" — one natural language goal
- The AI independently decides: check the cup, check current draw, calculate pump durations, identify the power constraint, rewrite the plan to be sequential, execute two separate tool calls in sequence, verify after each step
- There is no code path that says "if color is purple, run red then blue" — this logic emerges from the AI's reasoning about physics

---

**Q: What if the AI makes a wrong decision?**

A: The Safety Governor is deterministic code that the AI cannot override. Even if the AI hallucinates a duration of 50,000ms, the Governor hard-caps at 10,000ms and rejects it. Even if the AI tries to run two pumps simultaneously to save time, the Governor checks predicted power draw and rejects the command if it would exceed the constraint. The AI can reason incorrectly — the Governor cannot be reasoned with. It compares numbers against hard limits.

---

**Q: What happens if the OpenAI API goes down during a demo?**

A: The system transitions to ERROR state. Python pushes the error to the dashboard. The operator can use the demo preset buttons (which run pre-scripted demo sequences locally in the dashboard without requiring the AI) to demonstrate the test cases visually. The hardware, security layer, and all sensor telemetry continue functioning independently of the AI.

---

**Q: Could someone manipulate the AI through prompt injection?**

A: The system prompt is injected once at initialization and cannot be modified through the natural language input. The input only feeds the "user message" to the agent. Additionally, even a successfully injected prompt that caused the AI to call `actuate_pump("red; drop table; ", 99999)` would be immediately rejected by the Safety Governor's pump ID validation (must be exactly "red", "blue", or "yellow") and duration cap. The AI is untrusted by design.

---

### HARDWARE QUESTIONS

**Q: How do you know exactly how much liquid was dispensed?**

A: We use the physical pump flow rates as ground truth:
- Red: 8.5 ml/second
- Blue: 9.1 ml/second
- Yellow: 8.8 ml/second (calibrated per unit using a 10-second graduated-cup test)

Volume = flow_rate × (duration_ms / 1000)

The AI calculates duration_ms = (target_volume / flow_rate) × 1000 and rounds to the nearest millisecond. This is open-loop control — we're trusting the pump's consistency rather than measuring actual output volume. In production, a flow meter would close this loop.

---

**Q: What prevents a pump from running forever if the software crashes?**

A: Three independent mechanisms:
1. **Active LOW relay:** GPIO defaults HIGH (pump OFF). A crash means GPIO goes HIGH → pump stops
2. **Dead Man's Switch:** Python monitors heartbeat. No heartbeat in 3 seconds → ERROR state + emergency halt command
3. **Duration cap:** The Safety Governor hard-rejects any command over 10,000ms. The ESP32 uses `vTaskDelay()` for exactly the commanded duration and then forces GPIO HIGH regardless of what Python does next

---

**Q: Why does the HC-SR04 use an EMA filter?**

A: Ultrasonic sensors work by bouncing sound waves off a surface and timing the echo. Over a liquid surface, the surface is slightly rippled by pump vibration and convection, causing readings to scatter wildly. A single raw reading might say 8cm, then 15cm, then 7cm in three consecutive polls — all wrong. The Exponential Moving Average blends each new reading with recent history (20% new, 80% history), smoothing out noise. Readings that deviate more than 30% from the EMA are rejected as scatter artifacts. This gives reliable state classification (EMPTY/FULL/NO_CUP) despite the noisy physical environment.

---

### ARCHITECTURE QUESTIONS

**Q: Why use MQTT instead of direct HTTP REST calls?**

A: MQTT is designed for IoT command-and-control:
- **Lightweight:** Much lower overhead than HTTP, important for a microcontroller
- **Publish/subscribe:** Multiple consumers can subscribe to the same topic (Python, a monitoring system, and the dashboard can all receive telemetry simultaneously)
- **QoS levels:** MQTT supports guaranteed-delivery semantics
- **Bidirectional:** Easy to receive telemetry back from the device
- **Persistent connections:** Lower latency for time-sensitive commands vs HTTP's request-response overhead

---

**Q: Why use FreeRTOS on the ESP32? Couldn't you just use a loop?**

A: A simple loop would create a security vulnerability: if the physical execution task (pump control) blocked the loop for 3 seconds while a pump ran, incoming MQTT messages would buffer unprocessed. An attacker could flood the command queue during this window. With FreeRTOS dual-core:
- Core 0 (security) is always running, even while Core 1 is executing a pump command
- Core 0 has higher priority — even Core 1 cannot block security verification
- The queue depth of 1 means only ONE command can be pending at any time, preventing queue-flooding attacks

---

**Q: Why SQLite for the audit log? Why not a cloud database?**

A: Three reasons:
1. **Air gap resilience:** The system operates on a local hotspot with no internet requirement. Cloud database dependency would be a single point of failure for demos.
2. **Forensic locality:** All evidence stays on the physical device. No data leaves the system.
3. **WAL mode:** SQLite's Write-Ahead Log mode allows concurrent reads (dashboard queries) while writes (audit logging) are happening, without blocking.

In production, this would be supplemented by an append-only distributed ledger for tamper-evidence.

---

**Q: What is the WebSocket server for? Why not just have the dashboard poll REST endpoints?**

A: Real-time push vs polling:
- **Polling** (dashboard asks "any updates?" every 500ms): 500ms minimum latency, wasteful bandwidth, misses events between polls
- **WebSocket** (server pushes events instantly): sub-50ms latency, events are never missed, much lower bandwidth

For a live industrial control system demo where we're showing pump activations happening in real time, 500ms polling latency would make the simulation feel laggy and disconnected from reality. WebSocket pushes arrive within single-digit milliseconds of the actual hardware event.

---

**Q: How does the crash recovery work?**

A: On every startup, the system reads the last row from the `state_transitions` table in the audit database. If the last recorded state was EXECUTING or PAUSED, it means the process terminated abnormally while controlling hardware. The system immediately transitions to ERROR state and requires manual operator reset. This prevents the scenario where a crashed process leaves a pump physically running, and a restarted process naively resumes execution without knowing the physical state of the hardware.

---

**Q: What is the difference between `zero-trust-dashboard.jsx` and `zero-trust-dashboard1.jsx`?**

A: `zero-trust-dashboard.jsx` is the production dashboard (1,063 lines) with all features fully implemented. `zero-trust-dashboard1.jsx` is an earlier iteration (664 lines) kept for reference. Only the main file is used in production.

---

### SCALABILITY / FUTURE QUESTIONS

**Q: How would you scale this to a real industrial facility?**

A:
1. Replace hardcoded HMAC key with ATECC608A hardware security module per device
2. Add mutual TLS (mTLS) on MQTT broker for certificate-based device authentication
3. Replace SQLite with an append-only distributed ledger (e.g., Azure Confidential Ledger) for tamper-evident audit
4. Add per-session key derivation (HKDF) for forward secrecy
5. Deploy MQTT broker with redundancy (clustered Mosquitto or AWS IoT Core)
6. Add closed-loop flow metering (actual volume measurement vs calculated)
7. Add offline LLM fallback (local Llama model) for air-gapped deployments
8. Multi-device support with device ID in canonical HMAC string

**Q: What is the biggest limitation of this demo version?**

A: The HMAC secret key is hardcoded in both Python and ESP32 firmware. In a production deployment, key compromise would require firmware re-flashing every device. The ATECC608A HSM solution would allow secure key provisioning without exposing the key in source code. This is the most critical production gap.

---

*Document prepared for JNTU Hackathon 2024 — Zero-Trust Agentic Micro-Refinery Team*
