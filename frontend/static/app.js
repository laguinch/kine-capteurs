const $ = (id) => document.getElementById(id);

const state = {
  history: [],
  polling: null,
  startedAt: null,
  awaitingTare: false,
  awaitingReady: false,
  lastMeasurementTimestamp: null,
  cmjResultLoaded: false,
  cmjReady: false,
};

const format = (value, digits = 1) =>
  Number.isFinite(value)
    ? value.toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : "—";

function setMessage(text, error = false, ready = false) {
  const box = $("message");
  box.textContent = text || "";
  box.classList.toggle("hidden", !text);
  box.classList.toggle("error", error);
  box.classList.toggle("ready", ready);
}

function updateStatus(data) {
  const running = Boolean(data.running);
  const validating = Boolean(data.validating_streams);
  const connecting = ["connecting", "recovering"].includes(data.worker_phase);
  const degraded = data.worker_phase === "degraded";
  const cmjMode = data.mode === "cmj";
  $("statusDot").className = `status-dot ${running || data.worker_ready ? "running" : data.last_error ? "error" : ""}`;
  $("statusText").textContent = running
    ? validating
      ? "Vérification des plateformes"
      : "Acquisition en cours"
    : connecting
      ? "Connexion des capteurs"
      : degraded
        ? "Connexion partielle"
      : cmjMode && data.finished_at && data.csv_path
        ? "CMJ enregistré"
      : data.last_error
        ? "Erreur"
        : data.worker_ready
          ? "Plateformes connectées"
          : "Service Bluetooth arrêté";
  $("startButton").disabled = running;
  $("stopButton").disabled = !running;
  $("connectButton").disabled = running || connecting || data.bluetooth_connected;
  $("disconnectButton").disabled = running || connecting || (!data.bluetooth_connected && !degraded);
  $("fileLabel").textContent = data.csv_path ? data.csv_path.split("/").pop() : "Aucun fichier en cours";
  $("downloadButton").classList.toggle("disabled", !data.csv_path);
  $("balanceMetrics").classList.toggle("hidden", cmjMode);
  $("balanceVisuals").classList.toggle("hidden", cmjMode);
  $("cmjResults").classList.toggle(
    "hidden",
    !cmjMode || running || !data.finished_at
  );
  $("cmjDetails").classList.toggle(
    "hidden",
    !cmjMode || running || !data.finished_at
  );

  const elapsed = data.elapsed_seconds || 0;
  const minutes = Math.floor(elapsed / 60).toString().padStart(2, "0");
  const seconds = Math.floor(elapsed % 60).toString().padStart(2, "0");
  $("timer").textContent = `${minutes}:${seconds}`;

  if (
    data.last_error
    && cmjMode
    && data.csv_path
    && data.finished_at
  ) {
    setMessage(
      "✓ CMJ enregistré et analysé. Reconnectez les plateformes avant le prochain essai.",
      false,
      true
    );
  } else if (data.last_error) {
    state.awaitingTare = false;
    state.awaitingReady = false;
    if (!running) resetMeasurement();
    setMessage(data.last_error, true);
  } else if (data.worker_phase === "disconnected") {
    state.awaitingTare = false;
    state.awaitingReady = false;
    setMessage("Capteurs déconnectés. Cliquez sur « Connecter les capteurs ».");
  } else if (!running && data.return_code === 0 && data.csv_path) {
    state.awaitingTare = false;
    state.awaitingReady = false;
    setMessage("Acquisition terminée et enregistrée.");
  } else if (running && cmjMode) {
    const preparation = data.cmj_preparation;
    if (preparation?.ready) {
      state.cmjReady = true;
      setMessage(
        `✓ Poids enregistré : ${format(preparation.body_mass_kg, 1)} kg. Vous pouvez débuter le saut.`,
        false,
        true
      );
    } else if (preparation?.status === "stabilizing") {
      setMessage(
        "Patient détecté. Restez debout et immobile pendant l’enregistrement du poids."
      );
    } else {
      setMessage(
        "Montez sur les deux plateformes et restez debout immobile."
      );
    }
  }
  if (
    !running
    && data.mode === "cmj"
    && data.csv_path
    && data.finished_at
    && !state.cmjResultLoaded
  ) {
    loadCmjResult();
  }
}

async function loadCmjResult() {
  state.cmjResultLoaded = true;
  try {
    const response = await fetch("/api/kplates/cmj/result", {
      cache: "no-store",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Analyse CMJ impossible");
    $("jumpHeight").textContent = format(result.jump_height_cm, 1);
    $("flightTime").textContent =
      `Temps de vol ${format(result.flight_time_s, 3)} s`;
    $("cmjPeakKg").textContent = format(result.peak_force_kg, 1);
    $("cmjPeakN").textContent = `${format(result.peak_force_n, 0)} N`;
    $("cmjRate").textContent = format(
      Math.min(result.left_source_rate_hz, result.right_source_rate_hz),
      0
    );
    $("cmjSamples").textContent =
      `${result.raw_event_count} événements bruts conservés`;
    $("leftPeakKg").textContent = format(result.left_peak_force_kg, 1);
    $("rightPeakKg").textContent = format(result.right_peak_force_kg, 1);
    $("forceDifference").textContent =
      `G ${format(result.left_peak_force_n, 0)} N · ` +
      `D ${format(result.right_peak_force_n, 0)} N · ` +
      `différence ${format(result.peak_force_difference_kg, 1)} kg · ` +
      `${format(result.peak_force_asymmetry_pct, 1)} %`;
    $("takeoffFirst").textContent =
      result.takeoff_difference_reliable
        ? `Pied ${result.takeoff_first_side}`
        : "Non discriminable";
    $("takeoffDifference").textContent =
      `Écart ${format(result.takeoff_difference_ms, 0)} ms · ` +
      `résolution ≈ ${format(result.temporal_resolution_ms, 0)} ms`;
    $("landingFirst").textContent =
      result.landing_difference_reliable
        ? `Pied ${result.landing_first_side}`
        : "Non discriminable";
    $("landingDifference").textContent =
      `Écart ${format(result.landing_difference_ms, 0)} ms · ` +
      `résolution ≈ ${format(result.temporal_resolution_ms, 0)} ms`;
  } catch (error) {
    setMessage(error.message, true);
  }
}

function updateMeasurement(m) {
  if (!m) return;
  if (
    m.timestamp_utc
    && m.timestamp_utc === state.lastMeasurementTimestamp
  ) return;
  state.lastMeasurementTimestamp = m.timestamp_utc || null;
  if (state.awaitingReady) {
    state.awaitingReady = false;
    state.awaitingTare = false;
    setMessage("Plateformes prêtes. Vous pouvez monter.");
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

function resetMeasurement() {
  state.history = [];
  state.lastMeasurementTimestamp = null;
  $("leftKg").textContent = "0,0";
  $("rightKg").textContent = "0,0";
  $("totalKg").textContent = "0,0";
  $("totalN").textContent = "0 N";
  $("leftPct").textContent = "0,0 %";
  $("rightPct").textContent = "0,0 %";
  $("asymmetryBadge").textContent = "0,0 %";
  $("balanceLeft").style.width = "50%";
  $("copDot").style.left = "50%";
  $("copDot").style.top = "50%";
  $("copX").textContent = "0,000";
  $("copY").textContent = "0,000";
  $("syncDelta").textContent = "—";
  $("syncBadge").textContent = "En attente";
  $("syncBadge").classList.add("neutral");
  drawHistory();
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
  resetMeasurement();
  state.awaitingTare = true;
  state.awaitingReady = true;
  state.cmjResultLoaded = false;
  state.cmjReady = false;
  const mode = $("testMode").value;
  $("balanceMetrics").classList.toggle("hidden", mode === "cmj");
  $("balanceVisuals").classList.toggle("hidden", mode === "cmj");
  $("cmjResults").classList.add("hidden");
  $("cmjDetails").classList.add("hidden");
  const filename = $("filename").value.trim();
  const body = {
    adapter: "hci1",
    duration: Number($("duration").value),
    tare_duration: 2,
    sync_tolerance_ms: 20,
    filename: filename || null,
    recalibrate: $("recalibrate").checked,
    mode,
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
      mode === "cmj"
        ? "Montez sur les deux plateformes et restez debout immobile. Attendez le feu vert avant de sauter."
        : state.awaitingTare
        ? "Laissez les deux plateformes vides pendant la tare."
        : "Plateformes connectées. Démarrage de l’enregistrement…"
    );
  } catch (error) {
    state.awaitingTare = false;
    state.awaitingReady = false;
    setMessage(error.message, true);
  }
}

async function stop() {
  state.awaitingTare = false;
  state.awaitingReady = false;
  try {
    const response = await fetch("/api/kplates/dual/stop", { method: "POST" });
    const data = await response.json();
    updateStatus(data);
  } catch (error) {
    setMessage(error.message, true);
  }
}

async function setBluetoothConnection(action) {
  setMessage(
    action === "connect"
      ? "Connexion aux plateformes…"
      : "Déconnexion des plateformes…"
  );
  try {
    const response = await fetch(`/api/kplates/dual/${action}`, {
      method: "POST",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Commande impossible");
    updateStatus(data);
  } catch (error) {
    setMessage(error.message, true);
  }
}

$("startButton").addEventListener("click", start);
$("stopButton").addEventListener("click", stop);
$("connectButton").addEventListener("click", () => setBluetoothConnection("connect"));
$("disconnectButton").addEventListener("click", () => setBluetoothConnection("disconnect"));
window.addEventListener("resize", drawHistory);
poll();
state.polling = setInterval(poll, 250);
