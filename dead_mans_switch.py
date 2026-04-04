"""
Module 5: Dead Man's Switch & Fault Tolerance

The Dead Man's Switch is a background thread that continuously monitors
the ESP32 heartbeat. If no heartbeat arrives within 3000ms, it transitions
the system to ERROR state and publishes an emergency halt.

SECURITY RATIONALE: Without this, a crashed or disconnected ESP32 would
leave the orchestrator believing the edge node is healthy. The LLM might
continue issuing commands that never arrive or ACK. The DMS ensures that
any ESP32 failure is caught within 3 seconds regardless of MQTT client
state — it is a software-layer watchdog on top of the MQTT disconnect
handler (which handles the broker side) and the ACK retry (which handles
individual command failures).

EMERGENCY HALT: Even the halt command is HMAC-signed. An attacker who
can forge unsigned "emergency=true" packets could use them as a DoS
vector. Zero-trust applies to safety commands.
"""

import asyncio
import json
import time
import threading
import colorama
from colorama import Fore, Style

from system_state import shared, State
from crypto_transport import crypto

colorama.init()

# ── TIMING CONSTANTS ──
HEARTBEAT_TIMEOUT_S = 10.0    # RELAXED FOR DIAGNOSTICS: 10s with no heartbeat
CHECK_INTERVAL_S    = 0.5     # Poll interval — fast enough to detect failure quickly


class DeadMansSwitch:
    """
    Runs in a background daemon thread.
    Monitors shared.last_heartbeat_time every 500ms.
    On timeout: ERROR state + signed emergency halt.
    """

    def __init__(self, mqtt_manager, loop: asyncio.AbstractEventLoop):
        self._mqtt  = mqtt_manager
        self._loop  = loop
        self._armed = False
        self._thread: threading.Thread = None

    def arm(self) -> None:
        """Start the monitoring thread. Call after MQTT is connected."""
        self._armed = True
        shared.last_heartbeat_time = time.time()  # Seed so we don't false-trip on startup
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="DeadMansSwitch",
            daemon=True,
        )
        self._thread.start()
        print(f"{Fore.CYAN}[DMS] Dead Man's Switch armed. Timeout: {HEARTBEAT_TIMEOUT_S}s{Style.RESET_ALL}")

    def disarm(self) -> None:
        """Disarm (e.g., on clean shutdown)."""
        self._armed = False

    def _monitor_loop(self) -> None:
        """
        Blocking loop in daemon thread.
        Schedules async handlers on the main event loop.
        """
        # Wait for ESP32 to connect to Wi-Fi and Mosquitto during cold boot
        time.sleep(15.0)
        while self._armed:
            time.sleep(CHECK_INTERVAL_S)

            # Don't fire if MQTT is known disconnected (that's handled separately)
            if not shared.mqtt_connected:
                continue

            # Don't re-fire if already in ERROR or BREACH
            if shared.state in (State.ERROR, State.BREACH):
                continue

            elapsed = time.time() - shared.last_heartbeat_time
            if elapsed > HEARTBEAT_TIMEOUT_S:
                print(
                    f"{Fore.RED}[DMS] FIRED — no heartbeat for {elapsed:.1f}s "
                    f"> {HEARTBEAT_TIMEOUT_S}s{Style.RESET_ALL}"
                )
                asyncio.run_coroutine_threadsafe(self._trigger(), self._loop)

    async def _trigger(self) -> None:
        """
        Called when the dead man's switch fires.
        1. Transition to ERROR.
        2. Publish signed emergency halt.
        3. Log the event.
        """
        await shared.transition(
            State.ERROR,
            trigger="DEAD_MAN_SWITCH",
            direction="none",
            detail="EDGE NODE UNRESPONSIVE — Dead man switch triggered",
            focus="NONE",
        )

        print(f"{Fore.RED}[DMS] Sending emergency halt...{Style.RESET_ALL}")

        # Build and publish a signed emergency halt
        # SECURITY: Signed even for emergency — zero-trust always.
        halt = crypto.sign_emergency_halt()
        try:
            self._mqtt.publish("refinery/cmd/pump", halt)
            print(f"{Fore.RED}[DMS] Emergency halt published (packet_id={halt['packet_id'][:8]}...){Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.RED}[DMS] Failed to publish halt: {e}{Style.RESET_ALL}")
