"""
Module 7: SQLite Audit Log & Crash Recovery

WAL (Write-Ahead Log) mode for concurrent reads during writes.
Every command, security event, state transition, AI reasoning step,
and connectivity event is persisted here.

SECURITY RATIONALE: The audit log is the post-mortem record. If a pump
fires unexpectedly, the audit log tells us which plan_id authorized it,
what the governor decided, whether the ACK came back, and what state the
system was in at execution time. Without this, a security incident has
no forensic trail.

CRASH RECOVERY: On startup, we read the last state_transitions row. If
the process crashed while EXECUTING or PAUSED, physical pump state is
unknown — a pump could be stuck on. We immediately transition to ERROR
and require manual reset rather than attempting to auto-resume.
"""

import aiosqlite
import asyncio
import time
from typing import Optional

DB_PATH = "refinery_audit.db"


async def init_db() -> None:
    """
    Initialize all tables. Safe to call on every startup — uses
    CREATE TABLE IF NOT EXISTS so re-runs are idempotent.
    WAL mode is set first so all subsequent writes use it.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL mode: readers don't block writers, writers don't block readers.
        # Critical for real-time telemetry writes while the UI reads metrics.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")  # WAL + NORMAL is safe and fast

        # ── TABLE: commands ──
        # Every actuate_pump() call, approved or rejected.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS commands (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                packet_id           TEXT    NOT NULL,
                pump                TEXT    NOT NULL,
                duration_ms         INTEGER NOT NULL,
                ts                  INTEGER NOT NULL,
                state_at_execution  TEXT,
                governor_decision   TEXT,
                governor_reason     TEXT,
                ack_received        INTEGER DEFAULT 0,
                ack_ts              INTEGER,
                attempt_number      INTEGER DEFAULT 1
            )
        """)

        # ── TABLE: security_events ──
        # HMAC_FAIL, REPLAY, BREACH_ESCALATION, RATE_LIMIT_HIT.
        # sig field is deliberately NOT stored (see security rationale).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS security_events (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type              TEXT    NOT NULL,
                topic                   TEXT,
                hash_preview            TEXT,
                delta_t                 INTEGER,
                ts                      INTEGER NOT NULL,
                intrusion_count_at_event INTEGER
            )
        """)

        # ── TABLE: state_transitions ──
        # Every IDLE→EXECUTING, ANY→ERROR, etc.
        # ai_reasoning_snippet: last AI log line at time of transition.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS state_transitions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                from_state          TEXT    NOT NULL,
                to_state            TEXT    NOT NULL,
                trigger             TEXT,
                ts                  INTEGER NOT NULL,
                ai_reasoning_snippet TEXT
            )
        """)

        # ── TABLE: ai_reasoning ──
        # Every LangChain reasoning step and tool call.
        # Grouped by plan_id (UUID4 per LLM invocation).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_reasoning (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT,
                plan_id     TEXT,
                step_index  INTEGER,
                step_label  TEXT,
                text        TEXT,
                tool_call   TEXT,
                ts          INTEGER NOT NULL
            )
        """)

        # ── TABLE: connectivity_events ──
        # MQTT disconnects, reconnects, drift escalations, clock faults.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS connectivity_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT    NOT NULL,
                detail      TEXT,
                ts          INTEGER NOT NULL
            )
        """)

        await db.commit()


async def check_crash_recovery() -> Optional[str]:
    """
    Read the last persisted state on startup.

    Returns the last state string if it was EXECUTING or PAUSED,
    indicating a crash occurred during active operation.

    SECURITY RATIONALE: Auto-resuming after a crash is unsafe. If the
    process crashed mid-execution, a pump may be physically stuck ON.
    Requiring manual reset forces the operator to physically inspect
    the hardware before resuming. This prevents runaway over-dispense.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT to_state FROM state_transitions ORDER BY id DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0] in ("EXECUTING", "PAUSED"):
                    return row[0]
    except Exception:
        pass  # DB doesn't exist yet on first run — that's fine
    return None


# ── WRITE HELPERS ──

async def log_command(
    packet_id: str, pump: str, duration_ms: int,
    state: str, governor_decision: str, governor_reason: str,
    attempt: int = 1
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO commands
               (packet_id, pump, duration_ms, ts, state_at_execution,
                governor_decision, governor_reason, attempt_number)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (packet_id, pump, duration_ms, int(time.time()),
             state, governor_decision, governor_reason, attempt)
        )
        await db.commit()


async def log_ack(packet_id: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE commands SET ack_received=1, ack_ts=? WHERE packet_id=?",
            (int(time.time()), packet_id)
        )
        await db.commit()


async def log_security_event(
    event_type: str, topic: str, hash_preview: str,
    delta_t: Optional[int], intrusion_count: int
) -> None:
    """
    Log a security event. sig field is NEVER stored — only a truncated
    hash_preview is kept. Storing full signatures could assist an attacker
    in building a signature corpus for analysis.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO security_events
               (event_type, topic, hash_preview, delta_t, ts, intrusion_count_at_event)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event_type, topic, hash_preview, delta_t,
             int(time.time()), intrusion_count)
        )
        await db.commit()


async def log_state_transition(
    from_state: str, to_state: str, trigger: str, snippet: str = ""
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO state_transitions
               (from_state, to_state, trigger, ts, ai_reasoning_snippet)
               VALUES (?, ?, ?, ?, ?)""",
            (from_state, to_state, trigger, int(time.time()), snippet)
        )
        await db.commit()


async def log_ai_reasoning(
    session_id: str, plan_id: str, step_index: int,
    step_label: str, text: str, tool_call: str
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO ai_reasoning
               (session_id, plan_id, step_index, step_label, text, tool_call, ts)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (session_id, plan_id, step_index, step_label,
             text, tool_call, int(time.time()))
        )
        await db.commit()


async def log_connectivity_event(event_type: str, detail: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO connectivity_events (event_type, detail, ts) VALUES (?, ?, ?)",
            (event_type, detail, int(time.time()))
        )
        await db.commit()


# ── TRANSITION CALLBACK ──
# Registered with SharedState so every state.transition() auto-logs.

async def on_state_transition(
    from_state: str, to_state: str, trigger: str, snippet: str = ""
) -> None:
    """Auto-called by SharedState.transition() for every state change."""
    await log_state_transition(from_state, to_state, trigger, snippet)
