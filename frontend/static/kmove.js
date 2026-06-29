const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(window.location.search);
const selection = {
  articulation: params.get("articulation") || "",
  cote: params.get("cote") || "",
  mouvement: params.get("mouvement") || "",
  protocole: params.get("protocole") || "",
};
const isGlobalProtocol = selection.protocole === "global";
const state = {
  history: { rotation: [], flexion: [], inclination: [] },
  lastTimestamp: null,
  phase: "disconnected",
  globalCardsReady: false,
  currentAngles: {
    rotation: 0,
    flexion_extension: 0,
    inclination: 0,
  },
  guided: {
    configured: false,
    selectedIndexes: [],
    repetitions: 3,
    currentTarget: 0,
    currentRep: 1,
    repActive: false,
    repRange: null,
    complete: false,
    results: {},
  },
  ranges: {
    rotation: { min: 0, max: 0 },
    flexion_extension: { min: 0, max: 0 },
    inclination: { min: 0, max: 0 },
  },
};

const GLOBAL_PROTOCOLS = {
  "Rachis cervical": {
    title: "Rachis cervical · bilan global",
    cards: [
      { label: "Flexion", axis: "flexion_extension", side: "positive" },
      { label: "Extension", axis: "flexion_extension", side: "negative" },
      { label: "Inclinaison droite", axis: "inclination", side: "positive" },
      { label: "Inclinaison gauche", axis: "inclination", side: "negative" },
      { label: "Rotation droite", axis: "rotation", side: "positive" },
      { label: "Rotation gauche", axis: "rotation", side: "negative" },
    ],
  },
  "Épaule": {
    title: "Épaule · bilan global",
    cards: [
      { label: "Flexion", axis: "inclination", side: "negative" },
      { label: "Extension", axis: "inclination", side: "positive" },
      { label: "Abduction", axis: "rotation", side: "negative" },
      { label: "Adduction", axis: "flexion_extension", side: "positive" },
      { label: "Rotation externe", axis: "rotation", side: "negative" },
      { label: "Rotation interne", axis: "rotation", side: "positive" },
    ],
  },
  "Hanche": {
    title: "Hanche · bilan global",
    cards: [
      { label: "Flexion", axis: "flexion_extension", side: "positive" },
      { label: "Extension", axis: "flexion_extension", side: "negative" },
      { label: "Abduction", axis: "inclination", side: "positive" },
      { label: "Adduction", axis: "inclination", side: "negative" },
      { label: "Rotation externe", axis: "rotation", side: "positive" },
      { label: "Rotation interne", axis: "rotation", side: "negative" },
    ],
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

function amplitudeFromRange(range, side) {
  if (!range) return 0;
  if (side === "positive") return Math.max(0, Number(range.max) || 0);
  return Math.max(0, -(Number(range.min) || 0));
}

function amplitudeFromDelta(delta, side) {
  if (!Number.isFinite(delta)) return 0;
  if (side === "positive") return Math.max(0, delta);
  return Math.max(0, -delta);
}

function angularDelta(current, baseline) {
  if (!Number.isFinite(current) || !Number.isFinite(baseline)) return 0;
  let delta = current - baseline;
  while (delta > 180) delta -= 360;
  while (delta < -180) delta += 360;
  return delta;
}

function protocolConfig() {
  return GLOBAL_PROTOCOLS[selection.articulation] || null;
}

function selectedGuidedCards() {
  const config = protocolConfig();
  if (!config) return [];
  return state.guided.selectedIndexes
    .map((index) => ({ ...config.cards[index], index }))
    .filter((card) => card.label);
}

function guidedResultValue(card) {
  const values = state.guided.results[card.index] || [];
  return values.length ? Math.max(...values) : null;
}

function guidedSummaryText() {
  const cards = selectedGuidedCards();
  if (!cards.length) return null;
  return cards
    .map((card) => {
      const values = state.guided.results[card.index] || [];
      const best = guidedResultValue(card);
      const suffix = values.length ? ` · ${values.length} rep.` : "";
      return `${card.label} ${best === null ? "—" : `${format(best, 1)}°`}${suffix}`;
    })
    .join(" · ");
}

function setupGlobalProtocol() {
  const panel = $("globalProtocol");
  const choices = $("globalProtocolChoices");
  const title = $("globalProtocolTitle");
  if (!panel || !choices || !isGlobalProtocol || !protocolConfig()) return;
  $("durationControl")?.classList.add("hidden");
  $("timerBlock")?.classList.add("hidden");
  const config = protocolConfig();
  panel.classList.remove("hidden");
  title.textContent = [
    config.title,
    selection.cote,
  ].filter(Boolean).join(" · ");
  choices.innerHTML = "";
  config.cards.forEach((card, index) => {
    const label = document.createElement("label");
    label.className = "global-protocol-choice";
    label.innerHTML = `
      <input type="checkbox" value="${index}" checked>
      <span>${card.label}</span>
    `;
    choices.appendChild(label);
  });
  state.guided.selectedIndexes = config.cards.map((_, index) => index);
}

function configureGuidedProtocol() {
  if (!isGlobalProtocol || !protocolConfig()) return true;
  const checked = Array.from(document.querySelectorAll("#globalProtocolChoices input:checked"))
    .map((input) => Number(input.value))
    .filter((value) => Number.isInteger(value));
  if (!checked.length) {
    message("Choisissez au moins une amplitude à tester.", true);
    return false;
  }
  state.guided = {
    configured: true,
    selectedIndexes: checked,
    repetitions: Number($("globalRepetitions")?.value || 3),
    currentTarget: 0,
    currentRep: 1,
    repActive: false,
    repRange: null,
    complete: false,
    results: {},
  };
  checked.forEach((index) => {
    state.guided.results[index] = [];
  });
  state.globalCardsReady = false;
  $("globalProtocolSetup")?.classList.add("hidden");
  $("globalProtocolRunner")?.classList.remove("hidden");
  setupGlobalCards();
  updateGuidedRunner();
  renderGlobalCards();
  return true;
}

function activeGuidedCard() {
  return selectedGuidedCards()[state.guided.currentTarget] || null;
}

function updateGuidedRunner() {
  if (!isGlobalProtocol) return;
  const card = activeGuidedCard();
  const target = $("globalCurrentTarget");
  const rep = $("globalCurrentRep");
  const startButton = $("globalStartRep");
  const validateButton = $("globalValidateRep");
  if (!target || !rep || !startButton || !validateButton) return;
  if (!card || state.guided.complete) {
    target.textContent = "Protocole terminé";
    rep.textContent = "Toutes les amplitudes sélectionnées ont été testées.";
    startButton.disabled = true;
    validateButton.disabled = true;
    return;
  }
  target.textContent = card.label;
  rep.textContent =
    `Répétition ${state.guided.currentRep} / ${state.guided.repetitions}`;
  startButton.disabled = !["armed", "active"].includes(state.phase) || state.guided.repActive;
  validateButton.disabled = !state.guided.repActive;
}

function startGuidedRepetition() {
  const card = activeGuidedCard();
  if (!card) return;
  const current = Number(state.currentAngles[card.axis]) || 0;
  state.guided.repActive = true;
  state.guided.repRange = { baseline: current, current: 0, min: 0, max: 0 };
  message(`Zéro de ${card.label} enregistré. Faites le mouvement puis revenez au départ.`);
  updateGuidedRunner();
}

function updateGuidedRepetition() {
  if (!state.guided.repActive) return;
  const card = activeGuidedCard();
  if (!card || !state.guided.repRange) return;
  const current = Number(state.currentAngles[card.axis]) || 0;
  const delta = angularDelta(current, Number(state.guided.repRange.baseline) || 0);
  state.guided.repRange.current = delta;
  state.guided.repRange.min = Math.min(state.guided.repRange.min, delta);
  state.guided.repRange.max = Math.max(state.guided.repRange.max, delta);
}

function validateGuidedRepetition() {
  const card = activeGuidedCard();
  if (!card || !state.guided.repRange) return;
  const value = amplitudeFromRange(state.guided.repRange, card.side);
  state.guided.results[card.index] = state.guided.results[card.index] || [];
  state.guided.results[card.index].push(value);
  state.guided.repActive = false;
  state.guided.repRange = null;
  if (state.guided.currentRep < state.guided.repetitions) {
    state.guided.currentRep += 1;
  } else {
    state.guided.currentRep = 1;
    state.guided.currentTarget += 1;
    if (state.guided.currentTarget >= selectedGuidedCards().length) {
      state.guided.complete = true;
      message("✓ Protocole global terminé. Vous pouvez arrêter l’acquisition.", false, true);
    }
  }
  renderGlobalCards();
  updateGuidedRunner();
}

function setupGlobalCards() {
  const panel = $("globalSummary");
  const target = $("globalCards");
  if (!panel || !target || !isGlobalProtocol || !protocolConfig()) return;
  const config = protocolConfig();
  panel.classList.remove("hidden");
  $("globalTitle").textContent = [
    config.title,
    selection.cote,
  ].filter(Boolean).join(" · ");
  target.innerHTML = "";
  const cards = isGlobalProtocol && state.guided.configured
    ? selectedGuidedCards()
    : config.cards.map((card, index) => ({ ...card, index }));
  cards.forEach((card) => {
    const row = document.createElement("div");
    row.className = "global-recap-row";
    row.innerHTML = `
      <div>
        <strong>${card.label}</strong>
        <small id="globalSmall${card.index}">En attente</small>
      </div>
      <strong><span id="globalValue${card.index}">—</span>°</strong>
    `;
    target.appendChild(row);
  });
  state.globalCardsReady = true;
}

function renderGlobalCards() {
  const config = protocolConfig();
  if (!isGlobalProtocol || !config) return;
  if (!state.globalCardsReady) setupGlobalCards();
  const cards = state.guided.configured
    ? selectedGuidedCards()
    : config.cards.map((card, index) => ({ ...card, index }));
  cards.forEach((card) => {
    const result = guidedResultValue(card);
    const element = $(`globalValue${card.index}`);
    const small = $(`globalSmall${card.index}`);
    if (element) element.textContent = result === null ? "—" : format(result, 1);
    if (small) {
      const reps = state.guided.results[card.index]?.length || 0;
      small.textContent = reps
        ? `Meilleure valeur · ${reps} rep.`
        : "En attente";
    }
  });
  renderGlobalLiveValue();
}

function renderGlobalLiveValue() {
  if (!isGlobalProtocol) return;
  const label = $("globalLiveLabel");
  const value = $("globalLiveValue");
  const small = $("globalLiveSmall");
  if (!label || !value || !small) return;
  const card = activeGuidedCard();
  if (!card || state.guided.complete) {
    label.textContent = "Protocole terminé";
    value.textContent = "—";
    small.textContent = "Le récapitulatif contient les meilleures valeurs.";
    return;
  }
  label.textContent = card.label;
  if (state.guided.repActive && state.guided.repRange) {
    value.textContent = format(
      amplitudeFromDelta(Number(state.guided.repRange.current) || 0, card.side),
      1,
    );
    small.textContent =
      `Répétition ${state.guided.currentRep} / ${state.guided.repetitions} · variation instantanée`;
    return;
  }
  value.textContent = "0,0";
  small.textContent =
    `Répétition ${state.guided.currentRep} / ${state.guided.repetitions} · démarrez la répétition`;
}

function update(data) {
  const phase = data.phase || "disconnected";
  state.phase = phase;
  const active = phase === "active";
  const armed = phase === "armed";
  const ready = phase === "ready";
  const busy = ["connecting", "reference"].includes(phase);
  $("statusDot").className =
    `status-dot ${data.connected ? "running" : data.last_error ? "error" : ""}`;
  const labels = {
    disconnected: "K‑Move déconnecté",
    connecting: "Connexion au K‑Move",
    reference: "Mise à zéro",
    ready: "K‑Move prêt",
    armed: "En attente de mouvement",
    active: "Acquisition en cours",
    error: "Erreur",
  };
  $("statusText").textContent = labels[phase] || "Prêt";
  $("connectButton").disabled = data.connected || busy;
  $("disconnectButton").disabled = !data.connected || active || armed;
  $("startButton").disabled = !ready;
  $("stopButton").disabled = !(active || armed);
  const downloadable = Boolean(data.csv_path && data.finished_at && !active && !armed);
  $("downloadButton").classList.toggle("disabled", !downloadable);
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
    message("Merci de ne pas bouger, étalonnage en cours.");
  } else if (phase === "ready") {
    message("Référence enregistrée. Le K‑Move est prêt.");
  } else if (phase === "armed" && isGlobalProtocol) {
    message("Protocole prêt. Cliquez sur « Démarrer la répétition », puis faites le mouvement.");
  } else if (phase === "armed") {
    message("Test prêt. Le chrono démarrera au premier mouvement.");
  } else if (phase === "active" && isGlobalProtocol && state.guided.complete) {
    message("✓ Protocole global terminé. Vous pouvez arrêter l’acquisition.", false, true);
  } else if (phase === "active" && isGlobalProtocol && state.guided.repActive) {
    const card = activeGuidedCard();
    message(`Répétition en cours : ${card?.label || "amplitude"}. Validez après le mouvement.`);
  } else if (phase === "active" && isGlobalProtocol) {
    message("Acquisition active. Cliquez sur « Démarrer la répétition » pour l’amplitude affichée.");
  } else if (phase === "active") {
    message("Test en cours : effectuez le mouvement demandé.");
  } else if (phase === "disconnected") {
    message("Cliquez sur « Connecter le K‑Move ».");
  }
  const m = data.measurement;
  if (m && m.timestamp_utc !== state.lastTimestamp) {
    state.lastTimestamp = m.timestamp_utc;
    state.ranges = m.ranges || state.ranges;
    state.currentAngles = {
      rotation: m.rotation_deg,
      flexion_extension: m.flexion_extension_deg,
      inclination: m.inclination_deg,
    };
    $("rotation").textContent = format(m.rotation_deg, 1);
    $("flexion").textContent = format(m.flexion_extension_deg, 1);
    $("inclination").textContent = format(m.inclination_deg, 1);
    $("batteryBadge").textContent = `Batterie ${m.battery_pct} %`;
    $("rotationRange").textContent = rangeText(m.ranges?.rotation);
    $("flexionRange").textContent = rangeText(m.ranges?.flexion_extension);
    $("inclinationRange").textContent = rangeText(m.ranges?.inclination);
    updateGuidedRepetition();
    renderGlobalCards();
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
  if (!active && data.finished_at && (!isGlobalProtocol || state.guided.complete)) {
    maybeSave(data);
  }
  updateGuidedRunner();
  renderGlobalLiveValue();
}

async function maybeSave(data) {
  if (!window.KinePatientSave) return;
  const patientSelection = window.KinePatientSave.selection();
  const label = [patientSelection.articulation, patientSelection.cote, patientSelection.mouvement].filter(Boolean).join(" · ");
  const rotation = state.ranges.rotation || {};
  const flexion = state.ranges.flexion_extension || {};
  const inclination = state.ranges.inclination || {};
  const config = protocolConfig();
  const globalSummary = guidedSummaryText() ||
    (config
      ? config.cards
        .map((card) => `${card.label} ${format(amplitudeFromRange(state.ranges[card.axis], card.side), 1)}°`)
        .join(" · ")
      : null);
  try {
    const saved = await window.KinePatientSave.saveEvaluation(data, {
      sensor: "K-Move",
      test_name: patientSelection.mouvement || "Mobilité",
      display_name: label || "Mobilité tridimensionnelle",
      summary: globalSummary ||
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
  if (isGlobalProtocol && !configureGuidedProtocol()) return;
  state.history = { rotation: [], flexion: [], inclination: [] };
  state.lastTimestamp = null;
  if (isGlobalProtocol) {
    state.globalCardsReady = false;
  }
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
        duration: isGlobalProtocol ? null : Number($("duration").value),
        filename: $("filename").value.trim() || null,
      }),
    });
  } catch (error) {
    message(error.message, true);
  }
}

async function stopTest() {
  $("stopButton").disabled = true;
  message("Arrêt du test…");
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
$("globalStartRep")?.addEventListener("click", startGuidedRepetition);
$("globalValidateRep")?.addEventListener("click", validateGuidedRepetition);
window.addEventListener("resize", draw);
poll();
setupGlobalProtocol();
setupGlobalCards();
renderGlobalCards();
setInterval(poll, 150);
