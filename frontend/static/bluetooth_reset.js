(() => {
  const nav = document.querySelector('.sensor-nav');
  if (!nav) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'secondary';
  button.textContent = 'Reset Bluetooth';
  button.style.marginLeft = 'auto';
  const notice = document.createElement('p');
  notice.className = 'message hidden';
  notice.setAttribute('role', 'status');
  notice.setAttribute('aria-live', 'polite');
  nav.appendChild(button);
  nav.after(notice);
  const storageKey = 'kinventBluetoothReset';
  const show = (text) => {
    notice.textContent = text;
    notice.classList.remove('hidden');
  };
  const remember = (value) => {
    try {
      if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
      else sessionStorage.removeItem(storageKey);
    } catch (_) { /* Storage may be disabled. */ }
  };
  async function request(url, options = {}) {
    const response = await fetch(url, {
      ...options, cache: 'no-store', signal: AbortSignal.timeout(8000),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'La demande a échoué.');
    return data;
  }
  async function monitor(job) {
    button.disabled = true;
    show('Réinitialisation du Bluetooth en cours…');
    try {
      while (Date.now() - job.startedAt < 60000) {
        const state = await request('/api/devices/bluetooth/reset');
        if (state.generation === job.generation && ['done', 'error', 'rejected', 'skipped'].includes(state.status)) {
          if (state.status === 'done') {
            notice.textContent = '';
            notice.classList.add('hidden');
          } else {
            show(state.message);
          }
          return;
        }
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      show('Le reset n’a pas été confirmé. Vérifiez l’état dans la page Appareils avant de réessayer.');
    } catch (error) {
      show(`Impossible de vérifier le reset : ${error.message}. Consultez la page Appareils.`);
    } finally {
      remember(null);
      button.disabled = false;
    }
  }
  button.addEventListener('click', async () => {
    if (!window.confirm('Réinitialiser le Bluetooth ?\n\nLe capteur sera déconnecté. Vous devrez le reconnecter après le reset.')) return;
    button.disabled = true;
    show('Demande de réinitialisation…');
    try {
      const data = await request('/api/devices/bluetooth/reset', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({confirmed: true}),
      });
      const job = {generation: data.generation, startedAt: Date.now()};
      remember(job);
      await monitor(job);
    } catch (error) {
      show(error.message);
      button.disabled = false;
    }
  });
  try {
    const pending = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
    if (pending) monitor(pending);
  } catch (_) { /* No saved request. */ }
})();
