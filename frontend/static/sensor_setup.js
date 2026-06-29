const JOINTS = [
  "Rachis cervical",
  "Rachis dorsal",
  "Rachis lombaire",
  "Épaule",
  "Coude",
  "Poignet",
  "Hanche",
  "Genou",
  "Cheville",
];

const MOVEMENTS = [
  "Flexion",
  "Extension",
  "Abduction",
  "Adduction",
  "Rotation interne",
  "Rotation externe",
];

const MOVEMENT_VARIANTS = {
  "Rotation interne": ["RI 1", "RI 2", "RI 3"],
  "Rotation externe": ["RE 1", "RE 2", "RE 3"],
};

const KMOVE_GLOBAL_JOINTS = new Set([
  "Rachis cervical",
  "Épaule",
  "Hanche",
]);

const SIDED_JOINTS = new Set([
  "Épaule",
  "Coude",
  "Poignet",
  "Hanche",
  "Genou",
  "Cheville",
]);

const params = new URLSearchParams(window.location.search);
const context = params.get("context") || "anonymous";
const type = params.get("type") || "evaluation";
const root = document.querySelector(".sensor-setup");
const testPath = root.dataset.testPath;
const jointChoices = document.getElementById("jointChoices");
const movementChoices = document.getElementById("movementChoices");
const movementStep = document.getElementById("movementStep");
const selectedJointTitle = document.getElementById("selectedJointTitle");
let selectedJoint = params.get("articulation") || null;
let selectedSide = params.get("cote") || null;

document.querySelectorAll(".sensor-nav a[href^='/kforceplates'], .sensor-nav a[href^='/kpush'], .sensor-nav a[href^='/kpull'], .sensor-nav a[href^='/kmove']").forEach((link) => {
  const target = new URL(link.getAttribute("href"), window.location.origin);
  if (type === "training" && target.pathname === "/kforceplates") {
    target.pathname = "/kforceplates/jeux";
  }
  target.searchParams.set("context", context);
  target.searchParams.set("type", type);
  link.href = `${target.pathname}${target.search}`;
});

function selectedLabel() {
  return [selectedJoint, selectedSide].filter(Boolean).join(" ");
}

function makeButton(label, className = "", side = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `choice-card setup-card ${className} ${side ? "has-side-choice" : ""}`.trim();
  button.innerHTML = `<strong>${label}</strong>`;
  if (side) {
    const row = document.createElement("span");
    row.className = "side-choice-row";
    ["Droite", "Gauche"].forEach((sideLabel) => {
      const sideButton = document.createElement("span");
      sideButton.className =
        `side-choice ${selectedJoint === label && selectedSide === sideLabel ? "active" : ""}`;
      sideButton.textContent = sideLabel;
      sideButton.addEventListener("click", (event) => {
        event.stopPropagation();
        selectedJoint = label;
        selectedSide = sideLabel;
        renderJoints();
        renderMovements();
        movementStep.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      row.appendChild(sideButton);
    });
    button.appendChild(row);
  }
  return button;
}

function renderJoints() {
  jointChoices.innerHTML = "";
  JOINTS.forEach((joint) => {
    const needsSide = SIDED_JOINTS.has(joint);
    const button = makeButton(
      joint,
      joint === selectedJoint ? "primary-choice" : "",
      needsSide
    );
    button.addEventListener("click", () => {
      selectedJoint = joint;
      selectedSide = needsSide ? selectedSide || "Droite" : null;
      renderJoints();
      renderMovements();
      movementStep.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    jointChoices.appendChild(button);
  });
}

function movementLabel(movement, variant = null) {
  return [movement, variant].filter(Boolean).join(" ");
}

function targetUrl(movement, variant = null) {
  const next = new URLSearchParams(params);
  next.set("articulation", selectedJoint);
  if (selectedSide) {
    next.set("cote", selectedSide);
  } else {
    next.delete("cote");
  }
  next.set("mouvement", movementLabel(movement, variant));
  return `${testPath}?${next.toString()}`;
}

function globalTargetUrl() {
  const next = new URLSearchParams(params);
  next.set("articulation", selectedJoint);
  if (selectedSide) {
    next.set("cote", selectedSide);
  } else {
    next.delete("cote");
  }
  next.set("mouvement", "Bilan global");
  next.set("protocole", "global");
  return `${testPath}?${next.toString()}`;
}

function renderMovements() {
  if (!selectedJoint) {
    movementStep.classList.add("hidden");
    return;
  }
  selectedJointTitle.textContent = selectedLabel();
  movementChoices.innerHTML = "";
  if (root.dataset.sensor === "kmove" && KMOVE_GLOBAL_JOINTS.has(selectedJoint)) {
    const globalCard = document.createElement("a");
    globalCard.className = "choice-card setup-card primary-choice";
    globalCard.href = globalTargetUrl();
    globalCard.innerHTML = `
      <span class="choice-kicker">Une seule acquisition</span>
      <strong>Bilan global</strong>
      <small>Mesurer plusieurs amplitudes à la suite depuis le même placement du K‑Move.</small>
    `;
    movementChoices.appendChild(globalCard);
  }
  MOVEMENTS.forEach((movement) => {
    const variants = MOVEMENT_VARIANTS[movement] || [];
    const card = document.createElement(variants.length ? "div" : "a");
    card.className = `choice-card setup-card ${variants.length ? "has-side-choice variant-choice-card" : ""}`.trim();
    if (!variants.length) {
      card.href = targetUrl(movement);
    }
    card.innerHTML = `<strong>${movement}</strong>`;
    if (variants.length) {
      const row = document.createElement("span");
      row.className = "side-choice-row";
      variants.forEach((variant) => {
        const variantLink = document.createElement("span");
        variantLink.className = "side-choice";
        variantLink.textContent = variant;
        variantLink.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          window.location.href = targetUrl(movement, variant);
        });
        row.appendChild(variantLink);
      });
      card.appendChild(row);
    }
    movementChoices.appendChild(card);
  });
  movementStep.classList.remove("hidden");
}

renderJoints();
renderMovements();
