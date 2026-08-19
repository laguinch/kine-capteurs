const $ = (id) => document.getElementById(id);

const state = {
  history: [],
  lastTimestamp: null,
  maxEmgRaw: 0,
  pollInFlight: false,
  commandInFlight: false,
  drawScheduled: false,
  lastDrawAt: 0,
};

const format = (value, digits = 0) =>
  Number.isFinite(value)
    ? value.toLocaleString("fr-FR", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : "—";

function hasNumber(value) {
  return (
    value !== null &&
    value !== undefined &&
    value !== "" &&
    Number.isFinite(Number(value))
  );
}

function message(text, error = false, ready = false) {
  $("message").textContent = text || "";
  $("message").classList.toggle("hidden", !text);
  $("message").classList.toggle("error", error);
  $("message").classList.toggle("ready", ready);
}

function draw() {
  state.drawScheduled = false;
  state.lastDrawAt = performance.now();
  const canvas = $("emgChart");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));

  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);

  ctx.strokeStyle = "#dce5e1";
  ctx.lineWidth = 1;
  [0.25, 0.5, 0.75].forEach((ratio) => {
    const y = rect.height * (1 - ratio);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(rect.width, y);
    ctx.stroke();
  });

  if (state.history.length < 2) return;

  ctx.strokeStyle = "#147c75";
  ctx.lineWidth = 3;
  ctx.beginPath();
  state.history.forEach((emg, index) => {
    const x = (index / (state.history.length - 1)) * rect.width;
    const y = rect.height - (Math.max(0, emg) / 1023) * rect.height;
    if (index) ctx.lineTo(x, y);
    else ctx.moveTo(x, y);
  });
  ctx.stroke();
}

function scheduleDraw() {
  if (state.drawScheduled) return;
  const delay = Math.max(0, 80 - (performance.now() - state.lastDrawAt));
  state.drawScheduled = true;
  window.setTimeout(() => window.requestAnimationFrame(draw), delay);
}

function setTimer(seconds) {
  const value = Number(seconds) || 0;
  $("timer").textContent =
    `${Math.floor(value / 60).toString().padStart(2, "0")}:` +
    `${Math.floor(value % 60).toString().padStart(2, "0")}`;
}

function resetLiveView() {
  state.history = [];
  state.lastTimestamp = null;
  state.maxEmgRaw = 0;
  $("emgRaw").textContent = "0";
  $("maxEmgRaw").textContent = "0";
  setTimer(0);
  draw();
}

function update(data) {
  if (!data || typeof data !== "object") return;

  const phase = data.phase || "ready";
  const active = phase === "active";
  const connecting = phase === "connecting";
  const unavailable = phase === "error";

  $("statusDot").className =
    `status-dot ${data.connected ? "running" : unavailable ? "error" : ""}`;

  const labels = {
    connecting: "Connexion à l’ANR M40",
    ready: "ANR M40 prêt",
    active: "Test en cours",
    error: "Erreur",
  };
  $("statusText").textContent = labels[phase] || "ANR M40 prêt";

  $("startButton").disabled = state.commandInFlight || active || connecting;
  $("stopButton").disabled = state.commandInFlight || !(active || connecting);
  $("downloadButton").classList.toggle(
    "disabled",
    !data.csv_path || active || connecting,
  );
  $("fileLabel").textContent = data.csv_path
    ? data.csv_path.split("/").pop()
    : "Aucun fichier disponible";

  setTimer(data.elapsed_seconds);

  if (hasNumber(data.battery_pct)) {
    $("batteryBadge").textContent = `Batterie ${Number(data.battery_pct)} %`;
  }

  if (data.last_error) {
    message(data.last_error, true);
  } else if (connecting) {
    message("Connexion au capteur et attente des premières données…");
  } else if (active) {
    message("Test EMG en cours.");
  } else {
    message("Prêt pour un test simple ANR M40.", false, true);
  }

  const measurement = data.measurement;
  if (!measurement || measurement.timestamp_utc === state.lastTimestamp) {
    return;
  }

  state.lastTimestamp = measurement.timestamp_utc;
  const emg = Number(measurement.emg_raw) || 0;
  state.maxEmgRaw = Math.max(
    state.maxEmgRaw,
    Number(measurement.max_emg_raw) || emg,
  );
  $("emgRaw").textContent = format(emg, 0);
  $("maxEmgRaw").textContent = format(state.maxEmgRaw, 0);

  if (hasNumber(measurement.battery_pct)) {
    $("batteryBadge").textContent = `Batterie ${Number(measurement.battery_pct)} %`;
  }

  if (active) {
    state.history.push(Math.max(0, emg));
    if (state.history.length > 300) state.history.shift();
    scheduleDraw();
  }
}

async function send(path, options = {}) {
  if (state.commandInFlight) return null;
  state.commandInFlight = true;
  try {
    const response = await fetch(path, options);
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json")
      ? await response.json()
      : { detail: await response.text() };
    if (!response.ok) throw new Error(data.detail || "Commande impossible");
    update(data);
    return data;
  } finally {
    state.commandInFlight = false;
  }
}

async function start() {
  resetLiveView();
  message("Démarrage du test ANR M40…");
  try {
    await send("/api/anr-m40/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
  } catch (error) {
    message(error.message, true);
  }
}

async function stopTest() {
  message("Arrêt du test et préparation du CSV…");
  try {
    await send("/api/anr-m40/stop", { method: "POST" });
  } catch (error) {
    message(error.message, true);
  }
}

async function poll() {
  if (state.pollInFlight) return;
  state.pollInFlight = true;
  try {
    const response = await fetch("/api/anr-m40/latest", { cache: "no-store" });
    update(await response.json());
  } catch (error) {
    message("Serveur indisponible", true);
  } finally {
    state.pollInFlight = false;
  }
}

$("startButton").addEventListener("click", start);
$("stopButton").addEventListener("click", stopTest);
window.addEventListener("resize", draw);
window.addEventListener("error", (event) => {
  message(`Erreur écran ANR M40: ${event.message}`, true);
});

draw();
poll();
setInterval(poll, 300);
