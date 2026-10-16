(() => {
  const params = new URLSearchParams(window.location.search);
  const isPatient = (params.get("context") || "anonymous").startsWith("patient:");
  const banner = document.getElementById("flowBanner");
  if (banner) banner.hidden = !isPatient;
  const container = document.getElementById("measurementSelection");
  if (!container) return;
  const groups = [
    ["cote", "Côté", ["Gauche", "Droite"]],
    ["articulation", "Articulation", ["Épaule", "Coude", "Poignet", "Hanche", "Genou", "Cheville", "Rachis cervical", "Rachis dorsal", "Rachis lombaire"]],
    ["mouvement", "Mouvement", ["Flexion", "Extension", "Abduction", "Adduction", "Rotation interne", "Rotation externe"]],
  ];
  // Keep older deep links, including rotation variants, editable without losing them.
  const selected = Object.fromEntries(groups.map(([key]) => [key, params.get(key) || ""]));
  let captured = null;
  const summary = document.getElementById("selectionSummary");
  function refresh() {
    const label = [selected.cote, selected.articulation, selected.mouvement].filter(Boolean).join(" · ");
    summary.textContent = label || "Aucune précision sélectionnée";
    const url = new URL(window.location.href);
    groups.forEach(([key]) => {
      if (selected[key]) url.searchParams.set(key, selected[key]);
      else url.searchParams.delete(key);
    });
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    if (isPatient) {
      document.getElementById("flowSave").textContent = (label ? label + " — " : "") + "À la fin, les résultats seront enregistrés dans le dossier patient.";
    }
  }
  groups.forEach(([key, title, choices]) => {
    const fieldset = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = title;
    fieldset.appendChild(legend);
    const options = selected[key] && !choices.includes(selected[key]) ? [...choices, selected[key]] : choices;
    options.forEach(value => {
      const label = document.createElement("label");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = key;
      radio.value = value;
      radio.checked = selected[key] === value;
      radio.addEventListener("change", () => {
        selected[key] = value;
        refresh();
      });
      label.appendChild(radio);
      label.appendChild(document.createTextNode(value));
      fieldset.appendChild(label);
    });
    container.appendChild(fieldset);
  });
  document.getElementById("clearSelection").addEventListener("click", () => {
    groups.forEach(([key]) => { selected[key] = ""; });
    container.querySelectorAll("input").forEach(input => { input.checked = false; });
    refresh();
  });
  window.KineForceSelection = {
    capture: () => { captured = {...selected}; },
    measurementSelection: () => ({...(captured || selected)}),
  };
  refresh();
})();
