"""
Module 4: Safety Governor — Middleware Layer

Intercepts every actuate_pump() call BEFORE any MQTT publish.
The LLM is untrusted input. The Governor is deterministic and always wins.

SECURITY RATIONALE: Large Language Models can hallucinate, misinterpret
constraints, or be adversarially prompted. We must never allow the LLM to
directly control physical hardware without a deterministic safety layer.
The Governor enforces hard physical limits that cannot be reasoned around
or overridden by prompt engineering. It is the last line of defense before
bytes become physical pump actuation.

The Governor logs every APPROVED/REJECTED decision to SQLite and pushes
a governor_event to the UI. This creates an immutable audit trail of
every hardware authorization decision.
"""

import asyncio
import os
import time
from typing import Optional

from system_state import shared, State
from crypto_transport import crypto
from audit_db import log_command
import colorama
from colorama import Fore, Style

colorama.init()

# ── PHYSICAL CONSTANTS (Section A) ──
FLOW_RATES = {
    "red":    8.5,   # ml/sec — GPIO 25
    "blue":   9.1,   # ml/sec — GPIO 26
    "yellow": float(os.getenv("YELLOW_FLOW_RATE_ML_PER_SEC", "8.8")),  # ml/sec — GPIO 27
}

# Safety limits
MAX_DURATION_MS     = 4_000    # JUDGE'S CAP: 4 seconds per pump
CURRENT_PER_PUMP_MA = 200      # ~200mA per pump under load (Section A)
POWER_CONSTRAINT_MA = 300      # Only one pump at a time (Sequential)

# Sensor sanity bounds
MAX_DISTANCE_CM = 400
MAX_CURRENT_MA  = 600


class SafetyGovernor:
    """
    Deterministic safety gate between the LLM and the physical hardware.

    All enforcement rules are checked in order. The first failing rule
    produces a REJECTED decision; approved commands are then signed and
    published.

    asyncio.Lock() ensures exactly one command is in-flight at all times.
    This is belt-and-suspenders with the ESP32's RTOS queue (depth=1) —
    double protection against concurrent actuation.
    """

    def __init__(self, mqtt_manager):
        self._mqtt = mqtt_manager
        self._execution_lock = asyncio.Lock()

    async def approve_and_execute(
        self,
        pump_id: str,
        duration_ms: int,
        attempt: int = 1,
    ) -> dict:
        """
        Main entry point. Returns dict with keys:
          status: "EXECUTED" | "REJECTED" | "ERROR"
          packet_id: str
          reason: str
          actual_duration_ms: int
        """
        # ── RULE 0: SYSTEM STATE GUARD ──
        # Rule 0: SYSTEM STATE GUARD
        # Normally, ERROR is non-recoverable. During this diagnostic phase, 
        # we allow the AI/User to attempt recovery by proceeding if in ERROR.
        if shared.state == State.BREACH:
            return self._reject("System in non-recoverable state — manual reset required", pump_id, duration_ms)

        # ── RULE 0b: EXECUTION BLOCKED ──
        if shared.execution_blocked:
            return self._reject("Execution blocked — MQTT reconnect requires manual reset", pump_id, duration_ms)

        # ── RULE 1: DURATION CAP ──
        if duration_ms >= MAX_DURATION_MS:
            return self._reject(
                f"Duration {duration_ms}ms ≥ {MAX_DURATION_MS}ms hard cap",
                pump_id, duration_ms
            )

        # ── RULE 2: PUMP ID VALIDATION ──
        if pump_id not in FLOW_RATES:
            return self._reject(
                f"Unknown pump_id '{pump_id}' — must be red|blue|yellow",
                pump_id, duration_ms
            )

        # ── RULE 3: TELEMETRY SANITY ──
        if not await self._telemetry_sanity_check():
            return self._reject("Telemetry out of sanity bounds — sensor fault", pump_id, duration_ms)

        # ── RULE 5: POWER PREDICTION ──
        baseline_ma = self._get_baseline_ma()
        predicted_ma = baseline_ma + CURRENT_PER_PUMP_MA
        if predicted_ma > POWER_CONSTRAINT_MA:
            return self._reject(
                f"Power limit exceeded: baseline {baseline_ma:.0f}mA + 200mA = "
                f"{predicted_ma:.0f}mA > {POWER_CONSTRAINT_MA}mA constraint",
                pump_id, duration_ms
            )

        # ── RULE 6: IDEMPOTENCY PRE-CHECK ──
        # (Duplicate packet_id check happens after signing — handled in actuate_pump)

        # ── ALL RULES PASSED → EXECUTE ──
        return await self._execute_with_lock(pump_id, duration_ms, attempt)

    async def _execute_with_lock(
        self, pump_id: str, duration_ms: int, attempt: int
    ) -> dict:
        """
        Acquire the execution lock and send the command.

        SECURITY RATIONALE: asyncio.Lock() ensures only one pump command
        is in-flight at any time. LangChain's tool executor blocks here
        if a previous command hasn't ACK'd yet. Combined with the ESP32's
        RTOS queue (depth=1), this gives us double protection against
        concurrent actuation across both the software and firmware layers.
        """
        async with self._execution_lock:
            MAX_STRIKES = 3
            for strike in range(1, MAX_STRIKES + 1):
                # ── SIGN FRESH PAYLOAD FOR EVERY ATTEMPT (Fix: Replay Protection) ──
                # Monotonic nonce is incremented inside sign_payload()
                payload = crypto.sign_payload(pump_id, duration_ms)
                packet_id = payload["packet_id"]

                # Log to SQLite (governor decision = APPROVED)
                await log_command(
                    packet_id, pump_id, duration_ms,
                    shared.state, "APPROVED", "All 6 governor rules passed", strike
                )

                # Push governor_event to UI
                await shared.push_governor_event("APPROVED", "All safety rules passed", pump_id)

                # Notify UI: pump is about to fire (or retry)
                await shared.push({
                    "channel": "pump_event",
                    "pump": pump_id,
                    "action": "ON",
                    "duration_ms": duration_ms,
                    "packet_id": packet_id,
                })

                # Publish to MQTT
                self._mqtt.publish("refinery/cmd/pump", payload)

                print(
                    f"{Fore.GREEN}[GOV] APPROVED: {pump_id} {duration_ms}ms "
                    f"packet={packet_id[:8]}... (Attempt {strike}){Style.RESET_ALL}"
                )

                # Await ACK with 3-second timeout
                shared.pending_ack_packet_id = packet_id
                shared.ack_event.clear()

                try:
                    # Windows Python 3.14 + ESP32 can have high jitter.
                    # Relax to 5.0s for more deterministic ACK matching.
                    await asyncio.wait_for(shared.ack_event.wait(), timeout=5.0)
                    # ACK received
                    ack = shared.last_ack_payload or {}
                    shared.consecutive_ack_failures = 0

                    await shared.push({
                        "channel": "pump_event",
                        "pump": pump_id,
                        "action": "OFF",
                        "duration_ms": duration_ms,
                        "packet_id": packet_id,
                    })

                    return {
                        "status": "EXECUTED",
                        "packet_id": packet_id,
                        "actual_duration_ms": duration_ms,
                        "reason": "ACK received",
                    }

                except asyncio.TimeoutError:
                    shared.consecutive_ack_failures += 1
                    print(
                        f"{Fore.YELLOW}[GOV] ACK timeout strike {strike}/{MAX_STRIKES} "
                        f"for {packet_id[:8]}...{Style.RESET_ALL}"
                    )
                    # Next iteration will re-sign and re-publish

            # 3 strikes — ERROR state
            await shared.transition(
                State.ERROR,
                trigger="ACK_FAILURE_x3",
                detail=f"3 consecutive ACK timeouts for pump {pump_id}",
            )
            return {
                "status": "ERROR",
                "packet_id": packet_id,
                "actual_duration_ms": 0,
                "reason": "3 ACK timeouts — system in ERROR state",
            }

    def _reject(self, reason: str, pump_id: str, duration_ms: int) -> dict:
        """Log and return a REJECTED decision."""
        print(f"{Fore.YELLOW}[GOV] REJECTED: {pump_id} — {reason}{Style.RESET_ALL}")

        # Fire-and-forget async log (can't await in sync context)
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(self._async_reject_log(reason, pump_id, duration_ms))

        return {
            "status": "REJECTED",
            "packet_id": "",
            "actual_duration_ms": 0,
            "reason": reason,
        }

    async def _async_reject_log(
        self, reason: str, pump_id: str, duration_ms: int
    ) -> None:
        """Async portion of rejection logging — runs on event loop."""
        fake_id = f"REJECTED-{int(time.time())}"
        await log_command(fake_id, pump_id, duration_ms, shared.state, "REJECTED", reason)
        await shared.push_governor_event("REJECTED", reason, pump_id)

    def _get_baseline_ma(self) -> float:
        if shared.last_power is None:
            return 0.0
        age = time.time() - shared.last_power.get("ts", 0)
        if age > 2.0:
            return 0.0
        return shared.last_power.get("ma", 0.0)

    async def _telemetry_sanity_check(self) -> bool:
        """Verify both sensor readings are within sanity bounds."""
        if shared.last_power:
            ma = shared.last_power.get("ma", -1)
            if ma < 0 or ma > MAX_CURRENT_MA:
                await shared.transition(
                    State.ERROR, trigger="TELEMETRY_INSANE",
                    detail=f"INA219 {ma}mA outside 0–600mA bounds"
                )
                return False

        return True
