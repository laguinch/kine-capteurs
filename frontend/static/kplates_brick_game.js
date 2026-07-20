const $ = (id) => document.getElementById(id);

const canvas = $("brickCanvas");
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
  paddleX: 0.5,
  targetPaddleX: 0.5,
  misses: 0,
  startedAt: null,
  lastFrameAt: performance.now(),
  direction: "center",
  latestMeasurement: null,
  lastMeasurementTimestamp: null,
  saved: false,
  csvPath: null,
  completed: false,
  connecting: false,
  bricks: [],
  ball: { x: 0.5, y: 0.72, vx: 0.34, vy: -0.45 },
};

const movementThreshold = 10;
const fullSteeringAsymmetry = 45;
const levels = {
  easy: {
    label: "Facile",
    description: "Déclenchement léger · raquette large · vitesse lente",
    speedFactor: 0.78,
    steeringEase: 0.12,
    paddleWidth: 0.24,
  },
  medium: {
    label: "Moyen",
    description: "Déclenchement léger · raquette moyenne · vitesse modérée",
    speedFactor: 0.92,
    steeringEase: 0.15,
    paddleWidth: 0.20,
  },
  expert: {
    label: "Expert",
    description: "Déclenchement léger · raquette courte · vitesse rapide",
    speedFactor: 1.08,
    steeringEase: 0.18,
    paddleWidth: 0.16,
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
  void text;
  void error;
  void ready;
}

function setLevelControlsDisabled(disabled) {
  document.querySelectorAll(".level-choice").forEach((button) => {
    button.disabled = disabled;
  });
}

function bothPlatformsConnected(data) {
  const sides = Array.isArray(data.connected_sides) ? data.connected_sides : [];
  return sides.includes("gauche") && sides.includes("droite");
}

function platformsReady(data) {
  return data.worker_phase === "idle" && !data.last_error && bothPlatformsConnected(data);
}

function waitingForInitialMeasurements(data) {
  return (
    bothPlatformsConnected(data)
    && typeof data.last_error === "string"
    && data.last_error.includes("aucune mesure initiale reçue")
  );
}

function syncAvailabilityMessage(data, ready, running) {
  void data;
  void ready;
  void running;
}

function updateStatus(data) {
  const connected = Boolean(data.bluetooth_connected);
  const ready = platformsReady(data);
  const running = Boolean(data.running);
  const serverConnecting = data.worker_phase === "connecting";
  const connecting = game.connecting;
  const waitingForMeasures = waitingForInitialMeasurements(data);
  $("gameStatusDot").className = `status-dot ${running || ready || connecting || serverConnecting || waitingForMeasures ? "running" : data.last_error ? "error" : ""}`;
  $("gameStatusText").textContent = running
    ? "Jeu en cours"
    : connecting || serverConnecting
      ? "Connexion des plateformes"
    : ready
      ? "Plateformes connectées"
      : waitingForMeasures
        ? "Mesures en attente"
      : connected
        ? "Connexion partielle"
      : data.last_error
        ? "Erreur"
        : "Déconnecté";
  $("connectButton").disabled = connecting || running;
  $("startGameButton").disabled = connecting || running || !ready;
  $("stopGameButton").disabled = !running;
  document.querySelectorAll(".connect-action").forEach((button) => {
    button.disabled = connecting || running;
  });
  document.querySelectorAll(".start-action").forEach((button) => {
    button.disabled = connecting || running || !ready;
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
  syncAvailabilityMessage(data, ready, running);
}

function updateControls(measurement) {
  const totalKg = Number.isFinite(measurement.total_kg) ? measurement.total_kg : 0;
  const leftKg = Number.isFinite(measurement.left_kg) ? measurement.left_kg : 0;
  const rightKg = Number.isFinite(measurement.right_kg) ? measurement.right_kg : 0;
  const hasBipodalMeasurement =
    Number.isFinite(measurement.left_kg)
    && Number.isFinite(measurement.right_kg)
    && Number.isFinite(measurement.total_kg)
    && totalKg > 0;
  if (
    game.running
    && !game.playing
    && hasBipodalMeasurement
    && measurement.timestamp_utc
    && measurement.timestamp_utc !== game.lastMeasurementTimestamp
  ) {
    game.playing = true;
    game.startedAt = performance.now();
    $("gameStatusText").textContent = "Casse-brique en cours";
  } else if (game.running && !game.playing) {
    $("gameStatusText").textContent = "Attente des mesures des plateformes";
  }
  game.lastMeasurementTimestamp = measurement.timestamp_utc || null;

  const left = Number.isFinite(measurement.left_pct)
    ? measurement.left_pct
    : totalKg > 0
      ? (leftKg / totalKg) * 100
      : 0;
  const right = Number.isFinite(measurement.right_pct)
    ? measurement.right_pct
    : totalKg > 0
      ? (rightKg / totalKg) * 100
      : 0;
  const asymmetry = Number.isFinite(measurement.asymmetry_pct)
    ? measurement.asymmetry_pct
    : totalKg > 0
      ? ((rightKg - leftKg) / totalKg) * 100
      : 0;
  $("leftForceBar").style.width = `${Math.max(0, Math.min(100, left))}%`;
  $("rightForceBar").style.width = `${Math.max(0, Math.min(100, right))}%`;
  $("leftForceText").textContent = `${format(left)} %`;
  $("rightForceText").textContent = `${format(right)} %`;

  const normalizedSteering = Math.max(
    -1,
    Math.min(1, asymmetry / fullSteeringAsymmetry)
  );
  game.targetPaddleX = 0.5 + normalizedSteering * 0.34;
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
  game.connecting = true;
  $("gameStatusText").textContent = "Connexion des plateformes";
  $("gameStatusDot").className = "status-dot running";
  $("connectButton").disabled = true;
  $("startGameButton").disabled = true;
  document.querySelectorAll(".connect-action").forEach((button) => {
    button.disabled = true;
  });
  document.querySelectorAll(".start-action").forEach((button) => {
    button.disabled = true;
  });
  try {
    const response = await fetch("/api/kplates/dual/connect", { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Connexion impossible");
    game.connecting = false;
    updateStatus(data);
  } catch (error) {
    game.connecting = false;
    message(error.message, true);
    poll();
  }
}

function buildBricks() {
  const bricks = [];
  const rows = 5;
  const columns = 8;
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      bricks.push({
        row,
        column,
        alive: true,
        x: 0.12 + column * 0.095,
        y: 0.10 + row * 0.055,
        w: 0.078,
        h: 0.035,
      });
    }
  }
  return bricks;
}

function resetBall() {
  game.ball = {
    x: 0.5,
    y: 0.72,
    vx: Math.random() > 0.5 ? 0.34 : -0.34,
    vy: -0.45,
  };
}

async function startGame() {
  game.misses = 0;
  game.startedAt = null;
  game.bricks = buildBricks();
  resetBall();
  game.paddleX = 0.5;
  game.targetPaddleX = 0.5;
  game.playing = false;
  game.lastMeasurementTimestamp = null;
  game.saved = false;
  game.completed = false;
  game.csvPath = null;
  $("missValue").textContent = "0";
  $("gameStatusText").textContent = "Attente des mesures des plateformes";
  try {
    const response = await fetch("/api/kplates/dual/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        duration: 600,
        mode: "balance",
        recalibrate: false,
        filename: `kplates_casse_brique_${game.level}_${new Date().toISOString().replace(/[:.]/g, "-")}.csv`,
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

async function completeGame() {
  if (!game.running || game.completed) return;
  game.completed = true;
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
  $("gameStatusDot").className = "status-dot running";
  $("gameStatusText").textContent = game.completed ? "Casse-brique terminé" : "Jeu arrêté";
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
        test_name: "Casse-brique",
        display_name: "Casse-brique · contrôle latéral",
        summary: `Échecs ${game.misses} · niveau ${levels[game.level].label}`,
        csv_path: game.csvPath,
      }),
    });
  } catch (_) {
    game.saved = false;
  }
}

function drawCourt(width, height) {
  ctx.fillStyle = "#172629";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#24383c";
  const playLeft = width * 0.10;
  const playWidth = width * 0.80;
  ctx.fillRect(playLeft, 0, playWidth, height);
  ctx.strokeStyle = "rgba(255,255,255,.18)";
  ctx.lineWidth = 2;
  ctx.setLineDash([18, 18]);
  for (let index = 1; index < 5; index += 1) {
    const x = playLeft + (playWidth * index) / 5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

function drawBricks(width, height) {
  game.bricks.forEach((brick) => {
    if (!brick.alive) return;
    const x = width * brick.x;
    const y = height * brick.y;
    const w = width * brick.w;
    const h = height * brick.h;
    const colors = ["#df7b37", "#e39b47", "#147c75", "#3977a9", "#0d5854"];
    ctx.fillStyle = colors[brick.row % colors.length];
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 6);
    ctx.fill();
  });
}

function drawPaddle(width, height) {
  const level = levels[game.level];
  game.paddleX += (game.targetPaddleX - game.paddleX) * level.steeringEase;
  game.paddleX = Math.max(0.14, Math.min(0.86, game.paddleX));
  const paddleWidth = width * level.paddleWidth;
  const paddleHeight = 18;
  const x = width * game.paddleX - paddleWidth / 2;
  const y = height * 0.90;
  ctx.fillStyle = "#147c75";
  ctx.beginPath();
  ctx.roundRect(x, y, paddleWidth, paddleHeight, 999);
  ctx.fill();
  return { x, y, width: paddleWidth, height: paddleHeight };
}

function updateBall(width, height, dt, paddle) {
  const level = levels[game.level];
  const ball = game.ball;
  if (game.playing) {
    ball.x += ball.vx * level.speedFactor * dt;
    ball.y += ball.vy * level.speedFactor * dt;
  }

  const radiusX = 10 / width;
  const radiusY = 10 / height;
  if (ball.x < 0.11 + radiusX || ball.x > 0.89 - radiusX) {
    ball.vx *= -1;
    ball.x = Math.max(0.11 + radiusX, Math.min(0.89 - radiusX, ball.x));
  }
  if (ball.y < 0.03 + radiusY) {
    ball.vy = Math.abs(ball.vy);
  }
  if (ball.y > 0.98) {
    game.misses += 1;
    $("missValue").textContent = String(game.misses);
    resetBall();
  }

  const ballX = width * ball.x;
  const ballY = height * ball.y;
  if (
    ball.vy > 0
    && ballX > paddle.x
    && ballX < paddle.x + paddle.width
    && ballY + 10 > paddle.y
    && ballY < paddle.y + paddle.height
  ) {
    const impact = (ballX - (paddle.x + paddle.width / 2)) / (paddle.width / 2);
    ball.vx = impact * 0.48;
    ball.vy = -Math.abs(ball.vy);
    ball.y = (paddle.y - 12) / height;
  }

  game.bricks.forEach((brick) => {
    if (!brick.alive) return;
    const x = width * brick.x;
    const y = height * brick.y;
    const w = width * brick.w;
    const h = height * brick.h;
    if (ballX > x && ballX < x + w && ballY > y && ballY < y + h) {
      brick.alive = false;
      ball.vy *= -1;
    }
  });

  if (game.playing && game.bricks.every((brick) => !brick.alive)) {
    completeGame();
  }
}

function drawBall(width, height) {
  ctx.fillStyle = "#dff4ec";
  ctx.beginPath();
  ctx.arc(width * game.ball.x, height * game.ball.y, 10, 0, Math.PI * 2);
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

  drawCourt(width, height);
  drawBricks(width, height);
  const paddle = drawPaddle(width, height);
  updateBall(width, height, dt, paddle);
  drawBall(width, height);

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

game.bricks = buildBricks();
poll();
setInterval(poll, 180);
requestAnimationFrame(draw);
