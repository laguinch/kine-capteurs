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
  return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
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
  canvas.width = Math.max(1, rect.width * dpr);
  canvas.height = Math.max(1, rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.strokeStyle = "#dce5e1";
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
    const x = index / (state.history.length - 1) * rect.width;
    const y = rect.height - Math.max(0, emg) / 1023 * rect.height;
    index ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
}

function scheduleDraw() {
  const now = performance.now();
  if (state.drawScheduled) return;
  const delay = Math.max(0, 100 - (now - state.lastDrawAt));
  state.drawScheduled = true;
  window.setTimeout(() => window.requestAnimationFrame(draw), delay);
}

function update(data) {
  if (!data || typeof data !== "object") return;
  const phase = data.phase || "disconnected";
  const active = phase === "active";
  const ready = phase === "ready";
  const busy = phase === "connecting";
  $("statusDot").className =
    `status-dot ${data.connected ? "running" : data.last_error ? "error" : ""}`;
  const labels = {
    disconnected: "ANR M40 déconnecté",
    connecting: "Connexion à l’ANR M40",
    ready: "ANR M40 prêt",
    active: "Acquisition en cours",
    error: "Erreur",
  };
  $("statusText").textContent = labels[phase] || "Prêt";
  $("connectButton").disabled = state.commandInFlight || data.connected || busy;
  $("disconnectButton").disabled = state.commandInFlight || !data.connected || active;
  $("startButton").disabled = state.commandInFlight || !ready;
  $("stopButton").disabled = state.commandInFlight || !active;
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
    message("Recherche et connexion à l’ANR M40…");
  } else if (phase === "ready") {
    message("ANR M40 prêt : vous pouvez démarrer le test.", false, true);
  } else if (phase === "active") {
    message("Test EMG en cours.");
  } else if (phase === "disconnected") {
    message("Cliquez sur « Connecter l’ANR M40 ».");
  }
  if (hasNumber(data.battery_pct)) {
    $("batteryBadge").textContent = `Batterie ${Number(data.battery_pct)} %`;
  }

  const m = data.measurement;
  if (!m || m.timestamp_utc === state.lastTimestamp) return;
  state.lastTimestamp = m.timestamp_utc;
  if (hasNumber(m.battery_pct)) {
    $("batteryBadge").textContent = `Batterie ${Number(m.battery_pct)} %`;
  }
  state.maxEmgRaw = Math.max(state.maxEmgRaw, Number(m.max_emg_raw) || 0);
  $("emgRaw").textContent = format(Number(m.emg_raw), 0);
  $("maxEmgRaw").textContent = format(Number(m.max_emg_raw), 0);
  if (active) {
    state.history.push(Math.max(0, Number(m.emg_raw) || 0));
    if (state.history.length > 300) state.history.shift();
    scheduleDraw();
  }
}

async function request(path, options = {}) {
  if (state.commandInFlight) return;
  state.commandInFlight = true;
  try {
    const response = await fetch(path, options);
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json")
      ? await response.json()
      : { detail: await response.text() };
    if (!response.ok) throw new Error(data.detail || "Commande impossible");
    update(data);
  } finally {
    state.commandInFlight = false;
  }
}

async function connect() {
  try {
    await request("/api/anr-m40/connect", { method: "POST" });
  } catch (error) {
    message(error.message, true);
  }
}

async function disconnect() {
  try {
    await request("/api/anr-m40/disconnect", { method: "POST" });
  } catch (error) {
    message(error.message, true);
  }
}

async function start() {
  $("startButton").disabled = true;
  state.history = [];
  state.lastTimestamp = null;
  state.maxEmgRaw = 0;
  draw();
  try {
    await request("/api/anr-m40/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        duration: Number($("duration").value),
        filename: $("filename").value.trim() || null,
      }),
    });
  } catch (error) {
    message(error.message, true);
    $("startButton").disabled = false;
  }
}

async function stopTest() {
  $("stopButton").disabled = true;
  $("statusText").textContent = "Arrêt du test…";
  message("Arrêt du test…");
  try {
    await request("/api/anr-m40/stop", { method: "POST" });
  } catch (error) {
    message(error.message, true);
    $("stopButton").disabled = false;
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

$("connectButton").addEventListener("click", connect);
$("disconnectButton").addEventListener("click", disconnect);
$("startButton").addEventListener("click", start);
$("stopButton").addEventListener("click", stopTest);
window.addEventListener("resize", draw);
window.addEventListener("error", (event) => {
  message(`Erreur écran ANR M40: ${event.message}`, true);
});
poll();
setInterval(poll, 300);
