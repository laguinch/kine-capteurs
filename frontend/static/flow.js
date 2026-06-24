(() => {
  const params = new URLSearchParams(window.location.search);
  const context = params.get("context") || "anonymous";
  const type = params.get("type") || "evaluation";
  const isPatient = context.startsWith("patient:");
  const isTraining = type === "training";

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
})();
