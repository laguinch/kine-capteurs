window.KinePatientSave = (() => {
  const params = new URLSearchParams(window.location.search);
  const context = params.get("context") || "anonymous";
  const patientId = context.startsWith("patient:")
    ? Number(context.split(":")[1])
    : null;
  const saved = new Set();

  function hasPatient() {
    return Number.isFinite(patientId) && patientId > 0;
  }

  function selection() {
    return {
      articulation: params.get("articulation") || "",
      mouvement: params.get("mouvement") || "",
    };
  }

  async function saveEvaluation(data, details) {
    if (!hasPatient()) return false;
    if (!data?.finished_at || !data?.csv_path || data?.running || data?.phase === "active") {
      return false;
    }
    if (data.last_error) return false;
    if (saved.has(data.csv_path)) return true;

    saved.add(data.csv_path);
    const response = await fetch("/api/evaluations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        patient_id: patientId,
        session_type: details.session_type || "evaluation",
        sensor: details.sensor,
        test_name: details.test_name,
        display_name: details.display_name || details.test_name,
        summary: details.summary || "Test terminé.",
        csv_path: data.csv_path,
      }),
    });
    if (!response.ok) {
      saved.delete(data.csv_path);
      throw new Error("Enregistrement patient impossible");
    }
    return true;
  }

  return { hasPatient, saveEvaluation, selection };
})();
