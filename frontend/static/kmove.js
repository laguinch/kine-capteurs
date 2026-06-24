const $ = (id) => document.getElementById(id);
const state = {
  history: { rotation: [], flexion: [], inclination: [] },
  lastTimestamp: null,
  ranges: {
    rotation: { min: 0, max: 0 },
    flexion_extension: { min: 0, max: 0 },
    inclination: { min: 0, max: 0 },
  },
};
const format = (value, digits = 1) =>
  Number.isFinite(value)
    ? value.toLocaleString("fr-FR", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : "—";

function message(text, error = false, ready = false) {
  $("message").textContent = text || "";
  $("message").classList.toggle("hidden", !text);
  $("message").classList.toggle("error", error);
  $("message").classList.toggle("ready", ready);
}

function drawSeries(ctx, values, color, width, height, maximum) {
  if (values.length < 2) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  values.forEach((value, index) => {
    const x = index / (values.length - 1) * width;
    const y = height / 2 - value / maximum * (height * 0.46);
    index ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
}

function draw() {
  const canvas = $("angleChart");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * dpr);
  canvas.height = Math.max(1, rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.strokeStyle = "#dce5e1";
  [0.25, 0.5, 0.75].forEach((ratio) => {
    const y = rect.height * ratio;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(rect.width, y);
    ctx.stroke();
  });
  const all = [
    ...state.history.rotation,
    ...state.history.flexion,
    ...state.history.inclination,
  ];
  const maximum = Math.max(20, ...all.map((value) => Math.abs(value))) * 1.1;
  drawSeries(ctx, state.history.rotation, "#347fb4", rect.width, rect.height, maximum);
  drawSeries(ctx, state.history.flexion, "#147c75", rect.width, rect.height, maximum);
  drawSeries(ctx, state.history.inclination, "#e7792f", rect.width, rect.height, maximum);
}

function rangeText(range) {
  return `Min ${format(range?.min || 0, 1)}° · Max ${format(range?.max || 0, 1)}°`;
}

function update(data) {
  const phase = data.phase || "disconnected";
  const active = phase === "active";
  const ready = phase === "ready";
  const busy = ["connecting", "reference"].includes(phase);
  $("statusDot").className =
    `status-dot ${data.connected ? "running" : data.last_error ? "error" : ""}`;
  const labels = {
    disconnected: "K‑Move déconnecté",
    connecting: "Connexion au K‑Move",
    reference: "Mise à zéro",
    ready: "K‑Move prêt",
    active: "Acquisition en cours",
    error: "Erreur",
  };
  $("statusText").textContent = labels[phase] || "Prêt";
  $("connectButton").disabled = data.connected || busy;
  $("disconnectButton").disabled = !data.connected || active;
  $("startButton").disabled = !ready;
  $("stopButton").disabled = !active;
  $("downloadButton").classList.toggle("disabled", !data.csv_path);
  $("fileLabel").textContent = data.csv_path
    ? data.csv_path.split("/").pop()
    : "Aucun fichier en cours";

  const elapsed = active || data.finished_at ? data.elapsed_seconds || 0 : 0;
  $("timer").textContent =
    `${Math.floor(elapsed / 60).toString().padStart(2, "0")}:` +
    `${Math.floor(elapsed % 60).toString().padStart(2, "0")}`;

  if (data.last_error) {
    message(data.last_error, true);
  } else if (phase === "connecting") {
    message("Recherche et connexion au K‑Move…");
  } else if (phase === "reference") {
    message("Maintenez le K‑Move parfaitement immobile pendant la mise à zéro.");
  } else if (phase === "ready") {
    message("Référence enregistrée. Le K‑Move est prêt.");
  } else if (phase === "active") {
    message("Test en cours : effectuez le mouvement demandé.");
  } else if (phase === "disconnected") {
    message("Cliquez sur « Connecter le K‑Move ».");
  }
  maybeSave(data);

  const m = data.measurement;
  if (!m || m.timestamp_utc === state.lastTimestamp) return;
  state.lastTimestamp = m.timestamp_utc;
  state.ranges = m.ranges || state.ranges;
  $("rotation").textContent = format(m.rotation_deg, 1);
  $("flexion").textContent = format(m.flexion_extension_deg, 1);
  $("inclination").textContent = format(m.inclination_deg, 1);
  $("batteryBadge").textContent = `Batterie ${m.battery_pct} %`;
  $("rotationRange").textContent = rangeText(m.ranges?.rotation);
  $("flexionRange").textContent = rangeText(m.ranges?.flexion_extension);
  $("inclinationRange").textContent = rangeText(m.ranges?.inclination);
  if (active) {
    state.history.rotation.push(m.rotation_deg);
    state.history.flexion.push(m.flexion_extension_deg);
    state.history.inclination.push(m.inclination_deg);
    for (const values of Object.values(state.history)) {
      if (values.length > 300) values.shift();
    }
    draw();
  }
}

async function maybeSave(data) {
  if (!window.KinePatientSave) return;
  const selection = window.KinePatientSave.selection();
  const label = [selection.articulation, selection.mouvement].filter(Boolean).join(" · ");
  const rotation = state.ranges.rotation || {};
  const flexion = state.ranges.flexion_extension || {};
  const inclination = state.ranges.inclination || {};
  try {
    const saved = await window.KinePatientSave.saveEvaluation(data, {
      sensor: "K-Move",
      test_name: selection.mouvement || "Mobilité",
      display_name: label ? `K‑Move — ${label}` : "K‑Move — mobilité tridimensionnelle",
      summary:
        `Rotation ${format(rotation.min, 1)}° à ${format(rotation.max, 1)}° · ` +
        `flexion ${format(flexion.min, 1)}° à ${format(flexion.max, 1)}° · ` +
        `inclinaison ${format(inclination.min, 1)}° à ${format(inclination.max, 1)}°`,
    });
    if (saved) message("✓ Test terminé et enregistré dans le dossier patient.", false, true);
  } catch (error) {
    message(`${error.message}. Le fichier CSV reste disponible.`, true);
  }
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Commande impossible");
  update(data);
}

async function connect() {
  try {
    await request("/api/kmove/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference_duration: 2 }),
    });
  } catch (error) {
    message(error.message, true);
  }
}

async function disconnect() {
  try {
    await request("/api/kmove/disconnect", { method: "POST" });
  } catch (error) {
    message(error.message, true);
  }
}

async function start() {
  state.history = { rotation: [], flexion: [], inclination: [] };
  state.lastTimestamp = null;
  state.ranges = {
    rotation: { min: 0, max: 0 },
    flexion_extension: { min: 0, max: 0 },
    inclination: { min: 0, max: 0 },
  };
  draw();
  try {
    await request("/api/kmove/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        duration: Number($("duration").value),
        filename: $("filename").value.trim() || null,
      }),
    });
  } catch (error) {
    message(error.message, true);
  }
}

async function stopTest() {
  $("stopButton").disabled = true;
  message("Arrêt et enregistrement du test…");
  try {
    await request("/api/kmove/stop", { method: "POST" });
  } catch (error) {
    message(error.message, true);
    $("stopButton").disabled = false;
  }
}

async function poll() {
  try {
    const response = await fetch("/api/kmove/latest", { cache: "no-store" });
    update(await response.json());
  } catch (error) {
    message("Serveur indisponible", true);
  }
}

$("connectButton").addEventListener("click", connect);
$("disconnectButton").addEventListener("click", disconnect);
$("startButton").addEventListener("click", start);
$("stopButton").addEventListener("click", stopTest);
window.addEventListener("resize", draw);
poll();
setInterval(poll, 150);
