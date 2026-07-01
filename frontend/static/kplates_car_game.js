const $ = (id) => document.getElementById(id);

const canvas = $("carCanvas");
const ctx = canvas.getContext("2d");
const params = new URLSearchParams(window.location.search);
const flowContext = params.get("context") || "anonymous";
const patientId = flowContext.startsWith("patient:")
  ? Number(flowContext.split(":")[1])
  : null;

const game = {
  running: false,
  playing: false,
  level: "easy",
  lane: 2,
  targetLane: 2,
  score: 0,
  startedAt: null,
  lastFrameAt: performance.now(),
  lastObstacleAt: 0,
  obstacles: [],
  direction: "center",
  latestMeasurement: null,
  lastMeasurementTimestamp: null,
  saved: false,
  csvPath: null,
};

const laneCount = 5;
const laneMin = 0.17;
const laneMax = 0.83;
const lanes = Array.from({ length: laneCount }, (_, index) =>
  laneMin + ((laneMax - laneMin) * index) / (laneCount - 1)
);
const carYRatio = 0.94;
const movementThreshold = 10;
const fullSteeringAsymmetry = 45;
const levels = {
  easy: {
    label: "Facile",
    description: "Déclenchement léger · vitesse lente",
    speedFactor: 0.55,
    steeringEase: 0.10,
  },
  medium: {
    label: "Moyen",
    description: "Déclenchement léger · vitesse modérée",
    speedFactor: 0.75,
    steeringEase: 0.14,
  },
  expert: {
    label: "Expert",
    description: "Déclenchement léger · vitesse rapide",
    speedFactor: 1,
    steeringEase: 0.18,
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
    game.lastObstacleAt = performance.now();
    message("Jeu lancé : évitez les obstacles.", false, true);
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
  game.targetLane = ((normalizedSteering + 1) / 2) * (laneCount - 1);
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
  game.lastObstacleAt = 0;
  game.obstacles = [];
  game.lane = (laneCount - 1) / 2;
  game.targetLane = (laneCount - 1) / 2;
  game.playing = false;
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
        duration: 60,
        mode: "balance",
        recalibrate: false,
        filename: `kplates_voiture_${game.level}_${new Date().toISOString().replace(/[:.]/g, "-")}.csv`,
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
  message(`Jeu terminé. Score : ${game.score}.`, false, true);
  saveTrainingSummary();
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
        test_name: "Voiture",
        display_name: "Voiture · évitement",
        summary: `Score ${game.score} · niveau ${levels[game.level].label}`,
        csv_path: game.csvPath,
      }),
    });
  } catch (_) {
    game.saved = false;
  }
}

function drawRoad(width, height) {
  ctx.fillStyle = "#172629";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#24383c";
  const roadLeft = width * 0.10;
  const roadWidth = width * 0.80;
  ctx.fillRect(roadLeft, 0, roadWidth, height);
  ctx.strokeStyle = "rgba(255,255,255,.28)";
  ctx.lineWidth = 3;
  for (let index = 1; index < laneCount; index += 1) {
    const xRatio = laneMin + ((laneMax - laneMin) * (index - 0.5)) / (laneCount - 1);
    ctx.setLineDash([24, 18]);
    ctx.beginPath();
    ctx.moveTo(width * xRatio, 0);
    ctx.lineTo(width * xRatio, height);
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

function drawCar(width, height) {
  game.lane += (game.targetLane - game.lane) * levels[game.level].steeringEase;
  const laneIndex = Math.max(0, Math.min(laneCount - 1, game.lane));
  const x = width * lanes[0] + (width * (lanes[laneCount - 1] - lanes[0]) / (laneCount - 1)) * laneIndex;
  const y = height * carYRatio;
  ctx.fillStyle = "#147c75";
  ctx.beginPath();
  ctx.roundRect(x - 22, y - 36, 44, 72, 10);
  ctx.fill();
  ctx.fillStyle = "#dff4ec";
  ctx.beginPath();
  ctx.roundRect(x - 14, y - 22, 28, 20, 6);
  ctx.fill();
}

function drawObstacles(width, height, dt) {
  if (game.playing && performance.now() - game.lastObstacleAt > 950) {
    game.lastObstacleAt = performance.now();
    game.obstacles.push({
      lane: Math.floor(Math.random() * laneCount),
      y: -80,
      counted: false,
    });
  }
  const level = levels[game.level];
  const speed = (250 + Math.min(220, game.score * 5)) * level.speedFactor;
  const carLane = Math.round(game.lane);
  game.obstacles.forEach((obstacle) => {
    obstacle.y += speed * dt;
    const x = width * lanes[obstacle.lane];
    ctx.fillStyle = "#df7b37";
    ctx.beginPath();
    ctx.roundRect(x - 20, obstacle.y - 32, 40, 64, 10);
    ctx.fill();
    if (!obstacle.counted && obstacle.y > height * carYRatio) {
      obstacle.counted = true;
      if (obstacle.lane === carLane) {
        game.score = Math.max(0, game.score - 3);
        message("Obstacle touché : transférez plus vite l’appui.", true);
      } else {
        game.score += 1;
        message("Bien joué, obstacle évité.", false, true);
      }
      $("scoreValue").textContent = String(game.score);
    }
  });
  game.obstacles = game.obstacles.filter((obstacle) => obstacle.y < height + 90);
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

  drawRoad(width, height);
  drawObstacles(width, height, dt);
  drawCar(width, height);

  if (game.playing && game.startedAt) {
    const elapsed = (now - game.startedAt) / 1000;
    const minutes = Math.floor(elapsed / 60).toString().padStart(2, "0");
    const seconds = Math.floor(elapsed % 60).toString().padStart(2, "0");
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
    const level = levels[game.level];
    $("sensitivityLabel").textContent =
      `${level.description} · seuil ${movementThreshold} %`;
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
