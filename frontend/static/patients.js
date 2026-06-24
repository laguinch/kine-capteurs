const searchInput = document.getElementById("patientSearch");
const showCreateButton = document.getElementById("showCreateButton");
const cancelCreateButton = document.getElementById("cancelCreateButton");
const patientForm = document.getElementById("patientForm");
const patientList = document.getElementById("patientList");
const patientMessage = document.getElementById("patientMessage");

function showMessage(text, type = "") {
  patientMessage.textContent = text;
  patientMessage.className = `message ${type}`.trim();
  patientMessage.classList.remove("hidden");
}

function hideMessage() {
  patientMessage.classList.add("hidden");
}

function formatDate(value) {
  if (!value) return "Date non renseignée";
  return new Date(`${value}T00:00:00`).toLocaleDateString("fr-FR");
}

function patientCard(patient) {
  const article = document.createElement("article");
  article.className = "patient-card";
  article.innerHTML = `
    <div>
      <strong>${patient.nom} ${patient.prenom}</strong>
      <small>${formatDate(patient.date_naissance)}${patient.pathologie ? ` · ${patient.pathologie}` : ""}</small>
    </div>
    <a class="download" href="/session/patient/${patient.id}?context=patient">Choisir</a>
  `;
  return article;
}

async function loadPatients() {
  const q = searchInput.value.trim();
  const response = await fetch(`/api/patients${q ? `?q=${encodeURIComponent(q)}` : ""}`);
  if (!response.ok) {
    showMessage("Impossible de charger les patients.", "error");
    return;
  }
  const patients = await response.json();
  patientList.innerHTML = "";
  if (!patients.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
      <p class="eyebrow">Aucun patient</p>
      <h2>Aucun dossier ne correspond à la recherche</h2>
      <p>Vous pouvez créer un nouveau patient ou continuer en test anonyme.</p>
    `;
    patientList.appendChild(empty);
    return;
  }
  patients.forEach((patient) => patientList.appendChild(patientCard(patient)));
}

function fieldValue(id) {
  const value = document.getElementById(id).value.trim();
  return value || null;
}

showCreateButton.addEventListener("click", () => {
  patientForm.classList.remove("hidden");
  document.getElementById("patientLastName").focus();
});

cancelCreateButton.addEventListener("click", () => {
  patientForm.reset();
  patientForm.classList.add("hidden");
});

patientForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideMessage();
  const payload = {
    nom: fieldValue("patientLastName"),
    prenom: fieldValue("patientFirstName"),
    date_naissance: fieldValue("patientBirthDate"),
    date_ordonnance: fieldValue("patientPrescriptionDate"),
    pathologie: fieldValue("patientCondition"),
    commentaires: fieldValue("patientNotes"),
  };
  const response = await fetch("/api/patients", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    showMessage("Le patient n’a pas pu être créé. Vérifiez le nom et le prénom.", "error");
    return;
  }
  const patient = await response.json();
  showMessage("Patient créé. Vous pouvez démarrer sa séance.", "ready");
  patientForm.reset();
  patientForm.classList.add("hidden");
  await loadPatients();
  window.location.href = `/session/patient/${patient.id}?context=patient`;
});

let searchTimer;
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadPatients, 180);
});

loadPatients();
