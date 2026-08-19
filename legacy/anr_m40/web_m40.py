import asyncio
from bleak import BleakClient, BleakScanner
from aiohttp import web

M40_NAME = "ANR Corp M40"
M40_ADDRESS = "68:23:B0:B6:AF:F3"

EMG_UUID = "00002a58-0000-1000-8000-00805f9b34fb"
BAT_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

state = {
    "connected": False,
    "emg": 0,
    "battery": 0,
    "baseline": 870,
    "max_value": 930,
    "target": 0.5,
}

def handle_emg(sender, data):
    try:
        state["emg"] = int.from_bytes(data, byteorder="little")
    except Exception as e:
        print("Erreur notify EMG:", e)

async def find_m40():
    print("Scan BLE 8 s...")
    try:
        devices = await BleakScanner.discover(timeout=8.0, return_adv=True)
    except Exception as e:
        print("Erreur scan BLE:", e)
        return None

    for _, (device, adv) in devices.items():
        name = device.name or adv.local_name
        print(f"Vu: {device.address} - {name}")

        if name and M40_NAME in name:
            print(f"M40 trouvé par nom: {device.address}")
            return device

        md = adv.manufacturer_data or {}
        if 0x05DA in md:
            print(f"M40 trouvé par manufacturer data: {device.address}")
            return device

        if device.address.upper() == M40_ADDRESS:
            print(f"M40 trouvé par adresse connue: {device.address}")
            return device

    print("M40 non trouvé")
    return None

async def safe_read_battery(client):
    try:
        battery = await client.read_gatt_char(BAT_UUID)
        state["battery"] = int.from_bytes(battery, "little")
        print("Batterie:", state["battery"], "%")
    except Exception as e:
        print("Lecture batterie ignorée:", e)

async def ble_loop():
    while True:
        try:
            device = await find_m40()
            if not device:
                state["connected"] = False
                await asyncio.sleep(2)
                continue

            print("Connexion au M40...")
            async with BleakClient(device, timeout=15.0) as client:
                state["connected"] = client.is_connected
                print("Connecté:", client.is_connected)

                if not client.is_connected:
                    await asyncio.sleep(2)
                    continue

                await asyncio.sleep(0.3)
                await safe_read_battery(client)
                await asyncio.sleep(0.2)

                print("Activation notify EMG...")
                await client.start_notify(EMG_UUID, handle_emg)
                print("Notify EMG actif")

                while client.is_connected:
                    state["connected"] = True
                    await asyncio.sleep(1)

        except Exception as e:
            print("Erreur BLE principale:", e)

        state["connected"] = False
        await asyncio.sleep(2)

async def api_data(request):
    return web.json_response(state)

async def set_calibration(request):
    try:
        data = await request.json()
        if "baseline" in data:
            state["baseline"] = int(data["baseline"])
        if "max_value" in data:
            state["max_value"] = int(data["max_value"])
        if "target" in data:
            state["target"] = float(data["target"])
        return web.json_response({"ok": True, "state": state})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)

HTML_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>M40 EMG Biofeedback</title>
<style>
body {
  font-family: Arial, sans-serif;
  text-align: center;
  background: #f5f5f5;
  margin: 0;
  padding: 16px;
}
h1 { margin-top: 0; }
#status { font-size: 22px; margin-bottom: 10px; }
#emg { font-size: 56px; font-weight: bold; margin: 10px 0; }
#battery { font-size: 24px; margin-bottom: 12px; }
.panel {
  background: white;
  border-radius: 12px;
  padding: 14px;
  margin: 12px auto;
  max-width: 1100px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
  align-items: center;
}
button, input[type="range"], input[type="number"] {
  font-size: 18px;
}
button {
  padding: 10px 14px;
  border: none;
  border-radius: 10px;
  background: #1f6feb;
  color: white;
  cursor: pointer;
}
button.secondary { background: #666; }
button.stop { background: #b00020; }
.metric {
  font-size: 22px;
  margin: 6px 0;
}
canvas {
  border: 2px solid #222;
  background: white;
  width: 95%;
  max-width: 1050px;
  margin-top: 10px;
}
#bioCanvas { height: 280px; }
#gameCanvas { height: 380px; }
.small {
  font-size: 16px;
  color: #555;
}
.paramBox {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 6px 10px;
}
.paramBox input {
  width: 90px;
  padding: 6px;
}
#phaseDisplay {
  font-size: 30px;
  font-weight: bold;
  margin-top: 10px;
  min-height: 36px;
}
</style>
</head>
<body>
<h1>M40 EMG</h1>

<div class="panel">
  <div id="status">Connexion...</div>
  <div id="emg">0</div>
  <div id="battery">Batterie: 0%</div>
  <div class="metric">
    Repos: <span id="baselineDisplay">0</span> |
    Max: <span id="maxDisplay">0</span> |
    Cible: <span id="targetDisplay">0</span>%
  </div>
</div>

<div class="panel">
  <h2>Calibration</h2>
  <div class="controls">
    <button onclick="calibrateBaseline()">Calibrer repos</button>
    <button onclick="calibrateMax()">Calibrer max</button>
    <button class="secondary" onclick="resetSession()">Reset session</button>
  </div>
  <div class="small">Repos : relâché 2 s. Max : contraction forte 3 s.</div>
</div>

<div class="panel">
  <h2>Objectif / Biofeedback</h2>
  <div class="controls">
    <label for="targetRange">Cible</label>
    <input id="targetRange" type="range" min="10" max="95" value="50" oninput="setTargetFromSlider(this.value)">
    <span id="targetLabel">50%</span>
  </div>
  <canvas id="bioCanvas" width="1050" height="280"></canvas>
</div>

<div class="panel">
  <h2>Jeu à obstacles</h2>
  <div class="controls">
    <div class="paramBox">
      <label for="obstacleHeight">Hauteur (%)</label>
      <input id="obstacleHeight" type="number" min="10" max="100" step="1" value="90">
    </div>
    <div class="paramBox">
      <label for="obstacleDuration">Contraction (s)</label>
      <input id="obstacleDuration" type="number" min="1" max="20" step="0.5" value="5">
    </div>
    <div class="paramBox">
      <label for="restDuration">Repos inter-rép (s)</label>
      <input id="restDuration" type="number" min="1" max="60" step="1" value="5">
    </div>
    <div class="paramBox">
      <label for="restSeriesDuration">Repos inter-série (s)</label>
      <input id="restSeriesDuration" type="number" min="1" max="120" step="1" value="15">
    </div>
    <div class="paramBox">
      <label for="repetitions">Répétitions</label>
      <input id="repetitions" type="number" min="1" max="50" step="1" value="8">
    </div>
    <div class="paramBox">
      <label for="seriesCount">Séries</label>
      <input id="seriesCount" type="number" min="1" max="20" step="1" value="3">
    </div>
    <div class="paramBox">
      <label for="slopeAngle">Pente (°)</label>
      <input id="slopeAngle" type="number" min="30" max="85" step="5" value="60">
    </div>
    <button onclick="startProtocol()">Démarrer</button>
    <button class="stop" onclick="stopProtocol()">Stop</button>
  </div>

  <div class="metric">
    Série: <span id="seriesDisplay">0</span>/<span id="seriesTotalDisplay">0</span> |
    Répétition: <span id="repDisplay">0</span>/<span id="repTotalDisplay">0</span> |
    Réussites: <span id="score">0</span>
  </div>

  <div id="phaseDisplay">Prêt</div>
  <div class="metric">
    Temps restant: <span id="timerDisplay">0.0</span> s |
    Niveau actuel: <span id="levelDisplay">0</span>%
  </div>
  <div class="small">
    Boule plus petite. Max réel = 1, dépassement jusqu’à 1.2. Obstacles recalés avec la même marge.
  </div>

  <canvas id="gameCanvas" width="1050" height="380"></canvas>
</div>

<script>
let emg = 0;
let connected = false;
let battery = 0;
let baseline = 870;
let maxValue = 930;
let target = 0.5;
let samples = [];
let score = 0;

const playerX = 120;
const playerR = 10;
const timeScale = 120;
const ceilingMargin = 1.2;

let birdY = 350;
let birdV = 0;

const protocol = {
  running: false,
  obstacleHeightPercent: 0.9,
  contractionSec: 5,
  restSec: 5,
  restSeriesSec: 15,
  repsTotal: 8,
  seriesTotal: 3,
  slopeAngleDeg: 60,
  totalDuration: 0,
  elapsed: 0,
  currentSeries: 0,
  currentRep: 0,
  timeline: []
};

async function fetchData() {
  try {
    const r = await fetch('/data');
    const j = await r.json();

    emg = j.emg;
    connected = j.connected;
    battery = j.battery;
    baseline = j.baseline;
    maxValue = j.max_value;
    target = j.target;

    document.getElementById("status").textContent = connected ? "M40 connecté" : "M40 non connecté";
    document.getElementById("emg").textContent = emg;
    document.getElementById("battery").textContent = "Batterie: " + battery + "%";
    document.getElementById("baselineDisplay").textContent = baseline;
    document.getElementById("maxDisplay").textContent = maxValue;
    document.getElementById("targetDisplay").textContent = Math.round(target * 100);
    document.getElementById("targetLabel").textContent = Math.round(target * 100) + "%";
    document.getElementById("targetRange").value = Math.round(target * 100);

    samples.push(emg);
    if (samples.length > 120) samples.shift();
  } catch(e) {
    document.getElementById("status").textContent = "Erreur serveur";
  }
}
setInterval(fetchData, 100);

function normalizedEmg() {
  let denom = maxValue - baseline;
  if (denom < 5) denom = 5;

  let n = (emg - baseline) / denom;

  if (n < 0) n = 0;
  if (n > 1.2) n = 1.2;

  return n;
}

function normalizedSample(value) {
  let denom = maxValue - baseline;
  if (denom < 5) denom = 5;

  let n = (value - baseline) / denom;

  if (n < 0) n = 0;
  if (n > 1.2) n = 1.2;

  return n;
}

async function saveCalibration(payload) {
  await fetch('/set_calibration', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
}

async function calibrateBaseline() {
  let vals = [];
  for (let i = 0; i < 20; i++) {
    vals.push(emg);
    await new Promise(r => setTimeout(r, 100));
  }
  const avg = Math.round(vals.reduce((a, b) => a + b, 0) / vals.length);
  await saveCalibration({ baseline: avg });
}

async function calibrateMax() {
  let vals = [];
  for (let i = 0; i < 30; i++) {
    vals.push(emg);
    await new Promise(r => setTimeout(r, 100));
  }
  const maxV = Math.max(...vals);
  await saveCalibration({ max_value: maxV });
}

async function setTargetFromSlider(v) {
  const t = Number(v) / 100;
  await saveCalibration({ target: t });
}

const bioCanvas = document.getElementById("bioCanvas");
const bioCtx = bioCanvas.getContext("2d");

function drawBiofeedback() {
  bioCtx.clearRect(0, 0, bioCanvas.width, bioCanvas.height);

  const n = normalizedEmg();
  const barX = 150;
  const barY = 30;
  const barW = 120;
  const barH = 220;

  const targetH = 24;
  const targetY = barY + (1 - target) * barH - targetH / 2;
  bioCtx.fillStyle = "#cfeecf";
  bioCtx.fillRect(barX, targetY, barW, targetH);

  bioCtx.strokeStyle = "#222";
  bioCtx.strokeRect(barX, barY, barW, barH);

  const fillH = Math.min(n / ceilingMargin, 1) * barH;
  bioCtx.fillStyle = "#1f6feb";
  bioCtx.fillRect(barX, barY + barH - fillH, barW, fillH);

  bioCtx.fillStyle = "#000";
  bioCtx.font = "24px Arial";
  bioCtx.fillText("Niveau EMG", 110, 20);
  bioCtx.fillText(Math.round(n * 100) + "%", 180, 270);

  const gx = 350;
  const gy = 30;
  const gw = 620;
  const gh = 220;
  bioCtx.strokeRect(gx, gy, gw, gh);

  if (samples.length > 1) {
    bioCtx.beginPath();
    for (let i = 0; i < samples.length; i++) {
      let x = gx + (i / (samples.length - 1)) * gw;
      let sn = Math.min(normalizedSample(samples[i]) / ceilingMargin, 1);
      let y = gy + gh - sn * gh;
      if (i === 0) bioCtx.moveTo(x, y);
      else bioCtx.lineTo(x, y);
    }
    bioCtx.stroke();
  }

  bioCtx.fillText("Courbe temps réel", 590, 20);
}

function makeSegment(type, duration, series, rep, id) {
  return {
    type,
    duration,
    series,
    rep,
    id,
    success: true
  };
}

function buildTimeline() {
  const tl = [];
  let id = 0;

  tl.push(makeSegment("rest", protocol.restSec, 1, 0, id++));

  for (let s = 1; s <= protocol.seriesTotal; s++) {
    for (let r = 1; r <= protocol.repsTotal; r++) {
      tl.push(makeSegment("contraction", protocol.contractionSec, s, r, id++));
      if (r < protocol.repsTotal) {
        tl.push(makeSegment("rest", protocol.restSec, s, r, id++));
      }
    }
    if (s < protocol.seriesTotal) {
      tl.push(makeSegment("rest_series", protocol.restSeriesSec, s, protocol.repsTotal, id++));
    }
  }

  return tl;
}

function startProtocol() {
  protocol.obstacleHeightPercent = Number(document.getElementById("obstacleHeight").value) / 100;
  protocol.contractionSec = Number(document.getElementById("obstacleDuration").value);
  protocol.restSec = Number(document.getElementById("restDuration").value);
  protocol.restSeriesSec = Number(document.getElementById("restSeriesDuration").value);
  protocol.repsTotal = Number(document.getElementById("repetitions").value);
  protocol.seriesTotal = Number(document.getElementById("seriesCount").value);
  protocol.slopeAngleDeg = Number(document.getElementById("slopeAngle").value);

  protocol.timeline = buildTimeline();
  protocol.totalDuration = protocol.timeline.reduce((acc, seg) => acc + seg.duration, 0);
  protocol.elapsed = 0;
  protocol.running = true;
  protocol.currentSeries = 1;
  protocol.currentRep = 0;

  score = 0;
  birdY = 350;
  birdV = 0;

  updateProtocolDisplay();
}

function stopProtocol() {
  protocol.running = false;
  updateProtocolDisplay();
}

function resetSession() {
  protocol.running = false;
  protocol.timeline = [];
  protocol.totalDuration = 0;
  protocol.elapsed = 0;
  protocol.currentSeries = 0;
  protocol.currentRep = 0;
  score = 0;
  birdY = 350;
  birdV = 0;
  updateProtocolDisplay();
}

function getCurrentSegmentInfo() {
  if (!protocol.timeline.length) return null;
  let t = 0;
  for (const seg of protocol.timeline) {
    const start = t;
    const end = t + seg.duration;
    if (protocol.elapsed >= start && protocol.elapsed < end) {
      return {
        seg,
        start,
        end,
        remaining: end - protocol.elapsed
      };
    }
    t = end;
  }
  return null;
}

function updateProtocolDisplay() {
  const info = getCurrentSegmentInfo();

  if (!protocol.running || !info) {
    document.getElementById("seriesDisplay").textContent = protocol.currentSeries;
    document.getElementById("seriesTotalDisplay").textContent = protocol.seriesTotal;
    document.getElementById("repDisplay").textContent = protocol.currentRep;
    document.getElementById("repTotalDisplay").textContent = protocol.repsTotal;
    document.getElementById("timerDisplay").textContent = "0.0";
    document.getElementById("score").textContent = score;
    document.getElementById("phaseDisplay").textContent = protocol.timeline.length && !protocol.running ? "Terminé" : "Prêt";
    return;
  }

  protocol.currentSeries = info.seg.series;
  protocol.currentRep = info.seg.rep;

  document.getElementById("seriesDisplay").textContent = info.seg.series;
  document.getElementById("seriesTotalDisplay").textContent = protocol.seriesTotal;
  document.getElementById("repDisplay").textContent = info.seg.rep;
  document.getElementById("repTotalDisplay").textContent = protocol.repsTotal;
  document.getElementById("timerDisplay").textContent = info.remaining.toFixed(1);
  document.getElementById("score").textContent = score;

  let phaseText = "";
  if (info.seg.type === "contraction") phaseText = "Contracter";
  document.getElementById("phaseDisplay").textContent = phaseText;
}

function topYForObstacleAtX(obstacleX, obstacleW, obstacleH, queryX) {
  const obstacleRight = obstacleX + obstacleW;
  if (queryX < obstacleX || queryX > obstacleRight) return null;

  const floorY = gameCanvas.height;
  const topY = floorY - obstacleH;

  const angleRad = protocol.slopeAngleDeg * Math.PI / 180;
  let ramp = obstacleH / Math.tan(angleRad);
  ramp = Math.min(ramp, obstacleW / 2 - 1);
  if (ramp < 1) return topY;

  const flatStart = obstacleX + ramp;
  const flatEnd = obstacleRight - ramp;

  if (queryX <= flatStart) {
    const p = (queryX - obstacleX) / ramp;
    return floorY - obstacleH * p;
  }

  if (queryX >= flatEnd) {
    const p = (queryX - flatEnd) / ramp;
    return topY + obstacleH * p;
  }

  return topY;
}

const gameCanvas = document.getElementById("gameCanvas");
const ctxGame = gameCanvas.getContext("2d");

function drawRoundedObstacleBottom(x, width, height) {
  const floorY = gameCanvas.height;
  const topY = floorY - height;

  const angleRad = protocol.slopeAngleDeg * Math.PI / 180;
  let ramp = height / Math.tan(angleRad);
  ramp = Math.min(ramp, width / 2 - 1);
  if (ramp < 1) ramp = 1;

  ctxGame.beginPath();
  ctxGame.moveTo(x, floorY);
  ctxGame.lineTo(x + ramp, topY);
  ctxGame.lineTo(x + width - ramp, topY);
  ctxGame.lineTo(x + width, floorY);
  ctxGame.closePath();
  ctxGame.fill();
}

function markCompletedContractions(prevElapsed, newElapsed) {
  let t = 0;
  for (const seg of protocol.timeline) {
    const start = t;
    const end = t + seg.duration;
    t = end;

    if (seg.type === "contraction") {
      if (prevElapsed < end && newElapsed >= end) {
        if (seg.success !== false) {
          score += 1;
        }
      }
    }
  }
}

let lastFrameTime = performance.now();

function updateGame(now) {
  const dt = (now - lastFrameTime) / 1000;
  lastFrameTime = now;

  const n = normalizedEmg();
  document.getElementById("levelDisplay").textContent = Math.round(n * 100);

  const displayN = Math.min(n / ceilingMargin, 1);

  birdV -= displayN * 1.9;
  birdV += 0.42;
  birdY += birdV;

  if (birdY < 20 + playerR) {
    birdY = 20 + playerR;
    birdV = 0;
  }
  if (birdY > gameCanvas.height - 20 - playerR) {
    birdY = gameCanvas.height - 20 - playerR;
    birdV = 0;
  }

  if (!protocol.running) {
    updateProtocolDisplay();
    return;
  }

  const prevElapsed = protocol.elapsed;
  protocol.elapsed += dt;

  if (protocol.elapsed >= protocol.totalDuration) {
    protocol.elapsed = protocol.totalDuration;
    protocol.running = false;
  }

  const obstacleH = gameCanvas.height * (protocol.obstacleHeightPercent / ceilingMargin);

  let t = 0;
  for (const seg of protocol.timeline) {
    const segStart = t;
    const segEnd = t + seg.duration;
    t = segEnd;

    if (seg.type !== "contraction") continue;

    const width = seg.duration * timeScale;
    const x = (playerX + playerR) + (segStart - protocol.elapsed) * timeScale;

    if (x + width < 0 || x > gameCanvas.width) continue;

    const sampleXs = [playerX - playerR, playerX, playerX + playerR];
    for (const sx of sampleXs) {
      const topY = topYForObstacleAtX(x, width, obstacleH, sx);
      if (topY !== null) {
        if (birdY + playerR > topY) {
          seg.success = false;
        }
      }
    }
  }

  markCompletedContractions(prevElapsed, protocol.elapsed);
  updateProtocolDisplay();
}

function drawGame() {
  ctxGame.clearRect(0, 0, gameCanvas.width, gameCanvas.height);

  ctxGame.strokeStyle = "#bbb";
  ctxGame.beginPath();
  ctxGame.moveTo(0, gameCanvas.height - 20);
  ctxGame.lineTo(gameCanvas.width, gameCanvas.height - 20);
  ctxGame.stroke();

  const thresholdY = gameCanvas.height - (gameCanvas.height * (protocol.obstacleHeightPercent / ceilingMargin));
  ctxGame.strokeStyle = "red";
  ctxGame.beginPath();
  ctxGame.moveTo(0, thresholdY);
  ctxGame.lineTo(gameCanvas.width, thresholdY);
  ctxGame.stroke();

  const obstacleH = gameCanvas.height * (protocol.obstacleHeightPercent / ceilingMargin);

  let t = 0;
  for (const seg of protocol.timeline) {
    const segStart = t;
    const segEnd = t + seg.duration;
    t = segEnd;

    if (seg.type !== "contraction") continue;

    const width = seg.duration * timeScale;
    const x = (playerX + playerR) + (segStart - protocol.elapsed) * timeScale;

    if (x + width < -5 || x > gameCanvas.width + 5) continue;

    ctxGame.fillStyle = seg.success === false ? "#b00020" : "#333";
    drawRoundedObstacleBottom(x, width, obstacleH);
  }

  ctxGame.fillStyle = "#1f6feb";
  ctxGame.beginPath();
  ctxGame.arc(playerX, birdY, playerR, 0, Math.PI * 2);
  ctxGame.fill();

  ctxGame.fillStyle = "#000";
  ctxGame.font = "24px Arial";
  ctxGame.fillText("EMG: " + emg, 20, 30);
  ctxGame.fillText("Réussites: " + score, 20, 60);
}

function loop(now) {
  drawBiofeedback();
  updateGame(now);
  drawGame();
  requestAnimationFrame(loop);
}

updateProtocolDisplay();
requestAnimationFrame(loop);
</script>
</body>
</html>
"""

async def index(request):
    return web.Response(text=HTML_PAGE, content_type="text/html")

async def start_web():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/data", api_data)
    app.router.add_post("/set_calibration", set_calibration)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8090)
    await site.start()

async def main():
    await asyncio.gather(
        ble_loop(),
        start_web(),
    )

asyncio.run(main())
