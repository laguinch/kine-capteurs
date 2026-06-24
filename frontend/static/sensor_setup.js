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

const params = new URLSearchParams(window.location.search);
const root = document.querySelector(".sensor-setup");
const testPath = root.dataset.testPath;
const jointChoices = document.getElementById("jointChoices");
const movementChoices = document.getElementById("movementChoices");
const movementStep = document.getElementById("movementStep");
const selectedJointTitle = document.getElementById("selectedJointTitle");
let selectedJoint = params.get("articulation") || null;

function makeButton(label, className = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `choice-card setup-card ${className}`.trim();
  button.innerHTML = `<strong>${label}</strong>`;
  return button;
}

function renderJoints() {
  jointChoices.innerHTML = "";
  JOINTS.forEach((joint) => {
    const button = makeButton(joint, joint === selectedJoint ? "primary-choice" : "");
    button.addEventListener("click", () => {
      selectedJoint = joint;
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
  next.set("mouvement", movement);
  return `${testPath}?${next.toString()}`;
}

function renderMovements() {
  if (!selectedJoint) {
    movementStep.classList.add("hidden");
    return;
  }
  selectedJointTitle.textContent = selectedJoint;
  movementChoices.innerHTML = "";
  MOVEMENTS.forEach((movement) => {
    const link = document.createElement("a");
    link.className = "choice-card setup-card";
    link.href = targetUrl(movement);
    link.innerHTML = `<strong>${movement}</strong><small>Démarrer le protocole de test</small>`;
    movementChoices.appendChild(link);
  });
  movementStep.classList.remove("hidden");
}

renderJoints();
renderMovements();
