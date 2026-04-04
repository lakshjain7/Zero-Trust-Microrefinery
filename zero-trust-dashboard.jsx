import { useState, useEffect, useRef, useCallback } from "react";

// ── STATE CONSTANTS ──
const STATES = {
  IDLE: "IDLE", EXECUTING: "EXECUTING", PAUSED: "PAUSED",
  BREACH: "BREACH DETECTED", ERROR: "ERROR"
};
const STATE_COLORS = {
  [STATES.IDLE]: "#6b7280", [STATES.EXECUTING]: "#00FF9D",
  [STATES.PAUSED]: "#FFD166", [STATES.BREACH]: "#FF2A2A", [STATES.ERROR]: "#FF6B35"
};

// ── ODOMETER DIGIT ──
const OdometerDigit = ({ value, color = "#45F3FF" }) => {
  const digits = String(value).split("");
  return (
    <span style={{ display: "inline-flex", fontFamily: "'JetBrains Mono', monospace", color, fontWeight: 700 }}>
      {digits.map((d, i) => (
        <span key={i} style={{ display: "inline-block", overflow: "hidden", height: "1.2em", position: "relative", width: "0.65em" }}>
          <span style={{ display: "block", transition: "transform 0.5s cubic-bezier(0.25,0.1,0.25,1)", transform: `translateY(-${parseInt(d) * 1.2}em)` }}>
            {[0,1,2,3,4,5,6,7,8,9].map(n => <span key={n} style={{ display: "block", height: "1.2em", lineHeight: "1.2em" }}>{n}</span>)}
          </span>
        </span>
      ))}
    </span>
  );
};

// ── PULSE DOT ──
const PulseDot = ({ color, size = 8 }) => (
  <span style={{ display: "inline-block", width: size, height: size, borderRadius: "50%", background: color, boxShadow: `0 0 ${size}px ${color}`, animation: "pulse 2s ease-in-out infinite" }} />
);

// ── GLASS PANEL ──
const GlassPanel = ({ children, style, accent, focused, dimmed }) => (
  <div style={{
    background: "rgba(18,18,26,0.7)", backdropFilter: "blur(14px)", WebkitBackdropFilter: "blur(14px)",
    border: `1px solid ${focused ? accent || "#45F3FF" : "rgba(255,255,255,0.06)"}`,
    borderRadius: 12, padding: 16, position: "relative", overflow: "hidden",
    boxShadow: focused ? `0 0 30px ${accent || "#45F3FF"}33, inset 0 1px 0 rgba(255,255,255,0.05)` : "inset 0 1px 0 rgba(255,255,255,0.04)",
    opacity: dimmed ? 0.55 : 1, filter: dimmed ? "blur(2px)" : "none",
    transition: "all 0.3s cubic-bezier(0.25,0.1,0.25,1)", ...style
  }}>{children}</div>
);

// ── MAIN DASHBOARD ──
export default function Dashboard() {
  // ── CORE STATE ──
  const [systemState, setSystemState] = useState(STATES.IDLE);
  const [mA, setMA] = useState(0);
  const [peakMA, setPeakMA] = useState(0);
  const [distance, setDistance] = useState(12);
  const [packetsVerified, setPacketsVerified] = useState(0);
  const [packetsDropped, setPacketsDropped] = useState(0);
  const [uptime, setUptime] = useState(0);
  const [latency, setLatency] = useState(12);
  const [focusPanel, setFocusPanel] = useState(null);
  const [breachFlash, setBreachFlash] = useState(0);
  const [powerHistory, setPowerHistory] = useState(Array(60).fill(0));
  const [mqttLog, setMqttLog] = useState([]);
  const [aiLog, setAiLog] = useState([]);
  const [activePumps, setActivePumps] = useState({ red: false, blue: false, yellow: false });
  const [cupFill, setCupFill] = useState(0);
  const [cupColor, setCupColor] = useState("#8B5CF6");
  const [inputVal, setInputVal] = useState("");
  const [toolCall, setToolCall] = useState(null);
  const [sweepDir, setSweepDir] = useState(null);
  const [sweepColor, setSweepColor] = useState("#45F3FF");
  const canvasRef = useRef(null);

  // ── MODULE 11 NEW STATE ──
  const [planId, setPlanId] = useState(null);                   // Plan ID badge
  const [mockMode, setMockMode] = useState(false);              // Simulation badge
  const [attackInProgress, setAttackInProgress] = useState(false); // Attack demo button
  const [sensorError, setSensorError] = useState(false);        // Red X on SR04
  const [brokerConnected, setBrokerConnected] = useState(true); // Broker status
  const [brokerReconnecting, setBrokerReconnecting] = useState(false);
  const [brokerFlash, setBrokerFlash] = useState(null);         // "lost" | "restored" | null
  const [ackGlow, setAckGlow] = useState({ red: false, blue: false, yellow: false }); // Mint ACK pulse
  const [driftWarning, setDriftWarning] = useState(null);       // "escalated" | "clock_fault" | null
  const [governorCard, setGovernorCard] = useState(null);        // { reason, pump } | null

  // ── WEBSOCKET ──
  const wsRef = useRef(null);
  const wsTimerRef = useRef(null);

  // Refs to stable callbacks (avoids stale closure in WS handler)
  const addLogRef = useRef(null);
  const addAiRef = useRef(null);
  const triggerSweepRef = useRef(null);

  // ── UPTIME TIMER ──
  useEffect(() => { const t = setInterval(() => setUptime(u => u + 1), 1000); return () => clearInterval(t); }, []);

  // ── POWER SIMULATION (when no WS / mock) ──
  useEffect(() => {
    const t = setInterval(() => {
      if (systemState === STATES.EXECUTING) {
        const base = activePumps.red && activePumps.blue ? 380 : activePumps.red ? 170 : activePumps.blue ? 182 : activePumps.yellow ? 160 : 20;
        const noise = (Math.random() - 0.5) * 40;
        const val = Math.max(0, Math.min(500, base + noise));
        setMA(Math.round(val));
        setPeakMA(p => Math.max(p, Math.round(val)));
        setPowerHistory(h => [...h.slice(1), val]);
        setCupFill(f => Math.min(100, f + 0.8));
      } else if (systemState === STATES.IDLE) {
        const idle = 2 + Math.random() * 3;
        setMA(Math.round(idle));
        setPowerHistory(h => [...h.slice(1), idle]);
      }
    }, 200);
    return () => clearInterval(t);
  }, [systemState, activePumps]);

  // ── SWEEP ANIMATION ──
  const triggerSweep = useCallback((dir, color) => {
    setSweepDir(dir); setSweepColor(color);
    setTimeout(() => setSweepDir(null), 400);
  }, []);

  // ── BREACH FLASH ──
  useEffect(() => {
    if (systemState === STATES.BREACH) {
      setBreachFlash(3);
      [0, 300, 600].forEach((d, i) => setTimeout(() => setBreachFlash(3 - i), d));
      setTimeout(() => setBreachFlash(0), 1200);
    }
  }, [systemState]);

  // ── REACTIVE CUP COLOR — blend from active pump state ──
  // Mirrors physical color mixing: Purple=Red+Blue, Green=Blue+Yellow, Orange=Red+Yellow, Brown=all
  useEffect(() => {
    const { red, blue, yellow } = activePumps;
    if (red && blue && yellow)      setCupColor("#7C4B2A"); // Brown
    else if (red && blue)           setCupColor("#8B5CF6"); // Purple
    else if (blue && yellow)        setCupColor("#00FF9D"); // Green
    else if (red && yellow)         setCupColor("#FF6B35"); // Orange
    else if (red)                   setCupColor("#FF2A2A");
    else if (blue)                  setCupColor("#3B82F6");
    else if (yellow)                setCupColor("#FFD166");
    // When all pumps off, preserve last color so the filled cup remains visible
  }, [activePumps]);

  // ── UNIFIED LOG ENTRY ──
  // type: "verified" | "fail" | "governor_ok" | "governor_reject" | "ack" | "connectivity" | "drift" | "clockfault"
  const addLog = useCallback((entry) => {
    const ts = new Date().toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    setMqttLog(l => [...l.slice(-25), { ts, id: Date.now() + Math.random(), ...entry }]);
    if (entry.type === "verified" || entry.type === "ack") setPacketsVerified(p => p + 1);
    else if (entry.type === "fail" || entry.type === "replay") setPacketsDropped(p => p + 1);
  }, []);

  // Legacy wrapper — keeps demo sequences working without change
  const addMqtt = useCallback((topic, status, hash) => {
    addLog({ type: status === "VERIFIED" ? "verified" : "fail", topic, status, hash });
  }, [addLog]);

  const addAi = useCallback((text, type = "reason") => {
    setAiLog(l => [...l.slice(-15), { text, type, id: Date.now() + Math.random(), active: true }]);
    setTimeout(() => setAiLog(l => l.map((e, i) => i < l.length - 1 ? { ...e, active: false } : e)), 3000);
  }, []);

  // Stable refs for WS handler
  useEffect(() => { addLogRef.current = addLog; }, [addLog]);
  useEffect(() => { addAiRef.current = addAi; }, [addAi]);
  useEffect(() => { triggerSweepRef.current = triggerSweep; }, [triggerSweep]);

  // ── ACK GLOW TRIGGER ──
  const triggerAckGlow = useCallback((pump) => {
    setAckGlow(prev => ({ ...prev, [pump]: true }));
    setTimeout(() => setAckGlow(prev => ({ ...prev, [pump]: false })), 300);
  }, []);

  // ── SEND COMMAND VIA WEBSOCKET ──
  // Dispatches a natural-language command string to the Python orchestrator.
  // The backend websocket_server.py receives channel:"command" and passes
  // it to the LangChain agent for reasoning + physical execution.
  const sendCommand = useCallback((cmd) => {
    const trimmed = cmd.trim();
    if (!trimmed) return;
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      addAi("⚠ WebSocket not connected — cannot dispatch command.", "reason");
      return;
    }
    wsRef.current.send(JSON.stringify({ channel: "command", text: trimmed }));
    addAi(`→ Dispatching: "${trimmed}"`, "reason");
    setInputVal("");
  }, [addAi]);

  // ── WEBSOCKET CONNECTION ──
  useEffect(() => {
    let ws;
    let destroyed = false;

    const connect = () => {
      if (destroyed) return;
      try {
        ws = new WebSocket("ws://localhost:8000/ws");
        wsRef.current = ws;

        ws.onopen = () => {
          setLatency(prev => Math.max(5, prev - 2));
        };

        ws.onmessage = (event) => {
          let msg;
          try { msg = JSON.parse(event.data); } catch { return; }
          const ch = msg.channel;
          const log = addLogRef.current;
          const ai = addAiRef.current;
          const sweep = triggerSweepRef.current;

          if (ch === "telemetry_power") {
            setMA(Math.round(msg.ma));
            setPeakMA(p => Math.max(p, Math.round(msg.ma)));
            setPowerHistory(h => [...h.slice(1), msg.ma]);
          }

          else if (ch === "telemetry_distance") {
            setDistance(Math.round(msg.ema_cm));
            setSensorError(msg.state === "SENSOR_ERROR");
          }

          else if (ch === "state_change") {
            const stateMap = {
              IDLE: STATES.IDLE, EXECUTING: STATES.EXECUTING,
              PAUSED: STATES.PAUSED, BREACH: STATES.BREACH, ERROR: STATES.ERROR
            };
            setSystemState(stateMap[msg.state] || msg.state);
            if (msg.direction === "forward") sweep?.("ltr", "#45F3FF");
            else if (msg.direction === "backward") sweep?.("rtl", "#FFD166");
            else if (msg.direction === "restore") sweep?.("ltr", "#00FF9D");
            if (msg.focus) setFocusPanel(msg.focus === "NONE" ? null : msg.focus.toLowerCase());
            if (msg.message) ai?.(`⚠ ${msg.message}`);
          }

          else if (ch === "reasoning_stream") {
            if (msg.plan_id) setPlanId(msg.plan_id.slice(0, 8));
            if (msg.text) ai?.(msg.text, msg.tool_call ? "tool" : "reason");
            if (msg.tool_call) {
              setToolCall(msg.tool_call);
              if (!msg.active) setTimeout(() => setToolCall(null), 2500);
            }
          }

          else if (ch === "tool_event") {
            const args = msg.args ? Object.entries(msg.args).map(([k,v]) => `${k}=${JSON.stringify(v)}`).join(", ") : "";
            setToolCall(`${msg.tool}(${args})`);
            setTimeout(() => setToolCall(null), 3000);
          }

          else if (ch === "security_event") {
            // REPLAY events: amber row with delta_T info. HMAC_FAIL: red row.
            const isReplay = msg.event === "REPLAY";
            const status = isReplay && msg.delta_t != null
              ? `REPLAY — ΔT: ${Math.round(msg.delta_t / 60)}m out of window`
              : msg.event;
            log?.({ type: isReplay ? "replay" : "fail", topic: msg.topic || "security", status, hash: msg.hash_preview });
          }

          else if (ch === "pump_event") {
            setActivePumps(prev => ({ ...prev, [msg.pump]: msg.action === "ON" }));
            if (msg.action === "ON") {
              log?.({ type: "verified", topic: "cmd/pump", status: "VERIFIED", hash: msg.packet_id?.slice(0, 8) + "…" });
            }
          }

          else if (ch === "ack_event") {
            // Mint green 300ms ACK confirmation pulse on relay icon
            if (msg.pump) triggerAckGlow(msg.pump);
            const ok = msg.status === "EXECUTED";
            log?.({ type: "ack", topic: "telemetry/ack", status: ok ? "ACK_OK" : "ACK_ERR", hash: msg.packet_id?.slice(0, 8) + "…" });
          }

          else if (ch === "governor_event") {
            const rejected = msg.decision === "REJECTED";
            log?.({ type: rejected ? "governor_reject" : "governor_ok", topic: "GOVERNOR", status: `${rejected ? "✗" : "✓"} ${msg.decision}`, hash: msg.reason });
            if (rejected) {
              setGovernorCard({ reason: msg.reason, pump: msg.pump });
              setTimeout(() => setGovernorCard(null), 8000);
            }
          }

          else if (ch === "system_metrics") {
            setPacketsVerified(msg.packets_verified ?? 0);
            setPacketsDropped(msg.packets_dropped ?? 0);
            setUptime(msg.uptime_seconds ?? 0);
            if (msg.peak_ma != null) setPeakMA(p => Math.max(p, msg.peak_ma));
          }

          else if (ch === "connectivity_event") {
            if (msg.event === "MQTT_DISCONNECT") {
              setBrokerConnected(false);
              setBrokerReconnecting(true);
              setBrokerFlash("lost");
              log?.({ type: "connectivity", topic: "BROKER", status: "DISCONNECTED", hash: msg.detail || "" });
            } else if (msg.event === "MQTT_RECONNECT") {
              setBrokerConnected(true);
              setBrokerReconnecting(false);
              setBrokerFlash("restored");
              setTimeout(() => setBrokerFlash(null), 3000);
              log?.({ type: "connectivity", topic: "BROKER", status: "RECONNECTED", hash: msg.detail || "" });
            } else if (msg.event === "DRIFT_ESCALATION") {
              setDriftWarning("escalated");
              log?.({ type: "drift", topic: "TIME DRIFT", status: "✗ ESCALATED", hash: "3 consecutive packets" });
            } else if (msg.event === "CLOCK_FAULT") {
              setDriftWarning("clock_fault");
              log?.({ type: "clockfault", topic: "CLOCK FAULT", status: "✗ FAULT", hash: "Awaiting re-sync" });
            }
          }

          else if (ch === "mock_mode") {
            setMockMode(true);
          }

          // Standalone focus change — e.g. { "channel": "focus", "panel": "LEFT" }
          else if (ch === "focus") {
            setFocusPanel(msg.panel === "NONE" ? null : msg.panel?.toLowerCase());
          }

          // Network latency update from backend ping measurement
          else if (ch === "latency_update") {
            if (msg.latency_ms != null) setLatency(Math.round(msg.latency_ms));
          }
        };

        ws.onclose = () => {
          if (!destroyed) wsTimerRef.current = setTimeout(connect, 2000);
        };
        ws.onerror = () => ws.close();

      } catch {
        if (!destroyed) wsTimerRef.current = setTimeout(connect, 2000);
      }
    };

    connect();
    return () => {
      destroyed = true;
      clearTimeout(wsTimerRef.current);
      ws?.close();
    };
  }, [triggerAckGlow]);

  // ── DEMO: TEST CASE A ── Mix 50ml Purple / ≤300mA
  const runTestA = useCallback(() => {
    setSystemState(STATES.EXECUTING);
    triggerSweep("ltr", "#45F3FF");
    setFocusPanel("left");
    setCupFill(0); setCupColor("#8B5CF6");
    setPlanId("a3f9c12d");
    addAi("Parsing command: 'Mix 50ml Purple / ≤300mA'");
    setTimeout(() => addAi("Purple = Red + Blue. Checking parallel draw..."), 800);
    setTimeout(() => addAi("⚠ Parallel draw: ~400mA > 300mA limit. Constraint violated."), 1600);
    setTimeout(() => addAi("Rewriting to sequential execution plan."), 2200);
    setTimeout(() => {
      setToolCall("actuate_pump('red', 2940ms)");
      setTimeout(() => setToolCall(null), 2500);
    }, 2800);
    setTimeout(() => {
      setFocusPanel("center");
      setActivePumps({ red: true, blue: false, yellow: false });
      addMqtt("cmd/pump", "VERIFIED", "a3f9...c12d");
      addAi("Executing: Red pump ON (2.94s @ 8.5ml/sec = 25ml)");
    }, 3000);
    setTimeout(() => {
      // ACK glow simulation
      triggerAckGlow("red");
      setActivePumps({ red: false, blue: false, yellow: false });
      addLog({ type: "ack", topic: "telemetry/ack", status: "ACK_OK", hash: "a3f9…c12d" });
    }, 5900);
    setTimeout(() => {
      setToolCall("actuate_pump('blue', 2747ms)");
      setTimeout(() => setToolCall(null), 2500);
      setActivePumps({ red: false, blue: true, yellow: false });
      addMqtt("cmd/pump", "VERIFIED", "c1d8...f92b");
      addAi("Executing: Blue pump ON (2.75s @ 9.1ml/sec = 25ml)");
    }, 6200);
    setTimeout(() => {
      triggerAckGlow("blue");
      setActivePumps({ red: false, blue: false, yellow: false });
      addLog({ type: "ack", topic: "telemetry/ack", status: "ACK_OK", hash: "c1d8…f92b" });
      addAi("✓ 50ml Purple dispensed. Peak draw: 182mA. Constraint satisfied.");
      setSystemState(STATES.IDLE);
      triggerSweep("rtl", "#00FF9D");
      setFocusPanel(null);
    }, 9000);
  }, [addAi, addLog, addMqtt, triggerSweep, triggerAckGlow]);

  // ── DEMO: TEST CASE B ── 2x Green / Cup Swap
  const runTestB = useCallback(() => {
    setSystemState(STATES.EXECUTING);
    triggerSweep("ltr", "#45F3FF");
    setFocusPanel("center");
    setCupFill(0); setCupColor("#00FF9D");
    setPlanId("b7e2d45a");
    setActivePumps({ red: false, blue: true, yellow: true });
    addAi("Batch 1: Mixing Green (Blue + Yellow). Sequential mode.");
    addMqtt("cmd/pump", "VERIFIED", "d2f1...a89e");
    setTimeout(() => {
      setCupFill(95);
      addAi("HC-SR04 reading: 3cm — FULL_CUP detected.");
      setSystemState(STATES.PAUSED);
      triggerSweep("rtl", "#FFD166");
      setActivePumps({ red: false, blue: false, yellow: false });
      addAi("⚠ RECEPTACLE FULL — Pausing execution. Awaiting cup swap.");
    }, 4000);
    setTimeout(() => {
      addAi("Sensor: >15cm — NO_CUP. Waiting for replacement...");
      setDistance(16);
    }, 7000);
    setTimeout(() => {
      setDistance(12);
      addAi("Sensor: 12cm — EMPTY_CUP detected. Resuming batch 2.");
      setSystemState(STATES.EXECUTING);
      triggerSweep("ltr", "#00FF9D");
      setCupFill(0);
      setActivePumps({ red: false, blue: true, yellow: true });
      addMqtt("cmd/pump", "VERIFIED", "f8c3...e21d");
    }, 9500);
    setTimeout(() => {
      triggerAckGlow("blue");
      triggerAckGlow("yellow");
      setActivePumps({ red: false, blue: false, yellow: false });
      setCupFill(50);
      addAi("✓ Batch 2 complete. State machine validated.");
      setSystemState(STATES.IDLE);
      setFocusPanel(null);
    }, 13000);
  }, [addAi, addMqtt, triggerSweep, triggerAckGlow]);

  // ── DEMO: TEST CASE C ── Attack Demo
  const runTestC = useCallback(() => {
    setAttackInProgress(true);
    setTimeout(() => setAttackInProgress(false), 5000);
    setFocusPanel("right");
    setSystemState(STATES.BREACH);
    addMqtt("cmd/pump", "HMAC_FAIL", "FAKE_HASH_123");
    addAi("⛔ INTRUSION DETECTED — Raw JSON injection on mqtt://192.168.137.1:1883");
    addAi("Core 0 cryptographic verification FAILED. Packet dropped. Physical actuation blocked.");
    setActivePumps({ red: false, blue: false, yellow: false });
    setTimeout(() => {
      addAi("Threat neutralized. No physical actuation occurred. Returning to IDLE.");
      setSystemState(STATES.IDLE);
      triggerSweep("ltr", "#45F3FF");
      setFocusPanel(null);
    }, 6000);
  }, [addAi, addMqtt, triggerSweep]);

  // ── POWER CHART (Canvas) ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width = canvas.offsetWidth * 2;
    const h = canvas.height = canvas.offsetHeight * 2;
    ctx.scale(2, 2);
    const dw = canvas.offsetWidth, dh = canvas.offsetHeight;
    ctx.clearRect(0, 0, dw, dh);
    ctx.strokeStyle = "rgba(255,255,255,0.04)";
    ctx.lineWidth = 0.5;
    for (let y = 0; y <= 5; y++) {
      const py = (y / 5) * dh;
      ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(dw, py); ctx.stroke();
    }
    const threshY = dh - (300 / 500) * dh;
    ctx.strokeStyle = "#FF2A2A55";
    ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(0, threshY); ctx.lineTo(dw, threshY); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#FF2A2A99";
    ctx.font = "9px 'JetBrains Mono'";
    ctx.fillText("300mA LIMIT", dw - 75, threshY - 4);
    if (powerHistory.length < 2) return;
    const step = dw / (powerHistory.length - 1);
    ctx.beginPath();
    powerHistory.forEach((v, i) => {
      const x = i * step, y = dh - (v / 500) * dh;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    const lastVal = powerHistory[powerHistory.length - 1];
    const lineColor = lastVal > 300 ? "#FF2A2A" : lastVal > 250 ? "#FFD166" : "#00FF9D";
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2;
    ctx.stroke();
    const lastX = (powerHistory.length - 1) * step;
    ctx.lineTo(lastX, dh);
    ctx.lineTo(0, dh);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, dh);
    grad.addColorStop(0, lineColor + "40");
    grad.addColorStop(1, lineColor + "00");
    ctx.fillStyle = grad;
    ctx.fill();
  }, [powerHistory]);

  // ── FORMAT UPTIME ──
  const fmtTime = (s) => {
    const h = String(Math.floor(s / 3600)).padStart(2, "0");
    const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const sec = String(s % 60).padStart(2, "0");
    return `${h}:${m}:${sec}`;
  };

  // ── LOG ROW STYLE HELPER ──
  const logRowStyle = (type) => {
    switch (type) {
      case "verified":      return { border: "#00FF9D", bg: "transparent",            color: "#aaa" };
      case "fail":          return { border: "#FF2A2A", bg: "rgba(255,42,42,0.10)",   color: "#FF2A2A" };
      case "ack":           return { border: "#00FF9D", bg: "rgba(0,255,157,0.05)",   color: "#00FF9D" };
      case "governor_ok":   return { border: "#FFD166", bg: "rgba(255,209,102,0.05)", color: "#FFD166" };
      case "governor_reject": return { border: "#FFD166", bg: "rgba(255,209,102,0.10)", color: "#FFD166" };
      case "connectivity":  return { border: "#FFD166", bg: "rgba(255,209,102,0.06)", color: "#FFD166" };
      case "drift":         return { border: "#FACC15", bg: "rgba(250,204,21,0.06)",  color: "#FACC15" };
      case "clockfault":    return { border: "#FF2A2A", bg: "rgba(255,42,42,0.10)",   color: "#FF6B35" };
      case "replay":        return { border: "#FFD166", bg: "rgba(255,209,102,0.10)", color: "#FFD166" };
      default:              return { border: "#333",    bg: "transparent",            color: "#aaa" };
    }
  };

  const stateColor = STATE_COLORS[systemState] || "#6b7280";
  const isBreaching = systemState === STATES.BREACH;
  const isError = systemState === STATES.ERROR;

  return (
    <div style={{
      width: "100%", minHeight: "100vh",
      background: "radial-gradient(ellipse at 30% 20%, #12121A 0%, #0B0C10 70%)",
      color: "#E0E0E0", fontFamily: "'Inter', sans-serif", position: "relative", overflow: "hidden"
    }}>
      {/* ── GLOBAL STYLES ── */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
        @keyframes pulseAmber { 0%,100% { opacity: 1; box-shadow: 0 0 6px #FFD166; } 50% { opacity: 0.6; box-shadow: 0 0 14px #FFD166; } }
        @keyframes scan { 0% { transform: translateY(0); opacity: 0.8; } 50% { transform: translateY(60px); opacity: 1; } 100% { transform: translateY(0); opacity: 0.8; } }
        @keyframes flow { 0% { transform: translateX(-20px); opacity: 0; } 50% { opacity: 1; } 100% { transform: translateX(80px); opacity: 0; } }
        @keyframes heartbeat { 0%,100% { box-shadow: 0 0 0px transparent; } 50% { box-shadow: 0 0 8px var(--hb-color, #6b7280); } }
        @keyframes glitch { 0% { transform: translate(0); } 20% { transform: translate(-2px, 1px); } 40% { transform: translate(2px, -1px); } 60% { transform: translate(-1px, 2px); } 80% { transform: translate(1px, -2px); } 100% { transform: translate(0); } }
        @keyframes sweepLTR { 0% { left: -100%; } 100% { left: 100%; } }
        @keyframes sweepRTL { 0% { right: -100%; } 100% { right: 100%; } }
        @keyframes sonar { 0% { transform: scale(0.5); opacity: 0.8; } 100% { transform: scale(3); opacity: 0; } }
        @keyframes fadeSlideUp { 0% { opacity: 0; transform: translateY(12px); } 100% { opacity: 1; transform: translateY(0); } }
        @keyframes breathe { 0%,100% { opacity: 0.02; } 50% { opacity: 0.04; } }
        @keyframes attackPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
        @keyframes mintFlash { 0% { opacity:0; } 20% { opacity:1; } 80% { opacity:1; } 100% { opacity:0; } }
        ::-webkit-scrollbar { width: 4px; } ::-webkit-scrollbar-track { background: transparent; } ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 2px; }
      `}</style>

      {/* ── AMBIENT BG ── */}
      <div style={{
        position: "fixed", inset: 0,
        background: "radial-gradient(circle at 20% 80%, #9D4EDD08 0%, transparent 50%), radial-gradient(circle at 80% 20%, #45F3FF06 0%, transparent 50%)",
        animation: "breathe 8s ease-in-out infinite", pointerEvents: "none", zIndex: 0
      }} />

      {/* ── SWEEP OVERLAY ── */}
      {sweepDir && (
        <div style={{ position: "fixed", inset: 0, zIndex: 100, pointerEvents: "none", overflow: "hidden" }}>
          <div style={{
            position: "absolute", top: 0, width: "100%", height: "100%",
            background: `linear-gradient(90deg, transparent 0%, ${sweepColor}15 50%, transparent 100%)`,
            animation: sweepDir === "ltr" ? "sweepLTR 0.4s ease-out forwards" : "sweepRTL 0.4s ease-out forwards"
          }} />
        </div>
      )}

      {/* ── BREACH VIGNETTE ── */}
      {breachFlash > 0 && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 99, pointerEvents: "none",
          boxShadow: `inset 0 0 120px #FF2A2A${breachFlash === 3 ? "CC" : breachFlash === 2 ? "66" : "33"}`,
          transition: "box-shadow 0.15s ease-out"
        }} />
      )}

      {/* ── ERROR VIGNETTE ── */}
      {isError && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 98, pointerEvents: "none",
          boxShadow: "inset 0 0 80px #FF6B3544",
          animation: "pulse 2s ease-in-out infinite"
        }} />
      )}

      {/* ── TOP STATUS BAR ── */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 24px",
        borderBottom: "1px solid rgba(255,255,255,0.06)", position: "relative", zIndex: 10,
        background: "rgba(11,12,16,0.8)", backdropFilter: "blur(10px)"
      }}>
        {/* Logo */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 28, height: 28, borderRadius: 6, background: "linear-gradient(135deg, #45F3FF, #9D4EDD)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14 }}>🛡</div>
          <span style={{ fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700, fontSize: 13, letterSpacing: 2, color: "#45F3FF", textTransform: "uppercase" }}>Zero-Trust Orchestrator</span>
        </div>

        {/* Center — State Badge + optional alerts */}
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {/* ── MOCK MODE BADGE (Module 11 item 4) ── */}
          {mockMode && (
            <div style={{
              padding: "5px 14px", borderRadius: 14, background: "rgba(255,209,102,0.12)",
              border: "1px solid #FFD16688", fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
              color: "#FFD166", fontWeight: 600, animation: "pulseAmber 2s ease-in-out infinite"
            }}>⚠ SIMULATION MODE — No hardware connected</div>
          )}

          {/* ── BROKER LOST PILL (Module 11 item 8) ── */}
          {!brokerConnected && (
            <div style={{
              padding: "5px 14px", borderRadius: 14, background: "rgba(255,209,102,0.12)",
              border: "1px solid #FFD16688", fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
              color: "#FFD166", animation: "pulseAmber 1.2s ease-in-out infinite"
            }}>⚠ BROKER LOST</div>
          )}

          {/* ── BROKER RESTORED FLASH ── */}
          {brokerFlash === "restored" && (
            <div style={{
              padding: "5px 14px", borderRadius: 14, background: "rgba(0,255,157,0.10)",
              border: "1px solid #00FF9D55", fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
              color: "#00FF9D", animation: "mintFlash 3s ease-out forwards"
            }}>✓ BROKER RESTORED</div>
          )}

          {/* ── DRIFT WARNING PILL ── */}
          {driftWarning === "escalated" && (
            <div style={{
              padding: "5px 14px", borderRadius: 14, background: "rgba(250,204,21,0.10)",
              border: "1px solid #FACC1588", fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
              color: "#FACC15"
            }}>⏱ TIME DRIFT ESCALATED</div>
          )}
          {driftWarning === "clock_fault" && (
            <div style={{
              padding: "5px 14px", borderRadius: 14, background: "rgba(255,42,42,0.12)",
              border: "1px solid #FF2A2A88", fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
              color: "#FF6B35", animation: "pulse 1.5s ease-in-out infinite"
            }}>🕐 CLOCK FAULT — Awaiting re-sync</div>
          )}

          {/* State Badge */}
          <div style={{
            padding: "6px 20px", borderRadius: 20,
            background: isBreaching ? "#FF2A2A22" : isError ? "#FF6B3522" : `${stateColor}15`,
            border: `1px solid ${stateColor}55`,
            fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700, fontSize: 12,
            letterSpacing: 3, color: stateColor, textTransform: "uppercase",
            animation: systemState !== STATES.IDLE ? "none" : "heartbeat 2s ease-in-out infinite",
            "--hb-color": stateColor,
            boxShadow: `0 0 20px ${stateColor}22`
          }}>
            {systemState}
          </div>
        </div>

        {/* Status Pills */}
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 12px", borderRadius: 14, background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)", fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>
            <PulseDot color={stateColor} />
            SYSTEM: {systemState === STATES.EXECUTING ? "ACTIVE" : systemState === STATES.IDLE ? "IDLE" : systemState === STATES.PAUSED ? "PAUSED" : systemState === STATES.ERROR ? "FAULT" : "ALERT"}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 12px", borderRadius: 14, background: "rgba(157,78,221,0.08)", border: "1px solid rgba(157,78,221,0.2)", fontSize: 11, fontFamily: "'JetBrains Mono', monospace", color: "#C084FC" }}>
            🔐 HMAC: ACTIVE
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 12px", borderRadius: 14, background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)", fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>
            📡 EDGE: <OdometerDigit value={latency} color="#45F3FF" /><span style={{ color: "#666" }}>ms</span>
          </div>
        </div>
      </div>

      {/* ── THREE-PANEL LAYOUT ── */}
      <div style={{ display: "grid", gridTemplateColumns: "30% 40% 30%", gap: 16, padding: 16, height: "calc(100vh - 56px)", position: "relative", zIndex: 5 }}>

        {/* ═══ LEFT PANEL — AI CONSOLE ═══ */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
          <GlassPanel accent="#45F3FF" focused={focusPanel === "left"} dimmed={focusPanel && focusPanel !== "left"} style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>

            {/* ── LEFT PANEL HEADER with Plan ID Badge (Module 11 item 2) ── */}
            <div style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: 3, color: "#45F3FF", textTransform: "uppercase", marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#45F3FF", boxShadow: "0 0 8px #45F3FF" }} />
              Orchestrator
              {planId && (
                <span style={{
                  marginLeft: "auto", padding: "2px 10px", borderRadius: 8,
                  background: "rgba(69,243,255,0.08)", border: "1px solid #45F3FF33",
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: "#45F3FF99",
                  letterSpacing: 1, fontWeight: 500, textTransform: "none"
                }}>Plan: {planId}</span>
              )}
            </div>

            {/* ── GOVERNOR REJECTION CARD (Module 11 item 1) — amber, not cyan ── */}
            {governorCard && (
              <div style={{
                padding: "8px 12px", borderRadius: 8, marginBottom: 8,
                background: "rgba(255,209,102,0.08)", border: "1px solid #FFD16644",
                fontSize: 11, color: "#FFD166", fontFamily: "'JetBrains Mono', monospace",
                animation: "fadeSlideUp 0.3s ease-out"
              }}>
                ⚠ GOVERNOR OVERRIDE: {governorCard.reason}
                {governorCard.pump && <span style={{ color: "#FFD16699", marginLeft: 6 }}>— pump: {governorCard.pump}</span>}
                <div style={{ fontSize: 9, color: "#FFD16666", marginTop: 3 }}>LLM command rejected by Safety Governor</div>
              </div>
            )}

            {/* AI Reasoning Stream */}
            <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 8, minHeight: 0, paddingRight: 4 }}>
              {aiLog.length === 0 && (
                <div style={{ color: "#555", fontSize: 12, fontStyle: "italic", padding: 20, textAlign: "center" }}>
                  Awaiting command input...
                </div>
              )}
              {aiLog.map((entry) => (
                <div key={entry.id} style={{
                  padding: "10px 12px", borderRadius: 8, fontSize: 12, lineHeight: 1.5,
                  background: entry.type === "tool" ? "rgba(69,243,255,0.06)" : "rgba(255,255,255,0.03)",
                  borderLeft: `2px solid ${entry.active ? "#45F3FF" : "#45F3FF33"}`,
                  animation: "fadeSlideUp 0.3s ease-out",
                  color: entry.text.startsWith("⚠") ? "#FFD166" : entry.text.startsWith("⛔") ? "#FF2A2A" : entry.text.startsWith("✓") ? "#00FF9D" : "#ccc"
                }}>
                  {entry.text}
                </div>
              ))}
              {systemState === STATES.EXECUTING && (
                <div style={{ display: "flex", gap: 4, padding: 8 }}>
                  {[0,1,2].map(i => <div key={i} style={{ width: 6, height: 6, borderRadius: "50%", background: "#45F3FF", animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite` }} />)}
                </div>
              )}
            </div>

            {/* Tool Call Pill */}
            {toolCall && (
              <div style={{
                padding: "6px 12px", borderRadius: 14, background: "rgba(69,243,255,0.1)", border: "1px solid #45F3FF44",
                fontSize: 11, fontFamily: "'JetBrains Mono', monospace", color: "#45F3FF", margin: "8px 0",
                animation: "fadeSlideUp 0.2s ease-out"
              }}>⚙ {toolCall}</div>
            )}

            {/* Input Area */}
            <div style={{ borderTop: "1px solid rgba(255,255,255,0.06)", paddingTop: 12, marginTop: 8 }}>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  value={inputVal} onChange={e => setInputVal(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") sendCommand(inputVal); }}
                  placeholder="Enter constraint-aware command..."
                  style={{
                    flex: 1, background: "rgba(255,255,255,0.04)", border: "1px solid rgba(69,243,255,0.2)",
                    borderRadius: 8, padding: "10px 14px", color: "#E0E0E0", fontSize: 12,
                    fontFamily: "'Inter', sans-serif", outline: "none", transition: "border-color 0.2s, box-shadow 0.2s"
                  }}
                  onFocus={e => { e.target.style.borderColor = "#45F3FF"; e.target.style.boxShadow = "0 0 12px #45F3FF22"; }}
                  onBlur={e => { e.target.style.borderColor = "rgba(69,243,255,0.2)"; e.target.style.boxShadow = "none"; }}
                />
                <button
                  onClick={() => sendCommand(inputVal)}
                  style={{
                    padding: "8px 16px", borderRadius: 8, border: "none", cursor: "pointer",
                    background: "linear-gradient(135deg, #45F3FF, #3B82F6)", color: "#000",
                    fontWeight: 600, fontSize: 12, fontFamily: "'Space Grotesk', sans-serif",
                    transition: "transform 0.15s, box-shadow 0.15s"
                  }}
                  onMouseEnter={e => { e.target.style.transform = "scale(1.05)"; e.target.style.boxShadow = "0 0 20px #45F3FF44"; }}
                  onMouseLeave={e => { e.target.style.transform = "scale(1)"; e.target.style.boxShadow = "none"; }}
                >Send</button>
              </div>

              {/* Preset Buttons (Module 11 item 7 — Attack Demo state) */}
              <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                {["Mix 50ml Purple / ≤300mA", "2x Green / Cup Swap"].map((cmd, i) => (
                  <button key={i} onClick={() => i === 0 ? runTestA() : runTestB()}
                    style={{
                      padding: "5px 10px", borderRadius: 12, border: "1px solid rgba(255,255,255,0.1)",
                      background: "rgba(255,255,255,0.04)", color: "#aaa", fontSize: 10, cursor: "pointer",
                      fontFamily: "'JetBrains Mono', monospace", transition: "all 0.2s", whiteSpace: "nowrap"
                    }}
                    onMouseEnter={e => { e.target.style.borderColor = "#45F3FF55"; e.target.style.color = "#45F3FF"; }}
                    onMouseLeave={e => { e.target.style.borderColor = "rgba(255,255,255,0.1)"; e.target.style.color = "#aaa"; }}
                  >{cmd}</button>
                ))}

                {/* ── ATTACK DEMO BUTTON with state change (Module 11 item 7) ── */}
                <button onClick={runTestC}
                  style={{
                    padding: "5px 10px", borderRadius: 12,
                    border: attackInProgress ? "1px solid #FF2A2A" : "1px solid rgba(255,255,255,0.1)",
                    background: attackInProgress ? "rgba(255,42,42,0.15)" : "rgba(255,255,255,0.04)",
                    color: attackInProgress ? "#FF2A2A" : "#aaa",
                    fontSize: 10, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace",
                    transition: "all 0.2s", whiteSpace: "nowrap",
                    animation: attackInProgress ? "attackPulse 0.6s ease-in-out infinite" : "none",
                    fontWeight: attackInProgress ? 700 : 400
                  }}
                  onMouseEnter={e => { if (!attackInProgress) { e.target.style.borderColor = "#FF2A2A55"; e.target.style.color = "#FF2A2A"; }}}
                  onMouseLeave={e => { if (!attackInProgress) { e.target.style.borderColor = "rgba(255,255,255,0.1)"; e.target.style.color = "#aaa"; }}}
                >
                  {attackInProgress ? "⚡ ATTACK IN PROGRESS" : "Run Attack Demo"}
                </button>
              </div>
            </div>
          </GlassPanel>
        </div>

        {/* ═══ CENTER PANEL — DIGITAL TWIN ═══ */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>

          {/* PAUSED BANNER */}
          {systemState === STATES.PAUSED && (
            <div style={{
              padding: "10px 20px", borderRadius: 10, background: "rgba(255,209,102,0.12)",
              border: "1px solid #FFD16655", textAlign: "center", fontFamily: "'Space Grotesk', sans-serif",
              fontWeight: 600, fontSize: 13, color: "#FFD166", animation: "pulse 2s ease-in-out infinite"
            }}>
              ⚠ RECEPTACLE FULL — REPLACE CUP TO RESUME
            </div>
          )}

          {/* ERROR BANNER */}
          {isError && (
            <div style={{
              padding: "10px 20px", borderRadius: 10, background: "rgba(255,107,53,0.12)",
              border: "1px solid #FF6B3555", textAlign: "center", fontFamily: "'Space Grotesk', sans-serif",
              fontWeight: 600, fontSize: 13, color: "#FF6B35", animation: "pulse 1.5s ease-in-out infinite"
            }}>
              ⛔ SYSTEM ERROR — ALL PUMPS HALTED — MANUAL RESET REQUIRED
            </div>
          )}

          {/* Power Chart */}
          <GlassPanel accent="#00FF9D" focused={focusPanel === "center"} dimmed={focusPanel && focusPanel !== "center"} style={{ flex: "0 0 160px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 11, fontWeight: 600, letterSpacing: 2, color: "#888", textTransform: "uppercase" }}>Live Power Telemetry</span>
              <div style={{ display: "flex", alignItems: "baseline", gap: 4 }}>
                <OdometerDigit value={mA} color={mA > 300 ? "#FF2A2A" : mA > 250 ? "#FFD166" : "#00FF9D"} />
                <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: "#666" }}>mA</span>
              </div>
            </div>
            <canvas ref={canvasRef} style={{ width: "100%", height: 90, borderRadius: 6 }} />
            <div style={{ display: "flex", gap: 16, marginTop: 8 }}>
              <span style={{ fontSize: 10, fontFamily: "'JetBrains Mono', monospace", color: "#666" }}>Current: <span style={{ color: "#00FF9D" }}>{mA}mA</span></span>
              <span style={{ fontSize: 10, fontFamily: "'JetBrains Mono', monospace", color: "#666" }}>Peak: <span style={{ color: "#FFD166" }}>{peakMA}mA</span></span>
            </div>
          </GlassPanel>

          {/* Physical Simulation */}
          <GlassPanel accent="#3B82F6" focused={focusPanel === "center"} dimmed={focusPanel && focusPanel !== "center"} style={{ flex: 1, position: "relative", minHeight: 0 }}>
            <span style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 11, fontWeight: 600, letterSpacing: 2, color: "#888", textTransform: "uppercase" }}>Micro-Refinery Simulation</span>
            <svg viewBox="0 0 500 280" style={{ width: "100%", marginTop: 8 }}>
              {/* Source Cups + Pumps + Relay */}
              {[
                { color: "#FF2A2A", label: "RED",  y: 30,  pump: "red",    rate: "8.5ml/s" },
                { color: "#3B82F6", label: "BLUE", y: 110, pump: "blue",   rate: "9.1ml/s" },
                { color: "#FFD166", label: "YLW",  y: 190, pump: "yellow", rate: "7.8ml/s" }
              ].map((cup, i) => {
                const isActive = activePumps[cup.pump];
                const isAckGlowing = ackGlow[cup.pump];
                return (
                  <g key={i}>
                    {/* Cup body */}
                    <rect x={20} y={cup.y} width={50} height={55} rx={4} fill="none" stroke={cup.color} strokeWidth={1.5} opacity={0.6} />
                    <rect x={22} y={cup.y + 10} width={46} height={43} rx={2} fill={cup.color} opacity={0.2} />
                    <rect x={22} y={cup.y + 25} width={46} height={28} rx={2} fill={cup.color} opacity={0.4} />
                    <text x={45} y={cup.y + 68} textAnchor="middle" fill={cup.color} fontSize={8} fontFamily="Space Grotesk" fontWeight={600}>{cup.label}</text>
                    <text x={45} y={cup.y + 78} textAnchor="middle" fill="#555" fontSize={7} fontFamily="JetBrains Mono">{cup.rate}</text>

                    {/* Tube */}
                    <path d={`M 70 ${cup.y + 27} C 150 ${cup.y + 27}, 200 150, 300 150`}
                      fill="none"
                      stroke={isActive ? cup.color : "#333"}
                      strokeWidth={isActive ? 3 : 2}
                      opacity={isActive ? 0.8 : 0.3}
                      strokeLinecap="round" />

                    {/* Flow particles */}
                    {isActive && [0, 1, 2, 3].map(p => (
                      <circle key={p} r={3} fill={cup.color} opacity={0.9}>
                        <animateMotion dur={`${1.2 + p * 0.15}s`} begin={`${p * 0.3}s`} repeatCount="indefinite"
                          path={`M 70 ${cup.y + 27} C 150 ${cup.y + 27}, 200 150, 300 150`} />
                      </circle>
                    ))}

                    {/* ── RELAY INDICATOR with ACK glow (Module 11 item 3) ── */}
                    {/* isAckGlowing → mint green 300ms pulse; isActive → pump color; else dim */}
                    <rect x={100} y={cup.y + 20} width={14} height={14} rx={2}
                      fill={isAckGlowing ? "rgba(0,255,157,0.15)" : isActive ? cup.color + "33" : "#1a1a2e"}
                      stroke={isAckGlowing ? "#00FF9D" : isActive ? cup.color : "#333"}
                      strokeWidth={isAckGlowing ? 2 : 1}
                      style={{ filter: isAckGlowing ? "drop-shadow(0 0 4px #00FF9D)" : "none" }}
                    />
                    {isActive && (
                      <circle cx={107} cy={cup.y + 27} r={3} fill={cup.color}>
                        <animate attributeName="r" values="3;6;3" dur="0.6s" repeatCount="1" />
                        <animate attributeName="opacity" values="1;0;1" dur="0.6s" repeatCount="1" />
                      </circle>
                    )}
                    {/* ACK pulse ring */}
                    {isAckGlowing && (
                      <circle cx={107} cy={cup.y + 27} r={3} fill="none" stroke="#00FF9D" strokeWidth={1} opacity={0.9}>
                        <animate attributeName="r" values="3;10;3" dur="0.3s" repeatCount="1" />
                        <animate attributeName="opacity" values="0.9;0;0.9" dur="0.3s" repeatCount="1" />
                      </circle>
                    )}
                    {/* Breach X on relay */}
                    {isBreaching && (
                      <>
                        <line x1={101} y1={cup.y + 21} x2={113} y2={cup.y + 33} stroke="#FF2A2A" strokeWidth={2} />
                        <line x1={113} y1={cup.y + 21} x2={101} y2={cup.y + 33} stroke="#FF2A2A" strokeWidth={2} />
                      </>
                    )}
                  </g>
                );
              })}

              {/* Target Cup */}
              <rect x={310} y={90} width={70} height={90} rx={6} fill="none" stroke="#555" strokeWidth={2} />
              <rect x={313} y={90 + 90 - (cupFill / 100 * 80)} width={64} height={cupFill / 100 * 80} rx={3} fill={cupColor} opacity={0.35}>
                <animate attributeName="opacity" values="0.3;0.45;0.3" dur="2s" repeatCount="indefinite" />
              </rect>
              {cupFill > 5 && (
                <path d={`M 313 ${90 + 90 - cupFill / 100 * 80} Q 330 ${87 + 90 - cupFill / 100 * 80} 345 ${90 + 90 - cupFill / 100 * 80} Q 360 ${93 + 90 - cupFill / 100 * 80} 377 ${90 + 90 - cupFill / 100 * 80}`}
                  fill="none" stroke={cupColor} strokeWidth={1} opacity={0.6}>
                  <animate attributeName="d"
                    values={`M 313 ${90+90-cupFill/100*80} Q 330 ${87+90-cupFill/100*80} 345 ${90+90-cupFill/100*80} Q 360 ${93+90-cupFill/100*80} 377 ${90+90-cupFill/100*80};M 313 ${90+90-cupFill/100*80} Q 330 ${93+90-cupFill/100*80} 345 ${90+90-cupFill/100*80} Q 360 ${87+90-cupFill/100*80} 377 ${90+90-cupFill/100*80};M 313 ${90+90-cupFill/100*80} Q 330 ${87+90-cupFill/100*80} 345 ${90+90-cupFill/100*80} Q 360 ${93+90-cupFill/100*80} 377 ${90+90-cupFill/100*80}`}
                    dur="2s" repeatCount="indefinite" />
                </path>
              )}
              <text x={345} y={195} textAnchor="middle" fill="#888" fontSize={9} fontFamily="Space Grotesk" fontWeight={600}>TARGET</text>
              <text x={345} y={207} textAnchor="middle" fill="#555" fontSize={8} fontFamily="JetBrains Mono">{Math.round(cupFill)}%</text>

              {/* HC-SR04 Sensor */}
              <rect x={330} y={60} width={30} height={18} rx={3}
                fill={sensorError ? "rgba(255,42,42,0.15)" : "#1a1a2e"}
                stroke={sensorError ? "#FF2A2A" : "#45F3FF"}
                strokeWidth={sensorError ? 1.5 : 1} opacity={0.7} />
              <text x={345} y={72} textAnchor="middle"
                fill={sensorError ? "#FF2A2A" : "#45F3FF"}
                fontSize={6} fontFamily="JetBrains Mono">SR04</text>

              {/* ── SENSOR ERROR OVERLAY — red X on SR04 (Module 11 item 5) ── */}
              {sensorError && (
                <>
                  <line x1={332} y1={62} x2={358} y2={76} stroke="#FF2A2A" strokeWidth={2} />
                  <line x1={358} y1={62} x2={332} y2={76} stroke="#FF2A2A" strokeWidth={2} />
                </>
              )}

              {/* Scan line (shows NO_CUP warning as red when out of range) */}
              {!sensorError && (
                <line x1={345} y1={80} x2={345} y2={90}
                  stroke={distance > 15 ? "#FF2A2A" : distance < 5 ? "#FF2A2A" : "#45F3FF"}
                  strokeWidth={1} opacity={0.6}>
                  <animate attributeName="y2" values="80;90;80" dur="1.5s" repeatCount="indefinite" />
                </line>
              )}

              {/* No-cup warning triangle (distinct from sensor error X) */}
              {!sensorError && distance > 15 && (
                <g>
                  <polygon points="345,40 337,54 353,54" fill="none" stroke="#FFD166" strokeWidth={1.5} opacity={0.8} />
                  <text x={345} y={52} textAnchor="middle" fill="#FFD166" fontSize={7} fontFamily="JetBrains Mono">!</text>
                </g>
              )}

              {/* Sonar rings */}
              {!sensorError && (
                <circle cx={345} cy={78} r={4} fill="none" stroke="#45F3FF" strokeWidth={0.5} opacity={0}>
                  <animate attributeName="r" values="4;15" dur="1.5s" repeatCount="indefinite" />
                  <animate attributeName="opacity" values="0.5;0" dur="1.5s" repeatCount="indefinite" />
                </circle>
              )}
              <text x={345} y={55} textAnchor="middle"
                fill={sensorError ? "#FF2A2A" : "#45F3FF"}
                fontSize={9} fontFamily="JetBrains Mono" fontWeight={500}>
                {sensorError ? "ERR" : `${distance}cm`}
              </text>

              {/* INA219 */}
              <rect x={400} y={140} width={40} height={20} rx={3} fill="#1a1a2e" stroke="#00FF9D" strokeWidth={1} opacity={0.5} />
              <text x={420} y={153} textAnchor="middle" fill="#00FF9D" fontSize={6} fontFamily="JetBrains Mono">INA219</text>
              <text x={420} y={172} textAnchor="middle" fill="#00FF9D88" fontSize={8} fontFamily="JetBrains Mono">{mA}mA</text>

              {/* Labels */}
              <text x={107} y={260} textAnchor="middle" fill="#555" fontSize={7} fontFamily="JetBrains Mono">RELAY MODULE</text>
              <text x={250} y={260} textAnchor="middle" fill="#45F3FF33" fontSize={7} fontFamily="Space Grotesk" fontWeight={600}>ESP32 · CORE 1 · FreeRTOS</text>
            </svg>

            {/* Breach overlay */}
            {isBreaching && (
              <div style={{ position: "absolute", inset: 0, background: "rgba(255,42,42,0.05)", animation: "glitch 0.3s ease-in-out 3", borderRadius: 12, pointerEvents: "none" }} />
            )}
          </GlassPanel>

          {/* Action Bar */}
          <div style={{ display: "flex", gap: 10 }}>
            <button
              onClick={() => { if (systemState === STATES.IDLE) { setSystemState(STATES.EXECUTING); triggerSweep("ltr", "#45F3FF"); }}}
              disabled={systemState !== STATES.IDLE}
              style={{
                flex: 1, padding: "12px 0", borderRadius: 10, border: "none",
                cursor: systemState === STATES.IDLE ? "pointer" : "default",
                background: systemState === STATES.IDLE ? "linear-gradient(135deg, #45F3FF, #3B82F6)" : "#1a1a2e",
                color: systemState === STATES.IDLE ? "#000" : "#555",
                fontFamily: "'Space Grotesk', sans-serif", fontWeight: 700, fontSize: 13, letterSpacing: 1, transition: "all 0.2s"
              }}>
              {systemState === STATES.EXECUTING ? "⏳ RUNNING..." : "▶ START EXECUTION"}
            </button>
            <button
              onClick={() => { if (systemState === STATES.EXECUTING) { setSystemState(STATES.PAUSED); triggerSweep("rtl", "#FFD166"); }}}
              style={{
                padding: "12px 20px", borderRadius: 10, border: "1px solid #FFD16644",
                background: "transparent", color: "#FFD166", cursor: "pointer",
                fontFamily: "'Space Grotesk', sans-serif", fontWeight: 600, fontSize: 13
              }}>⏸ PAUSE</button>
            {/* HALT / Manual Reset — works from any non-IDLE state */}
            <button
              onClick={() => {
                setSystemState(STATES.IDLE);
                setActivePumps({ red: false, blue: false, yellow: false });
                setFocusPanel(null);
                setGovernorCard(null);
                setDriftWarning(null);
                setSensorError(false);
              }}
              style={{
                padding: "12px 20px", borderRadius: 10, border: "2px solid #FF2A2A88",
                background: "repeating-linear-gradient(45deg, #FF2A2A11, #FF2A2A11 4px, transparent 4px, transparent 8px)",
                color: "#FF2A2A", cursor: "pointer", fontFamily: "'Space Grotesk', sans-serif",
                fontWeight: 700, fontSize: 13, transition: "transform 0.1s"
              }}
              onMouseEnter={e => { e.target.style.transform = "translateX(-2px)"; setTimeout(() => e.target.style.transform = "translateX(2px)", 80); setTimeout(() => e.target.style.transform = "none", 160); }}
            >⬛ HALT</button>
          </div>
        </div>

        {/* ═══ RIGHT PANEL — SECURITY WATCHDOG ═══ */}
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
          <GlassPanel accent="#9D4EDD" focused={focusPanel === "right"} dimmed={focusPanel && focusPanel !== "right"} style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, position: "relative" }}>
            {isBreaching && (
              <div style={{ position: "absolute", inset: 0, animation: "glitch 0.2s ease-in-out 5", borderRadius: 12, pointerEvents: "none", background: "rgba(255,42,42,0.03)" }} />
            )}

            <div style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: 3, color: "#C084FC", textTransform: "uppercase", marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#9D4EDD", boxShadow: "0 0 8px #9D4EDD" }} />
              HMAC Watchdog — Core 0
            </div>

            {/* Unified Packet + Governor + Connectivity Feed */}
            <div style={{ flex: 1, overflow: "auto", minHeight: 0, fontFamily: "'JetBrains Mono', monospace", fontSize: 10, lineHeight: 1.8 }}>
              {mqttLog.length === 0 && (
                <div style={{ color: "#444", fontSize: 11, padding: 16, textAlign: "center" }}>No packets intercepted</div>
              )}
              {mqttLog.map((pkt) => {
                const s = logRowStyle(pkt.type);
                return (
                  <div key={pkt.id} style={{
                    padding: "4px 8px", borderRadius: 4, marginBottom: 3,
                    borderLeft: `2px solid ${s.border}`,
                    background: s.bg,
                    animation: "fadeSlideUp 0.2s ease-out",
                    display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap"
                  }}>
                    <span style={{ color: "#555", flexShrink: 0 }}>{pkt.ts}</span>
                    <span style={{ color: s.border, fontWeight: 600, flexShrink: 0 }}>
                      {pkt.topic}
                    </span>
                    <span style={{ color: s.color, fontWeight: 600, flexShrink: 0 }}>
                      {pkt.status}
                    </span>
                    <span style={{ color: "#555", marginLeft: "auto", textAlign: "right", maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {pkt.hash}
                    </span>
                  </div>
                );
              })}
            </div>

            {/* Threat Panel */}
            {isBreaching && (
              <div style={{
                margin: "8px 0", padding: "10px 12px", borderRadius: 8,
                background: "rgba(255,42,42,0.08)", border: "1px solid #FF2A2A44",
                fontSize: 11, color: "#FF2A2A", fontFamily: "'JetBrains Mono', monospace",
                animation: "fadeSlideUp 0.3s ease-out"
              }}>
                ⛔ INTRUSION DETECTED<br />
                <span style={{ color: "#FF2A2A99", fontSize: 10 }}>Raw JSON injection on mqtt://192.168.137.1:1883</span>
              </div>
            )}

            {/* Crypto + System Stats */}
            <div style={{ borderTop: "1px solid rgba(255,255,255,0.06)", paddingTop: 12, marginTop: 8 }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}>
                <div style={{ color: "#666" }}>Verified: <span style={{ color: "#00FF9D" }}>{packetsVerified}</span></div>
                <div style={{ color: "#666" }}>Dropped: <span style={{ color: "#FF2A2A" }}>{packetsDropped}</span></div>
                <div style={{ color: "#666" }}>Uptime: <span style={{ color: "#C084FC" }}>{fmtTime(uptime)}</span></div>
                <div style={{ color: "#666" }}>Key: <span style={{ color: "#9D4EDD" }}>H4ck***_K3y_**</span></div>
                <div style={{ color: "#666" }}>Broker: <span style={{ color: brokerConnected ? "#00FF9D" : brokerReconnecting ? "#FFD166" : "#FF2A2A" }}>
                  {brokerConnected ? "ONLINE" : brokerReconnecting ? "RECONNECTING" : "OFFLINE"}
                </span></div>
                <div style={{ color: "#666" }}>Peak: <span style={{ color: "#FFD166" }}>{peakMA}mA</span></div>
              </div>
            </div>
          </GlassPanel>
        </div>

      </div>
    </div>
  );
}
