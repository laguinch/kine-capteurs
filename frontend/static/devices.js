const $ = (id) => document.getElementById(id);

function phaseClass(device) {
  if (device.last_error) return "error";
  if (device.connected || device.active) return "running";
  return "";
}

function addressText(device) {
  return (device.addresses || [])
    .map((entry) => entry.side ? `${entry.side} ${entry.address}` : entry.address)
    .join(" · ");
}

function deviceCard(device) {
  const card = document.createElement("article");
  card.className = "choice-card device-card";
  const current = device.active ? "Appareil actif" : "Appareil connu";
  const sides = Array.isArray(device.connected_sides) && device.connected_sides.length
    ? ` · ${device.connected_sides.join(", ")}`
    : "";
  card.innerHTML = `
    <div class="device-card-head">
      <span class="status-dot ${phaseClass(device)}"></span>
      <span class="choice-kicker">${device.kind}</span>
    </div>
    <strong>${device.name}</strong>
    <small>${current} · ${device.phase_label}${sides}</small>
    <small class="device-addresses">${addressText(device)}</small>
    ${device.last_error ? `<small class="device-error">${device.last_error}</small>` : ""}
    <a class="secondary device-open" href="${device.open_path}">Ouvrir</a>
  `;
  return card;
}

function render(data) {
  const manager = data.manager || {};
  const active = Boolean(manager.target);
  const error = Boolean(manager.error);
  $("managerDot").className = `status-dot ${error ? "error" : active ? "running" : ""}`;
  $("managerText").textContent = error
    ? "Erreur"
    : active
      ? "Appareil connecté"
      : "Aucun appareil actif";
  $("managerTitle").textContent = active
    ? `Dongle utilisé par ${manager.target}`
    : "Dongle libre";
  $("managerDetails").textContent = [
    manager.phase_label,
    manager.backend ? `backend ${manager.backend}` : null,
    manager.transport,
    manager.kplates_backend ? `plateformes ${manager.kplates_backend}` : null,
    manager.hci_adapter,
  ].filter(Boolean).join(" · ");
  $("disconnectCurrentButton").disabled = !active;

  const grid = $("devicesGrid");
  grid.replaceChildren(...(data.devices || []).map(deviceCard));
}

async function refresh() {
  try {
    const response = await fetch("/api/devices", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Lecture impossible");
    render(data);
  } catch (error) {
    $("managerDot").className = "status-dot error";
    $("managerText").textContent = "Serveur indisponible";
    $("managerDetails").textContent = error.message;
  }
}

$("disconnectCurrentButton").addEventListener("click", async () => {
  $("disconnectCurrentButton").disabled = true;
  await fetch("/api/devices/disconnect", { method: "POST" });
  await refresh();
});

refresh();
setInterval(refresh, 2000);
