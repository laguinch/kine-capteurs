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

const SIDED_JOINTS = new Set([
  "Épaule",
  "Coude",
  "Poignet",
  "Hanche",
  "Genou",
  "Cheville",
]);

const params = new URLSearchParams(window.location.search);
const root = document.querySelector(".sensor-setup");
const testPath = root.dataset.testPath;
const jointChoices = document.getElementById("jointChoices");
const movementChoices = document.getElementById("movementChoices");
const movementStep = document.getElementById("movementStep");
const selectedJointTitle = document.getElementById("selectedJointTitle");
let selectedJoint = params.get("articulation") || null;
let selectedSide = params.get("cote") || null;

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

function targetUrl(movement) {
  const next = new URLSearchParams(params);
  next.set("articulation", selectedJoint);
  if (selectedSide) {
    next.set("cote", selectedSide);
  } else {
    next.delete("cote");
  }
  next.set("mouvement", movement);
  return `${testPath}?${next.toString()}`;
}

function renderMovements() {
  if (!selectedJoint) {
    movementStep.classList.add("hidden");
    return;
  }
  selectedJointTitle.textContent = selectedLabel();
  movementChoices.innerHTML = "";
  MOVEMENTS.forEach((movement) => {
    const link = document.createElement("a");
    link.className = "choice-card setup-card";
    link.href = targetUrl(movement);
    link.innerHTML = `<strong>${movement}</strong>`;
    movementChoices.appendChild(link);
  });
  movementStep.classList.remove("hidden");
}

renderJoints();
renderMovements();
