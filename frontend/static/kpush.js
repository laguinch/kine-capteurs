const $ = (id) => document.getElementById(id);
const state = { history: [], lastTimestamp: null };
const format = (value, digits = 1) =>
  Number.isFinite(value)
    ? value.toLocaleString("fr-FR", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : "—";

function message(text, error = false) {
  $("message").textContent = text || "";
  $("message").classList.toggle("hidden", !text);
  $("message").classList.toggle("error", error);
}

function draw() {
  const canvas = $("forceChart");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * dpr);
  canvas.height = Math.max(1, rect.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const maximum = Math.max(100, ...state.history);
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
  state.history.forEach((force, index) => {
    const x = index / (state.history.length - 1) * rect.width;
    const y = rect.height - Math.max(0, force) / maximum * rect.height;
    index ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
}

function update(data) {
  const phase = data.phase || "disconnected";
  const active = phase === "active";
  const ready = phase === "ready";
  const busy = ["connecting", "tare"].includes(phase);
  $("statusDot").className =
    `status-dot ${data.connected ? "running" : data.last_error ? "error" : ""}`;
  const labels = {
    disconnected: "K‑Push déconnecté",
    connecting: "Connexion au K‑Push",
    tare: "Tare en cours",
    ready: "K‑Push prêt",
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
    message("Recherche et connexion au K‑Push…");
  } else if (phase === "tare") {
    message("Laissez le K‑Push sans pression pendant la tare.");
  } else if (phase === "ready") {
    message("Tare terminée. Le K‑Push est prêt : vous pouvez démarrer le test.");
  } else if (phase === "active") {
    message("Test en cours : exercez la force demandée.");
  } else if (phase === "disconnected") {
    message("Cliquez sur « Connecter le K‑Push ».");
  }

  const m = data.measurement;
  if (!m || m.timestamp_utc === state.lastTimestamp) return;
  state.lastTimestamp = m.timestamp_utc;
  $("forceN").textContent = format(Math.max(0, m.force_n), 0);
  $("forceKg").textContent = `${format(Math.max(0, m.force_kg), 1)} kg`;
  $("maxN").textContent = format(m.max_force_n, 0);
  $("maxKg").textContent = `${format(m.max_force_kg, 1)} kg`;
  if (active) {
    state.history.push(Math.max(0, m.force_n));
    if (state.history.length > 300) state.history.shift();
    draw();
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
    await request("/api/kpush/connect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tare_duration: 2 }),
    });
  } catch (error) {
    message(error.message, true);
  }
}

async function disconnect() {
  try {
    await request("/api/kpush/disconnect", { method: "POST" });
  } catch (error) {
    message(error.message, true);
  }
}

async function start() {
  state.history = [];
  state.lastTimestamp = null;
  draw();
  try {
    await request("/api/kpush/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        duration: Number($("duration").value),
        tare_duration: 2,
        filename: $("filename").value.trim() || null,
      }),
    });
  } catch (error) {
    message(error.message, true);
  }
}

async function stop() {
  try {
    await request("/api/kpush/stop", { method: "POST" });
  } catch (error) {
    message(error.message, true);
  }
}

async function poll() {
  try {
    const response = await fetch("/api/kpush/latest", { cache: "no-store" });
    update(await response.json());
  } catch (error) {
    message("Serveur indisponible", true);
  }
}

$("connectButton").addEventListener("click", connect);
$("disconnectButton").addEventListener("click", disconnect);
$("startButton").addEventListener("click", start);
$("stopButton").addEventListener("click", stop);
window.addEventListener("resize", draw);
poll();
setInterval(poll, 150);
