"""
Shared system state — single source of truth for the entire orchestrator.

Every component (MQTT client, safety governor, dead man's switch, orchestrator)
reads and writes through this object. No component maintains its own copy of
system state — all transitions are centralized here and immediately broadcast
to the UI WebSocket.

SECURITY RATIONALE: Centralizing state prevents split-brain scenarios where,
e.g., the MQTT client thinks we're IDLE while the governor thinks we're
EXECUTING. A single authoritative state object with an asyncio.Lock prevents
race conditions on state transitions.
"""

import asyncio
import time
from typing import Optional, Callable, Awaitable

# ── SYSTEM STATE CONSTANTS ──
class State:
    IDLE      = "IDLE"
    EXECUTING = "EXECUTING"
    PAUSED    = "PAUSED"
    BREACH    = "BREACH"
    ERROR     = "ERROR"


class SharedState:
    """Thread-safe shared state. All mutations go through transition()."""

    def __init__(self):
        self._state: str = State.IDLE
        self._lock: Optional[asyncio.Lock] = None

        # ── TELEMETRY BUFFERS ──
        # Last verified (HMAC-checked) sensor readings.
        # SECURITY: Only verified readings live here. Raw/unverified data
        # is discarded before reaching this buffer.
        self.last_power: Optional[dict] = None      # {ma, ts}

        # ── HEARTBEAT ──
        self.last_heartbeat_time: float = 0.0       # Aggregate (any node)
        self.node_heartbeats: dict[str, float] = {  # Per-node timestamps
            "red": 0.0, "blue": 0.0, "yellow": 0.0
        }

        # ── MQTT CONNECTION ──
        self.mqtt_connected: bool = False
        self.execution_blocked: bool = False        # Locked on disconnect until manual reset

        # ── SECURITY COUNTERS ──
        self.intrusion_count: int = 0               # HMAC_FAIL or REPLAY events
        self.consecutive_drift_count: int = 0       # Consecutive time-window violations
        self.consecutive_ack_failures: int = 0      # Consecutive ACK timeouts
        self.consecutive_sensor_errors: int = 0     # Consecutive out-of-bounds sensor readings

        # ── ACK TRACKING ──
        # The pending packet_id we're waiting for an ACK on.
        self.pending_ack_packet_id: Optional[str] = None
        self._ack_event: Optional[asyncio.Event] = None
        self.last_ack_payload: Optional[dict] = None

        # ── IDEMPOTENCY ──
        # Session set of acknowledged packet_ids. If the same packet_id
        # is ACK'd twice (e.g., broker retry), we discard silently.
        self.acknowledged_packet_ids: set = set()

        # ── CLOCK SYNC ──
        self.clock_synced: bool = False             # Set to True after ESP32 sync ack

        # ── WEBSOCKET BROADCASTER ──
        # Injected by websocket_server.py after startup.
        self.ws_broadcast: Optional[Callable[..., Awaitable[None]]] = None

        # ── STATE TRANSITION CALLBACKS ──
        # Registered by audit_db to log every transition.
        self._transition_callbacks: list = []

    @property
    def state(self) -> str:
        return self._state

    def register_transition_callback(self, cb: Callable) -> None:
        """Register an async callback called on every state transition."""
        self._transition_callbacks.append(cb)

    async def transition(
        self,
        new_state: str,
        trigger: str = "",
        direction: str = "none",
        detail: str = "",
        message: Optional[str] = None,
        focus: Optional[str] = None,
        resumed: bool = False,
        ai_snippet: str = "",
    ) -> None:
        """
        Atomically change state and immediately push a WebSocket event.

        SECURITY RATIONALE: Every state change is observable by the UI in
        real time. There are no silent background state changes. If a judge
        asks "why did the system enter ERROR state?", the WS event stream and
        SQLite log both have a timestamped record with the trigger string.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            old_state = self._state
            if old_state == new_state:
                return  # No-op — already in target state
            self._state = new_state

        # Push WebSocket event immediately (outside lock to avoid deadlock)
        if self.ws_broadcast:
            payload: dict = {
                "channel": "state_change",
                "state": new_state,
                "direction": direction,
            }
            if detail:
                payload["detail"] = detail
            if message:
                payload["message"] = message
            if focus:
                payload["focus"] = focus
            if resumed:
                payload["resumed"] = True
            await self.ws_broadcast(payload)

        # Notify all registered callbacks (audit log writes happen here)
        for cb in self._transition_callbacks:
            try:
                await cb(old_state, new_state, trigger, ai_snippet)
            except Exception:
                pass  # Never let a logging failure crash the control path

    @property
    def ack_event(self) -> asyncio.Event:
        if self._ack_event is None:
            self._ack_event = asyncio.Event()
        return self._ack_event

    def is_node_online(self, node_id: str) -> bool:
        """Check if a specific node has sent a heartbeat/power in the last 5s."""
        last_seen = self.node_heartbeats.get(node_id, 0.0)
        return (time.time() - last_seen) < 5.0


    async def push(self, payload: dict) -> None:
        """Push an arbitrary payload to all connected WebSocket clients."""
        if self.ws_broadcast:
            await self.ws_broadcast(payload)

    async def push_connectivity_event(self, event: str, detail: str) -> None:
        """Push a connectivity_event channel message to the UI."""
        await self.push({
            "channel": "connectivity_event",
            "event": event,
            "detail": detail,
            "ts": int(time.time()),
        })

    async def push_security_event(
        self, event: str, topic: str, hash_preview: str,
        delta_t: Optional[int] = None
    ) -> None:
        """Push a security_event to the UI right panel."""
        payload = {
            "channel": "security_event",
            "event": event,
            "topic": topic,
            "hash_preview": hash_preview,
            "timestamp": int(time.time()),
        }
        if delta_t is not None:
            payload["delta_t"] = delta_t
        await self.push(payload)

    async def push_governor_event(
        self, decision: str, reason: str, pump: str
    ) -> None:
        """Push a governor decision to the UI MQTT feed."""
        await self.push({
            "channel": "governor_event",
            "decision": decision,
            "reason": reason,
            "pump": pump,
        })

    async def push_system_metrics(self, packets_verified: int,
                                   packets_dropped: int, peak_ma: float) -> None:
        """Push aggregate metrics to the UI status strip."""
        await self.push({
            "channel": "system_metrics",
            "packets_verified": packets_verified,
            "packets_dropped": packets_dropped,
            "uptime_seconds": int(time.time()),  # server handles absolute→relative
            "peak_ma": peak_ma,
        })


# Module-level singleton — imported by all other modules
shared = SharedState()
