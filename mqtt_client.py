"""
MQTT Client Manager — Python Side

Wraps paho-mqtt. Handles:
  - Initial connection and subscription
  - Section D: MQTT disconnect → immediate ERROR state + exponential backoff reconnect
  - Section D: Time drift escalation on 3 consecutive out-of-window packets
  - Telemetry routing: verified readings → shared state buffers
  - Dead Man's Switch heartbeat timestamp update
  - Security event routing → SharedState.push_security_event()
  - Clock sync on connect and after DRIFT_ESCALATION recovery

SECURITY RATIONALE: paho-mqtt's default behavior on disconnect is silent —
the client just stops receiving messages. Without an explicit on_disconnect
callback we'd rely solely on the Dead Man's Switch (3000ms blind window).
Explicit disconnect handling closes that window immediately: the instant
the TCP connection drops, we enter ERROR state and block execution.
"""

import asyncio
import json
import os
import time
import threading
import colorama
from colorama import Fore, Style

import paho.mqtt.client as mqtt

from system_state import shared, State
from crypto_transport import crypto
from audit_db import (
    log_security_event, log_connectivity_event, log_ack
)

colorama.init()

# ── MQTT BROKER SETTINGS ──
BROKER_IP   = os.getenv("MQTT_BROKER_IP",   "192.168.137.1")
BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))
USERNAME    = os.getenv("MQTT_USERNAME",    "refinery_node")
PASSWORD    = os.getenv("MQTT_PASSWORD",    "CHANGE_ME_16CHARS")

# ── TOPICS ──
TOPIC_CMD_PUMP   = "refinery/cmd/pump"
TOPIC_CMD_SYNC   = "refinery/cmd/sync"
TOPIC_TELEM_POWER    = "refinery/telemetry/power"
TOPIC_TELEM_DISTANCE = "refinery/telemetry/distance"
TOPIC_TELEM_ACK      = "refinery/telemetry/ack"
TOPIC_TELEM_HEARTBEAT= "refinery/telemetry/heartbeat"
TOPIC_TELEM_BOOT     = "refinery/telemetry/boot"
TOPIC_TELEM_SECURITY = "refinery/telemetry/security"

# ── RECONNECT BACKOFF SCHEDULE (Section D) ──
RECONNECT_DELAYS = [0.5, 1.0, 2.0]   # seconds; after 3 failures: MQTT_UNREACHABLE


class MQTTManager:
    """
    Manages the paho-mqtt client lifecycle.
    All callbacks schedule coroutines on the asyncio event loop
    (passed in from main.py) so they can safely call async functions.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._client = mqtt.Client(client_id="refinery_orchestrator", protocol=mqtt.MQTTv5)
        self._client.username_pw_set(USERNAME, PASSWORD)

        self._client.on_connect    = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message    = self._on_message

        self._reconnect_attempt = 0
        self._reconnecting = False

    def connect(self) -> None:
        """Connect to broker. The network loop is managed by run()."""
        self._client.connect(BROKER_IP, BROKER_PORT, keepalive=10)
        # loop_start() is DEPRECATED in this async refactor to prevent deadlocks.

    async def run(self) -> None:
        """
        Pure async network loop. Runs on the main event loop.
        This eliminates the need for thread-safe cross-loop scheduling.
        """
        print(f"{Fore.CYAN}[MQTT] Starting async network loop (robust)...{Style.RESET_ALL}")
        while True:
            try:
                # Process outgoing/incoming network traffic
                # Using 0.01 instead of 0.0 helps on Windows/Python 3.14 
                # to prevent high CPU and ensure network stack processing.
                self._client.loop(timeout=0.01)
                await asyncio.sleep(0.01) 
            except Exception as e:
                print(f"{Fore.RED}[MQTT] Loop error: {e}{Style.RESET_ALL}")
                await asyncio.sleep(1.0)

    def publish(self, topic: str, payload: dict) -> None:
        """Publish a JSON payload to a topic."""
        self._client.publish(topic, json.dumps(payload), qos=1)

    def _schedule(self, coro) -> None:
        """Sync wrapper to schedule a coroutine on the main event loop."""
        # Since we are now running on the main loop thread, we can use create_task
        try:
            asyncio.create_task(coro)
        except RuntimeError:
            # Fallback for cases where the loop isn't running yet (initial sync)
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ── CONNECT CALLBACK ──
    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc != 0:
            print(f"{Fore.RED}[MQTT] Connect failed: rc={rc}{Style.RESET_ALL}")
            return

        print(f"{Fore.GREEN}[MQTT] Connected to broker {BROKER_IP}:{BROKER_PORT}{Style.RESET_ALL}")
        shared.mqtt_connected = True
        self._reconnect_attempt = 0
        self._reconnecting = False

        # Subscribe to all ESP32 telemetry topics
        # IMPORTANT: Re-subscribe after every reconnect — paho does NOT
        # automatically restore subscriptions after a disconnect.
        for topic in [
            TOPIC_TELEM_POWER, TOPIC_TELEM_DISTANCE, TOPIC_TELEM_ACK,
            TOPIC_TELEM_HEARTBEAT, TOPIC_TELEM_BOOT, TOPIC_TELEM_SECURITY,
        ]:
            client.subscribe(topic, qos=1)

        # Push clock sync to ESP32
        sync_payload = crypto.sign_sync()
        client.publish(TOPIC_CMD_SYNC, json.dumps(sync_payload), qos=1)
        print(f"{Fore.CYAN}[MQTT] Clock sync sent: ts={sync_payload['ts']}{Style.RESET_ALL}")

    # ── DISCONNECT CALLBACK (Section D) ──
    def _on_disconnect(self, client, userdata, rc, properties=None) -> None:
        """
        Called IMMEDIATELY when the broker connection drops.

        SECURITY RATIONALE: We do NOT wait for the Dead Man's Switch
        (3000ms timeout) to catch this. The instant TCP drops, we
        enter ERROR state and block all execution. Physical pump state
        is unknown after a comms gap — resuming without human verification
        could cause over-dispense or hardware damage.
        """
        shared.mqtt_connected = False
        shared.execution_blocked = True
        print(f"{Fore.RED}[MQTT] Disconnected (rc={rc}). Entering ERROR state immediately.{Style.RESET_ALL}")

        self._schedule(self._handle_disconnect())

    async def _handle_disconnect(self) -> None:
        await shared.transition(
            State.ERROR,
            trigger="MQTT_DISCONNECT",
            direction="none",
            detail="MQTT broker disconnected",
            focus="NONE",
        )
        await shared.push_connectivity_event("MQTT_DISCONNECT", "Broker TCP connection lost")
        await log_connectivity_event("MQTT_DISCONNECT", "Broker TCP connection lost")

        # Exponential backoff reconnect
        for attempt, delay in enumerate(RECONNECT_DELAYS, 1):
            await asyncio.sleep(delay)
            print(f"{Fore.YELLOW}[MQTT] Reconnect attempt {attempt}/{len(RECONNECT_DELAYS)}...{Style.RESET_ALL}")
            try:
                self._client.reconnect()
                # on_connect will fire if successful — wait briefly
                await asyncio.sleep(1.0)
                if shared.mqtt_connected:
                    await shared.push_connectivity_event(
                        "MQTT_RECONNECT",
                        "Broker reconnected — manual reset required to resume execution"
                    )
                    await log_connectivity_event("MQTT_RECONNECT", "Reconnected after disconnect")
                    # Do NOT auto-resume — physical state is unknown after comms gap.
                    # Push IDLE but set execution_blocked; operator must manually reset.
                    await shared.transition(
                        State.IDLE,
                        trigger="MQTT_RECONNECT",
                        direction="restore",
                        detail="MQTT reconnected — manual reset required to resume execution",
                    )
                    return
            except (OSError, Exception) as e:
                # Catching OSError explicitly deals with WinError 10061 (Connection Refused)
                # which can sometimes bubble up through the socket library.
                print(f"{Fore.RED}[MQTT] Reconnect attempt {attempt} failed: {e}{Style.RESET_ALL}")

        # All 3 attempts failed
        print(f"{Fore.RED}[MQTT] MQTT_UNREACHABLE — all reconnect attempts exhausted.{Style.RESET_ALL}")
        print(f"{Fore.RED}[MQTT] System remains in ERROR state. Human intervention required.{Style.RESET_ALL}")
        await log_connectivity_event("MQTT_UNREACHABLE", "All 3 reconnect attempts failed")

    # ── MESSAGE CALLBACK ──
    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as e:
            return  # Malformed JSON — drop silently, do not crash

        topic = msg.topic
        print(f"{Fore.CYAN}[RAW_RECV] {topic}: {payload}{Style.RESET_ALL}")
        self._schedule(self._route_message(topic, payload))

    async def _route_message(self, topic: str, payload: dict) -> None:
        """Route verified telemetry to the appropriate handler."""

        if topic == TOPIC_TELEM_HEARTBEAT:
            await self._handle_heartbeat(payload)

        elif topic == TOPIC_TELEM_POWER:
            await self._handle_power(payload)

        elif topic == TOPIC_TELEM_ACK:
            await self._handle_ack(payload)

        elif topic == TOPIC_TELEM_BOOT:
            await self._handle_boot(payload)

        elif topic == TOPIC_TELEM_SECURITY:
            await self._handle_security_report(payload)

    # ── HEARTBEAT ──
    async def _handle_heartbeat(self, payload: dict) -> None:
        valid, reason = crypto.verify_telemetry(payload, "heartbeat")
        if valid:
            now = time.time()
            shared.last_heartbeat_time = now
            # Update per-node heartbeat (ESP32 heartbeat payload contains 'pump' field)
            pump = payload.get("pump", "unknown")
            if pump in shared.node_heartbeats:
                shared.node_heartbeats[pump] = now
            # Update live latency estimate for UI status bar
            ts = payload.get("ts", 0)
            latency_ms = int((time.time() - ts) * 1000)
            await shared.push({"channel": "latency_update", "ms": max(1, latency_ms)})
        else:
            await self._record_security_event("HMAC_FAIL", TOPIC_TELEM_HEARTBEAT, reason)


    # ── POWER TELEMETRY ──
    async def _handle_power(self, payload: dict) -> None:
        valid, reason = crypto.verify_telemetry(payload, "power")
        if not valid:
            await self._handle_drift_or_fail("power", reason)
            return

        ma = float(payload.get("ma", 0))
        pump = payload.get("pump", "unknown")

        # Sanity bounds (Section A: reject < 0 or > 600mA as sensor error)
        if ma < 0 or ma > 600:
            shared.consecutive_sensor_errors += 1
            if shared.consecutive_sensor_errors >= 3:
                await shared.transition(
                    State.ERROR,
                    trigger="SENSOR_ERROR_POWER",
                    detail=f"INA219 reading {ma}mA outside 0–600mA sanity bounds (3 consecutive)",
                )
            return

        shared.consecutive_sensor_errors = 0
        
        # Update per-node heartbeat (power data is proof of life)
        shared.node_heartbeats[pump] = time.time()

        # Aggregate across nodes
        if not hasattr(shared, "power_by_node"):
            shared.power_by_node = {}
        shared.power_by_node[pump] = ma
        total_ma = sum(shared.power_by_node.values())

        shared.last_power = {"ma": total_ma, "ts": int(time.time())}

        if total_ma > crypto.peak_ma:
            crypto.peak_ma = total_ma

        await shared.push({
            "channel": "telemetry_power",
            "ma": total_ma,
            "ts": int(time.time()),
        })


    # ── ACK ──
    async def _handle_ack(self, payload: dict) -> None:
        valid, reason = crypto.verify_telemetry(payload, "ack")
        if not valid:
            await self._record_security_event("HMAC_FAIL", TOPIC_TELEM_ACK, reason)
            return

        packet_id = payload.get("packet_id", "")
        pump      = payload.get("pump", "")
        status    = payload.get("status", "")

        # Idempotency: duplicate ACK is discarded silently
        if packet_id in shared.acknowledged_packet_ids:
            return

        # Match against pending ACK
        if packet_id == shared.pending_ack_packet_id:
            shared.last_ack_payload = payload
            shared.ack_event.set()
            shared.acknowledged_packet_ids.add(packet_id)
            await log_ack(packet_id)

            await shared.push({
                "channel": "ack_event",
                "packet_id": packet_id,
                "pump": pump,
                "status": status,
            })

    # ── BOOT EVENT ──
    async def _handle_boot(self, payload: dict) -> None:
        valid, reason = crypto.verify_telemetry(payload, "boot")
        reset_reason = payload.get("reset_reason", "UNKNOWN")

        if not valid:
            print(f"{Fore.YELLOW}[MQTT] Boot event received but HMAC invalid: {reason}{Style.RESET_ALL}")
            # Still process brownout even on failed verify — brownout may have
            # corrupted the signing state on ESP32
            pass

        print(f"{Fore.CYAN}[ESP32] Boot event: reset_reason={reset_reason} ip={payload.get('ip','?')}{Style.RESET_ALL}")

        # BROWNOUT: immediately push ERROR (Section A)
        if "BROWNOUT" in reset_reason.upper() or "MQTT_RECONNECT" not in reset_reason:
            if "BROWNOUT" in reset_reason.upper():
                await shared.transition(
                    State.ERROR,
                    trigger="BROWNOUT",
                    detail=f"ESP32 reported brownout reset. Check 5V power rail.",
                )

    # ── ESP32 SECURITY REPORTS ──
    async def _handle_security_report(self, payload: dict) -> None:
        """
        Handle security events reported by the ESP32 (e.g., TIME_DRIFT,
        CLOCK_FAULT, RATE_LIMIT_HIT). These come from Core 0 security watchdog.
        """
        event = payload.get("event", "UNKNOWN")
        consecutive = payload.get("consecutive", 0)

        if event == "TIME_DRIFT":
            delta = payload.get("delta", 0)
            await shared.push_connectivity_event(
                "DRIFT_ESCALATION" if consecutive >= 3 else "TIME_DRIFT",
                f"ESP32 drift={delta}s consecutive={consecutive}"
            )
            if consecutive >= 3:
                await log_connectivity_event("CLOCK_FAULT", f"ESP32 clock drift {delta}s x3")

        elif event == "CLOCK_FAULT":
            await shared.push_connectivity_event("CLOCK_FAULT", payload.get("detail", ""))
            # Re-send clock sync to help ESP32 recover
            sync = crypto.sign_sync()
            self.publish(TOPIC_CMD_SYNC, sync)

        elif event == "RATE_LIMIT_HIT":
            await self._record_security_event("RATE_LIMIT_HIT", TOPIC_CMD_PUMP, "")

        elif event in ("HMAC_FAIL", "REPLAY", "NONCE_FAIL"):
            await self._record_security_event(event, TOPIC_CMD_PUMP, "")

    # ── HELPERS ──

    async def _handle_drift_or_fail(self, telem_type: str, reason: str) -> None:
        """
        Route a failed verification to the correct handler.
        Time drift failures are tracked separately from HMAC failures.
        """
        if "Time drift" in reason:
            # Section D: escalate after 3 consecutive drift violations
            if crypto.consecutive_drift_count >= 3:
                await shared.transition(
                    State.ERROR,
                    trigger="PERSISTENT_TIME_DRIFT",
                    detail="PERSISTENT TIME DRIFT — ESP32 clock desynchronized. NTP re-sync required.",
                )
                await shared.push_connectivity_event(
                    "DRIFT_ESCALATION",
                    f"3 consecutive packets outside 5s window on {telem_type}"
                )
                await log_connectivity_event(
                    "DRIFT_ESCALATION",
                    f"Consecutive drift count hit 3 on {telem_type}"
                )
                # Attempt clock re-sync (Section D)
                sync = crypto.sign_sync()
                self.publish(TOPIC_CMD_SYNC, sync)
        else:
            await self._record_security_event("HMAC_FAIL", f"refinery/telemetry/{telem_type}", reason)

    async def _record_security_event(
        self, event_type: str, topic: str, reason: str
    ) -> None:
        """
        Record a security event, push to UI, and check BREACH threshold.
        intrusion_count is tracked in CryptoTransport (canonical source).
        """
        hash_preview = reason.split("preview=")[-1] if "preview=" in reason else reason[:20]

        await shared.push_security_event(event_type, topic, hash_preview)
        await log_security_event(
            event_type, topic, hash_preview, None, crypto.intrusion_count
        )

        if crypto.intrusion_count > 3:
            await shared.transition(
                State.BREACH,
                trigger=f"BREACH_ESCALATION after {crypto.intrusion_count} intrusions",
                direction="none",
                detail=f"Intrusion count exceeded threshold: {crypto.intrusion_count}",
                focus="RIGHT",
            )
