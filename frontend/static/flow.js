(() => {
  const params = new URLSearchParams(window.location.search);
  const context = params.get("context") || "anonymous";
  const type = params.get("type") || "evaluation";
  const isPatient = context.startsWith("patient:");
  const isTraining = type === "training";
  const articulation = params.get("articulation");
  const cote = params.get("cote");
  const mouvement = params.get("mouvement");

  const mode = document.getElementById("flowMode");
  const save = document.getElementById("flowSave");
  if (!mode || !save) return;

  mode.textContent = `${isTraining ? "Entraînement" : "Évaluation"} ${isPatient ? "patient" : "anonyme"}`;
  save.textContent = isPatient
    ? "À la fin, les résultats seront enregistrés dans le dossier patient."
    : "Résultats anonymes, exportables sans dossier patient.";

  if (isTraining) {
    save.textContent = isPatient
      ? "Les jeux d’entraînement utiliseront le capteur sélectionné et pourront être rattachés au patient."
      : "Les jeux d’entraînement resteront anonymes.";
  }

  if (articulation || cote || mouvement) {
    const selection = [articulation, cote, mouvement].filter(Boolean).join(" · ");
    save.textContent = `${selection} — ${save.textContent}`;
  }

  if (isPatient) {
    const patientId = context.split(":")[1];
    fetch(`/api/patients/${patientId}`)
      .then((response) => response.ok ? response.json() : null)
      .then((patient) => {
        if (!patient) return;
        mode.textContent = `${isTraining ? "Entraînement" : "Évaluation"} · ${patient.nom} ${patient.prenom}`;
      })
      .catch(() => {});
  }
})();
