const $ = (id) => document.getElementById(id);

const state = {
  history: [],
  polling: null,
  startedAt: null,
  awaitingTare: false,
};

const format = (value, digits = 1) =>
  Number.isFinite(value)
    ? value.toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : "—";

function setMessage(text, error = false) {
  const box = $("message");
  box.textContent = text || "";
  box.classList.toggle("hidden", !text);
  box.classList.toggle("error", error);
}

function updateStatus(data) {
  const running = Boolean(data.running);
  $("statusDot").className = `status-dot ${running ? "running" : data.last_error ? "error" : ""}`;
  $("statusText").textContent = running ? "Acquisition en cours" : data.last_error ? "Erreur" : "Prêt";
  $("startButton").disabled = running;
  $("stopButton").disabled = !running;
  $("fileLabel").textContent = data.csv_path ? data.csv_path.split("/").pop() : "Aucun fichier en cours";
  $("downloadButton").classList.toggle("disabled", !data.csv_path);

  const elapsed = data.elapsed_seconds || 0;
  const minutes = Math.floor(elapsed / 60).toString().padStart(2, "0");
  const seconds = Math.floor(elapsed % 60).toString().padStart(2, "0");
  $("timer").textContent = `${minutes}:${seconds}`;

  if (data.last_error) {
    state.awaitingTare = false;
    setMessage(data.last_error, true);
  } else if (!running && data.return_code === 0 && data.csv_path) {
    state.awaitingTare = false;
    setMessage("Acquisition terminée et enregistrée.");
  }
}

function updateMeasurement(m) {
  if (!m) return;
  if (state.awaitingTare) {
    state.awaitingTare = false;
    setMessage("Tare terminée. Vous pouvez monter sur les plateformes.");
  }
  $("leftKg").textContent = format(m.left_kg);
  $("rightKg").textContent = format(m.right_kg);
  $("totalKg").textContent = format(m.total_kg);
  $("totalN").textContent = `${format(m.total_n, 0)} N`;
  $("leftPct").textContent = `${format(m.left_pct)} %`;
  $("rightPct").textContent = `${format(m.right_pct)} %`;
  $("asymmetryBadge").textContent = `${m.asymmetry_pct >= 0 ? "+" : ""}${format(m.asymmetry_pct)} %`;

  const leftPct = Number.isFinite(m.left_pct) ? Math.max(0, Math.min(100, m.left_pct)) : 50;
  $("balanceLeft").style.width = `${leftPct}%`;

  const x = Number.isFinite(m.global_cop_x) ? Math.max(-1.5, Math.min(1.5, m.global_cop_x)) : 0;
  const y = Number.isFinite(m.global_cop_y) ? Math.max(-1, Math.min(1, m.global_cop_y)) : 0;
  $("copDot").style.left = `${50 + x * 30}%`;
  $("copDot").style.top = `${50 - y * 40}%`;
  $("copX").textContent = format(m.global_cop_x, 3);
  $("copY").textContent = format(m.global_cop_y, 3);
  $("syncDelta").textContent = Number.isFinite(m.sync_delta_ms) ? `${format(m.sync_delta_ms)} ms` : "—";
  $("syncBadge").textContent = m.sync_quality === "excellent" ? "Excellente" : "Acceptable";
  $("syncBadge").classList.remove("neutral");

  if (Number.isFinite(m.left_pct)) {
    state.history.push({ left: m.left_pct, right: m.right_pct });
    if (state.history.length > 180) state.history.shift();
    drawHistory();
  }
}

function drawHistory() {
  const canvas = $("historyChart");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * dpr);
  canvas.height = Math.max(1, rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);

  ctx.strokeStyle = "#dce5e1";
  ctx.lineWidth = 1;
  [25, 50, 75].forEach((pct) => {
    const y = height - (pct / 100) * height;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  });

  const drawLine = (key, color) => {
    if (state.history.length < 2) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    state.history.forEach((point, index) => {
      const x = (index / (state.history.length - 1)) * width;
      const y = height - (point[key] / 100) * height;
      index ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  };
  drawLine("left", "#3977a9");
  drawLine("right", "#df7b37");
}

async function poll() {
  try {
    const response = await fetch("/api/kplates/dual/latest", { cache: "no-store" });
    if (!response.ok) throw new Error("API indisponible");
    const data = await response.json();
    updateStatus(data);
    updateMeasurement(data.measurement);
  } catch (error) {
    $("statusDot").className = "status-dot error";
    $("statusText").textContent = "Serveur indisponible";
    setMessage(error.message, true);
  }
}

async function start() {
  setMessage("");
  state.history = [];
  state.awaitingTare = true;
  const filename = $("filename").value.trim();
  const body = {
    adapter: "hci1",
    duration: Number($("duration").value),
    tare_duration: 2,
    sync_tolerance_ms: 20,
    filename: filename || null,
    recalibrate: $("recalibrate").checked,
  };
  try {
    const response = await fetch("/api/kplates/dual/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Démarrage impossible");
    state.awaitingTare = Boolean(data.tare_required);
    updateStatus(data);
    setMessage(
      state.awaitingTare
        ? "Laissez les deux plateformes vides pendant la tare."
        : "Tare existante chargée. Vous pouvez monter sur les plateformes."
    );
  } catch (error) {
    state.awaitingTare = false;
    setMessage(error.message, true);
  }
}

async function stop() {
  state.awaitingTare = false;
  try {
    const response = await fetch("/api/kplates/dual/stop", { method: "POST" });
    const data = await response.json();
    updateStatus(data);
  } catch (error) {
    setMessage(error.message, true);
  }
}

$("startButton").addEventListener("click", start);
$("stopButton").addEventListener("click", stop);
window.addEventListener("resize", drawHistory);
poll();
state.polling = setInterval(poll, 250);
