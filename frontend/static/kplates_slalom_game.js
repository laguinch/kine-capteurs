const $ = (id) => document.getElementById(id);

const canvas = $("slalomCanvas");
const ctx = canvas.getContext("2d");
const params = new URLSearchParams(window.location.search);
const flowContext = params.get("context") || "anonymous";
const patientId = flowContext.startsWith("patient:")
  ? Number(flowContext.split(":")[1])
  : null;

const game = {
  running: false,
  playing: false,
  stopping: false,
  level: "easy",
  x: 0.5,
  targetX: 0.5,
  score: 0,
  startedAt: null,
  lastFrameAt: performance.now(),
  lastGateAt: 0,
  gates: [],
  direction: "center",
  latestMeasurement: null,
  lastMeasurementTimestamp: null,
  saved: false,
  csvPath: null,
};

const movementThreshold = 10;
const fullSteeringAsymmetry = 45;
const playerYRatio = 0.86;
const gameDurationSeconds = 60;
const levels = {
  easy: {
    label: "Facile",
    description: "Déclenchement léger · vitesse lente · portes larges",
    speedFactor: 0.55,
    steeringEase: 0.10,
    gateWidth: 0.34,
    gateEveryMs: 1500,
  },
  medium: {
    label: "Moyen",
    description: "Déclenchement léger · vitesse modérée · portes moyennes",
    speedFactor: 0.75,
    steeringEase: 0.14,
    gateWidth: 0.28,
    gateEveryMs: 1300,
  },
  expert: {
    label: "Expert",
    description: "Déclenchement léger · vitesse rapide · portes serrées",
    speedFactor: 1,
    steeringEase: 0.18,
    gateWidth: 0.23,
    gateEveryMs: 1100,
  },
};

function format(value, digits = 0) {
  return Number.isFinite(value)
    ? value.toLocaleString("fr-FR", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      })
    : "—";
}

function message(text, error = false, ready = false) {
  const box = $("gameMessage");
  box.textContent = text || "";
  box.classList.toggle("error", error);
  box.classList.toggle("ready", ready);
}

function setLevelControlsDisabled(disabled) {
  document.querySelectorAll(".level-choice").forEach((button) => {
    button.disabled = disabled;
  });
}

function updateStatus(data) {
  const connected = Boolean(data.bluetooth_connected);
  const running = Boolean(data.running);
  $("gameStatusDot").className = `status-dot ${running || connected ? "running" : data.last_error ? "error" : ""}`;
  $("gameStatusText").textContent = running
    ? "Jeu en cours"
    : connected
      ? "Plateformes connectées"
      : data.last_error
        ? "Erreur"
        : "Déconnecté";
  $("connectButton").disabled = connected || running;
  $("startGameButton").disabled = running || !connected;
  $("stopGameButton").disabled = !running;
  document.querySelectorAll(".connect-action").forEach((button) => {
    button.disabled = connected || running;
  });
  document.querySelectorAll(".start-action").forEach((button) => {
    button.disabled = running || !connected;
  });
  document.querySelectorAll(".stop-action").forEach((button) => {
    button.disabled = !running;
  });
  setLevelControlsDisabled(running);
  if (data.csv_path) game.csvPath = data.csv_path;
  if (data.measurement) {
    game.latestMeasurement = data.measurement;
    updateControls(data.measurement);
  }
  if (!running && game.running) {
    finishGame();
  }
  if (data.last_error) {
    message(data.last_error, true);
  }
}

function updateControls(measurement) {
  if (
    game.running
    && !game.playing
    && measurement.timestamp_utc
    && measurement.timestamp_utc !== game.lastMeasurementTimestamp
  ) {
    game.playing = true;
    game.startedAt = performance.now();
    game.lastGateAt = performance.now();
    message("Slalom lancé : passez entre les portes.", false, true);
  }
  game.lastMeasurementTimestamp = measurement.timestamp_utc || null;

  const left = Number.isFinite(measurement.left_pct) ? measurement.left_pct : 0;
  const right = Number.isFinite(measurement.right_pct) ? measurement.right_pct : 0;
  const asymmetry = Number.isFinite(measurement.asymmetry_pct) ? measurement.asymmetry_pct : 0;
  $("leftForceBar").style.width = `${Math.max(0, Math.min(100, left))}%`;
  $("rightForceBar").style.width = `${Math.max(0, Math.min(100, right))}%`;
  $("leftForceText").textContent = `${format(left)} %`;
  $("rightForceText").textContent = `${format(right)} %`;

  const normalizedSteering = Math.max(
    -1,
    Math.min(1, asymmetry / fullSteeringAsymmetry)
  );
  game.targetX = 0.5 + normalizedSteering * 0.34;
  if (asymmetry < -movementThreshold) {
    game.direction = "left";
  } else if (asymmetry > movementThreshold) {
    game.direction = "right";
  } else {
    game.direction = "center";
  }
  $("directionValue").textContent =
    game.direction === "left"
      ? "Gauche"
      : game.direction === "right"
        ? "Droite"
        : "Centre";
}

async function poll() {
  try {
    const response = await fetch("/api/kplates/dual/latest", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Lecture impossible");
    updateStatus(data);
  } catch (error) {
    $("gameStatusDot").className = "status-dot error";
    $("gameStatusText").textContent = "Serveur indisponible";
    message(error.message, true);
  }
}

async function connect() {
  message("Connexion aux plateformes…");
  try {
    const response = await fetch("/api/kplates/dual/connect", { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Connexion impossible");
    updateStatus(data);
    message("Plateformes connectées. Vous pouvez démarrer le jeu.", false, true);
  } catch (error) {
    message(error.message, true);
  }
}

async function startGame() {
  game.score = 0;
  game.startedAt = null;
  game.lastGateAt = 0;
  game.gates = [];
  game.x = 0.5;
  game.targetX = 0.5;
  game.playing = false;
  game.stopping = false;
  game.lastMeasurementTimestamp = null;
  game.saved = false;
  game.csvPath = null;
  $("scoreValue").textContent = "0";
  $("gameTimer").textContent = "00:00";
  message("Préparation du jeu : attente des premières mesures des plateformes.");
  try {
    const response = await fetch("/api/kplates/dual/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        duration: gameDurationSeconds,
        mode: "balance",
        recalibrate: false,
        filename: `kplates_slalom_${game.level}_${new Date().toISOString().replace(/[:.]/g, "-")}.csv`,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Démarrage impossible");
    game.running = true;
    updateStatus(data);
  } catch (error) {
    game.running = false;
    game.playing = false;
    message(error.message, true);
  }
}

async function stopGame() {
  try {
    await fetch("/api/kplates/dual/stop", { method: "POST" });
  } finally {
    finishGame();
  }
}

function finishGame() {
  if (!game.running) return;
  game.running = false;
  game.playing = false;
  game.gates = [];
  message(`Slalom terminé. Score : ${game.score}.`, false, true);
  saveTrainingSummary();
}

async function finishGameAndStopAcquisition() {
  if (!game.running || game.stopping) return;
  game.stopping = true;
  try {
    await fetch("/api/kplates/dual/stop", { method: "POST" });
  } finally {
    finishGame();
  }
}

async function saveTrainingSummary() {
  if (!patientId || game.saved) return;
  game.saved = true;
  try {
    await fetch("/api/evaluations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        patient_id: patientId,
        session_type: "training",
        sensor: "K-Force Plates",
        test_name: "Slalom",
        display_name: "Slalom · appuis alternés",
        summary: `Score ${game.score} · niveau ${levels[game.level].label}`,
        csv_path: game.csvPath,
      }),
    });
  } catch (_) {
    game.saved = false;
  }
}

function drawTrack(width, height) {
  ctx.fillStyle = "#172629";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#24383c";
  ctx.fillRect(width * 0.12, 0, width * 0.76, height);
  ctx.strokeStyle = "rgba(255,255,255,.14)";
  ctx.lineWidth = 2;
  ctx.setLineDash([18, 18]);
  for (let index = 1; index < 4; index += 1) {
    const x = width * (0.12 + index * 0.19);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

function gateCenterFor(index) {
  const wave = Math.sin(index * 1.35) * 0.24;
  const alternate = index % 2 === 0 ? -0.08 : 0.08;
  return Math.max(0.28, Math.min(0.72, 0.5 + wave + alternate));
}

function spawnGates(now) {
  const level = levels[game.level];
  if (!game.playing || now - game.lastGateAt <= level.gateEveryMs) return;
  game.lastGateAt = now;
  const index = game.gates.length + game.score;
  game.gates.push({
    center: gateCenterFor(index),
    y: -55,
    checked: false,
  });
}

function drawGates(width, height, dt) {
  const level = levels[game.level];
  const speed = (210 + Math.min(180, game.score * 4)) * level.speedFactor;
  const playerY = height * playerYRatio;
  game.gates.forEach((gate) => {
    if (game.playing) {
      gate.y += speed * dt;
    }
    const gapHalfWidth = width * level.gateWidth * 0.5;
    const centerX = width * gate.center;
    const leftPole = centerX - gapHalfWidth;
    const rightPole = centerX + gapHalfWidth;
    ctx.fillStyle = "#df7b37";
    ctx.beginPath();
    ctx.roundRect(leftPole - 14, gate.y - 34, 28, 68, 8);
    ctx.roundRect(rightPole - 14, gate.y - 34, 28, 68, 8);
    ctx.fill();
    ctx.strokeStyle = "rgba(223,123,55,.35)";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(leftPole + 16, gate.y);
    ctx.lineTo(rightPole - 16, gate.y);
    ctx.stroke();

    if (!gate.checked && gate.y > playerY) {
      gate.checked = true;
      const playerX = width * game.x;
      if (playerX > leftPole && playerX < rightPole) {
        game.score += 1;
        message("Porte franchie.", false, true);
      } else {
        game.score = Math.max(0, game.score - 2);
        message("Porte manquée : ajustez le transfert d’appui.", true);
      }
      $("scoreValue").textContent = String(game.score);
    }
  });
  game.gates = game.gates.filter((gate) => gate.y < height + 90);
}

function drawPlayer(width, height) {
  if (game.running) {
    game.x += (game.targetX - game.x) * levels[game.level].steeringEase;
  }
  game.x = Math.max(0.18, Math.min(0.82, game.x));
  const x = width * game.x;
  const y = height * playerYRatio;
  ctx.fillStyle = "#147c75";
  ctx.beginPath();
  ctx.arc(x, y, 28, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#dff4ec";
  ctx.beginPath();
  ctx.arc(x, y, 12, 0, Math.PI * 2);
  ctx.fill();
}

function draw() {
  const now = performance.now();
  const dt = Math.min(0.05, (now - game.lastFrameAt) / 1000);
  game.lastFrameAt = now;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, rect.width * dpr);
  canvas.height = Math.max(1, rect.height * dpr);
  ctx.scale(dpr, dpr);
  const width = rect.width;
  const height = rect.height;

  drawTrack(width, height);
  spawnGates(now);
  drawGates(width, height, dt);
  drawPlayer(width, height);

  if (game.playing && game.startedAt) {
    const elapsed = (now - game.startedAt) / 1000;
    if (elapsed >= gameDurationSeconds) {
      finishGameAndStopAcquisition();
    }
    const displayedElapsed = Math.min(elapsed, gameDurationSeconds);
    const minutes = Math.floor(displayedElapsed / 60).toString().padStart(2, "0");
    const seconds = Math.floor(displayedElapsed % 60).toString().padStart(2, "0");
    $("gameTimer").textContent = `${minutes}:${seconds}`;
  }

  requestAnimationFrame(draw);
}

$("connectButton").addEventListener("click", connect);
$("startGameButton").addEventListener("click", startGame);
$("stopGameButton").addEventListener("click", stopGame);
document.querySelectorAll(".connect-action").forEach((button) => {
  button.addEventListener("click", connect);
});
document.querySelectorAll(".start-action").forEach((button) => {
  button.addEventListener("click", startGame);
});
document.querySelectorAll(".stop-action").forEach((button) => {
  button.addEventListener("click", stopGame);
});
document.querySelectorAll(".level-choice").forEach((button) => {
  button.addEventListener("click", () => {
    if (game.running) return;
    game.level = button.dataset.level;
    document.querySelectorAll(".level-choice").forEach((choice) => {
      choice.classList.toggle("active", choice === button);
    });
    $("sensitivityLabel").textContent = levels[game.level].description;
  });
});

if (!CanvasRenderingContext2D.prototype.roundRect) {
  CanvasRenderingContext2D.prototype.roundRect = function (x, y, w, h, r) {
    this.moveTo(x + r, y);
    this.arcTo(x + w, y, x + w, y + h, r);
    this.arcTo(x + w, y + h, x, y + h, r);
    this.arcTo(x, y + h, x, y, r);
    this.arcTo(x, y, x + w, y, r);
    return this;
  };
}

poll();
setInterval(poll, 180);
requestAnimationFrame(draw);
