const $ = (id) => document.getElementById(id);

const state = {
  busy: false,
  polling: false,
  history: [],
  lastTimestamp: "",
  maxEmg: 0,
};

function fmt(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number).toLocaleString("fr-FR") : "—";
}

function message(text, error = false, ready = false) {
  const element = $("message");
  element.textContent = text || "";
  element.classList.toggle("hidden", !text);
  element.classList.toggle("error", error);
  element.classList.toggle("ready", ready);
}

function draw() {
  const canvas = $("emgChart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);

  ctx.strokeStyle = "#dce5e1";
  ctx.lineWidth = 1;
  [0.25, 0.5, 0.75].forEach((ratio) => {
    const y = height * (1 - ratio);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  });

  if (state.history.length < 2) return;
  ctx.strokeStyle = "#147c75";
  ctx.lineWidth = 3;
  ctx.beginPath();
  state.history.forEach((value, index) => {
    const x = (index / (state.history.length - 1)) * width;
    const y = height - (Math.max(0, value) / 1023) * height;
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

async function jsonFetch(path, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), options.timeout || 4000);
  try {
    const response = await fetch(path, {
      ...options,
      cache: "no-store",
      signal: controller.signal,
    });
    const type = response.headers.get("content-type") || "";
    const data = type.includes("application/json")
      ? await response.json()
      : { detail: await response.text() };
    if (!response.ok) throw new Error(data.detail || "Commande impossible");
    return data;
  } finally {
    window.clearTimeout(timeout);
  }
}

function setButtons(data) {
  const connected = Boolean(data.connected);
  const running = Boolean(data.running);
  const connecting = data.phase === "connecting";
  $("connectButton").disabled = state.busy || connected || connecting;
  $("disconnectButton").disabled = state.busy || !connected || running;
  $("startButton").disabled = state.busy || !connected || running || connecting;
  $("stopButton").disabled = state.busy || !running;
  $("downloadButton").classList.toggle("disabled", !data.csv_path);
}

function update(data) {
  if (!data || typeof data !== "object") return;

  const running = Boolean(data.running);
  const phase = running ? "active" : data.phase || "disconnected";
  const labels = {
    disconnected: "ANR M40 déconnecté",
    connecting: "Connexion à l’ANR M40",
    ready: "ANR M40 prêt",
    active: "Acquisition en cours",
    error: "Erreur",
  };

  $("statusText").textContent = labels[phase] || "Prêt";
  $("statusDot").className =
    `status-dot ${data.connected ? "running" : data.last_error ? "error" : ""}`;

  if (Number.isFinite(Number(data.battery_pct))) {
    $("batteryBadge").textContent = `Batterie ${Number(data.battery_pct)} %`;
  }

  const elapsed = Number(data.elapsed_seconds || 0);
  $("timer").textContent =
    `${Math.floor(elapsed / 60).toString().padStart(2, "0")}:` +
    `${Math.floor(elapsed % 60).toString().padStart(2, "0")}`;

  $("fileLabel").textContent = data.csv_path
    ? data.csv_path.split("/").pop()
    : "Aucun fichier en cours";

  if (data.last_error) {
    message(data.last_error, true);
  } else if (running) {
    message("Test EMG en cours.");
  } else if (phase === "ready") {
    message("ANR M40 prêt : les valeurs live doivent bouger.", false, true);
  } else if (phase === "connecting") {
    message("Connexion à l’ANR M40…");
  } else {
    message("Cliquez sur « Connecter l’ANR M40 ».");
  }

  const measurement = data.measurement;
  if (measurement && measurement.timestamp_utc !== state.lastTimestamp) {
    state.lastTimestamp = measurement.timestamp_utc;
    const emg = Number(measurement.emg_raw);
    if (Number.isFinite(emg)) {
      state.maxEmg = Math.max(state.maxEmg, emg);
      $("emgRaw").textContent = fmt(emg);
      $("maxEmgRaw").textContent = fmt(state.maxEmg);
      if (running) {
        state.history.push(emg);
        if (state.history.length > 240) state.history.shift();
        draw();
      }
    }
  }

  setButtons(data);
}

async function command(path, body = null) {
  if (state.busy) return;
  state.busy = true;
  try {
    const options = { method: "POST", timeout: 5000 };
    if (body) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    update(await jsonFetch(path, options));
  } catch (error) {
    message(
      error.name === "AbortError"
        ? "Commande trop lente. Le flux live continue, rechargez la page si besoin."
        : error.message,
      true,
    );
  } finally {
    state.busy = false;
  }
}

async function poll() {
  if (state.polling) return;
  state.polling = true;
  try {
    update(await jsonFetch("/api/anr-m40/latest", { timeout: 900 }));
  } catch (error) {
    if (error.name !== "AbortError") message("Serveur indisponible", true);
  } finally {
    state.polling = false;
  }
}

$("connectButton").addEventListener("click", () => command("/api/anr-m40/connect"));
$("disconnectButton").addEventListener("click", () => command("/api/anr-m40/disconnect"));
$("startButton").addEventListener("click", () => {
  state.history = [];
  state.maxEmg = 0;
  state.lastTimestamp = "";
  $("maxEmgRaw").textContent = "0";
  draw();
  command("/api/anr-m40/start", {
    duration: Number($("duration").value) || 30,
    filename: $("filename").value.trim() || null,
  });
});
$("stopButton").addEventListener("click", () => command("/api/anr-m40/stop"));

draw();
poll();
window.setInterval(poll, 100);
