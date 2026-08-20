const $ = (id) => document.getElementById(id);
let actionInProgress = null;

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
  card.className = `choice-card device-card ${
    device.connected ? "connected" : "disconnected"
  }`;
  card.dataset.deviceKey = device.key;
  const current = device.active ? "Appareil actif" : "Appareil connu";
  const sides = Array.isArray(device.connected_sides) && device.connected_sides.length
    ? ` · ${device.connected_sides.join(", ")}`
    : "";
  const connectionLabel = device.connected ? "Connecté" : "Non connecté";
  const busy = actionInProgress === device.key;
  card.innerHTML = `
    <div class="device-card-head">
      <span class="status-dot ${phaseClass(device)}"></span>
      <span class="choice-kicker">${device.kind}</span>
    </div>
    <span class="device-state-badge">${connectionLabel}</span>
    <strong>${device.name}</strong>
    <small>${current} · ${device.phase_label}${sides}</small>
    <small class="device-addresses">${addressText(device)}</small>
    ${device.last_error ? `<small class="device-error">${device.last_error}</small>` : ""}
    <div class="device-actions">
      <button
        class="secondary device-connect"
        type="button"
        data-device-key="${device.key}"
        ${device.connected || actionInProgress ? "disabled" : ""}
      >${busy ? "Connexion…" : "Connecter"}</button>
      <button
        class="secondary device-disconnect"
        type="button"
        data-device-key="${device.key}"
        ${!device.connected || actionInProgress ? "disabled" : ""}
      >${busy ? "Déconnexion…" : "Déconnecter"}</button>
    </div>
  `;
  return card;
}

function render(data) {
  const manager = data.manager || {};
  const active = Boolean(manager.target);
  $("managerTitle").textContent = active
    ? `Dongle utilisé par ${manager.target}`
    : "Dongle libre";
  $("managerDetails").textContent = [
    manager.phase_label,
    manager.backend ? `backend ${manager.backend}` : null,
    manager.hci_adapter,
    manager.error,
  ].filter(Boolean).join(" · ");

  const grid = $("devicesGrid");
  grid.replaceChildren(...(data.devices || []).map(deviceCard));
  grid.querySelectorAll(".device-connect").forEach((button) => {
    button.addEventListener("click", () => connectDevice(button.dataset.deviceKey));
  });
  grid.querySelectorAll(".device-disconnect").forEach((button) => {
    button.addEventListener("click", () => disconnectDevice(button.dataset.deviceKey));
  });
}

async function refresh() {
  try {
    const response = await fetch("/api/devices", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Lecture impossible");
    render(data);
  } catch (error) {
    $("managerTitle").textContent = "Serveur indisponible";
    $("managerDetails").textContent = error.message;
  }
}

async function connectDevice(deviceKey) {
  if (!deviceKey || actionInProgress) return;
  actionInProgress = deviceKey;
  await refresh();
  try {
    const response = await fetch(`/api/devices/${deviceKey}/connect`, {
      method: "POST",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Connexion impossible");
    render(data);
  } catch (error) {
    $("managerTitle").textContent = "Connexion impossible";
    $("managerDetails").textContent = error.message;
  } finally {
    actionInProgress = null;
    await refresh();
  }
}

async function disconnectDevice(deviceKey) {
  if (!deviceKey || actionInProgress) return;
  actionInProgress = deviceKey;
  await refresh();
  try {
    const response = await fetch(`/api/devices/${deviceKey}/disconnect`, {
      method: "POST",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Déconnexion impossible");
    render(data);
  } catch (error) {
    $("managerTitle").textContent = "Déconnexion impossible";
    $("managerDetails").textContent = error.message;
  } finally {
    actionInProgress = null;
    await refresh();
  }
}

refresh();
setInterval(refresh, 2000);
