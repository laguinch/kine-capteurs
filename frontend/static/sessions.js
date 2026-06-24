const searchInput = document.getElementById("patientSearch");
const patientList = document.getElementById("patientList");
const sessionPanel = document.getElementById("sessionPanel");
const sessionList = document.getElementById("sessionList");
const selectedPatient = document.getElementById("selectedPatient");
const newSessionLink = document.getElementById("newSessionLink");

function formatDate(value) {
  if (!value) return "Date non renseignée";
  return new Date(value).toLocaleDateString("fr-FR");
}

function formatDateTime(value) {
  if (!value) return "Date non renseignée";
  return new Date(value).toLocaleString("fr-FR", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function formatSessionType(value) {
  if (value === "evaluation") return "évaluation";
  if (value === "training") return "entraînement";
  return value || "séance";
}

function patientCard(patient) {
  const birthDate = patient.date_naissance ? formatDate(`${patient.date_naissance}T00:00:00`) : "Date non renseignée";
  const article = document.createElement("article");
  article.className = "patient-card";
  article.innerHTML = `
    <div>
      <strong>${patient.nom} ${patient.prenom}</strong>
      <small>${birthDate}${patient.pathologie ? ` · ${patient.pathologie}` : ""}</small>
    </div>
    <button class="secondary" type="button">Voir les séances</button>
  `;
  article.querySelector("button").addEventListener("click", () => loadSessions(patient));
  return article;
}

function sessionCard(session) {
  const article = document.createElement("article");
  article.className = "patient-card";
  article.innerHTML = `
    <div>
      <strong>${session.display_name || session.test_name}</strong>
      <small>${formatDateTime(session.created_at)} · ${session.sensor} · ${formatSessionType(session.session_type)}</small>
      ${session.summary ? `<p class="session-summary">${session.summary}</p>` : ""}
    </div>
    ${session.csv_path ? `<span class="badge neutral">Données brutes</span>` : `<span class="badge neutral">Résumé</span>`}
  `;
  return article;
}

async function loadPatients() {
  const q = searchInput.value.trim();
  const response = await fetch(`/api/patients${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  patientList.innerHTML = "";
  if (!response.ok) return;
  const patients = await response.json();
  if (!patients.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
      <p class="eyebrow">Aucun patient</p>
      <h2>Aucun dossier ne correspond à la recherche</h2>
      <p>Créez d’abord le patient depuis “Nouvelle séance”.</p>
    `;
    patientList.appendChild(empty);
    return;
  }
  patients.forEach((patient) => patientList.appendChild(patientCard(patient)));
}

async function loadSessions(patient) {
  selectedPatient.textContent = `${patient.nom} ${patient.prenom}`;
  newSessionLink.href = `/session/patient/${patient.id}?context=patient`;
  sessionPanel.classList.remove("hidden");
  sessionList.innerHTML = "";

  const response = await fetch(`/api/evaluations/patient/${patient.id}`);
  if (!response.ok) return;
  const sessions = await response.json();
  if (!sessions.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
      <p class="eyebrow">Aucune séance</p>
      <h2>Aucun test enregistré pour ce patient</h2>
      <p>Les prochaines évaluations seront affichées ici dès que l’enregistrement automatique sera branché.</p>
    `;
    sessionList.appendChild(empty);
    return;
  }
  sessions.forEach((session) => sessionList.appendChild(sessionCard(session)));
}

let searchTimer;
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadPatients, 180);
});

loadPatients();
