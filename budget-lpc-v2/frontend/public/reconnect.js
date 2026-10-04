/* Retry only a public health check, never financial requests or writes. */
(() => {
  const status = document.getElementById('status');
  const retry = document.getElementById('retry');
  document.getElementById('address').textContent = 'App address: ' + location.host;
  let active = false;
  async function reconnect() {
    if (active) return;
    active = true;
    retry.disabled = true;
    const started = Date.now();
    try {
      while (Date.now() - started < 90000) {
        status.textContent = navigator.onLine
          ? 'Connecting to Budget LPC. The free online server may need about a minute to wake up. Your laptop does not need to be on.'
          : 'No internet connection. Reconnect to Wi-Fi or mobile data; we’ll retry automatically.';
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 12000);
        try {
          const response = await fetch('/api/health', {cache: 'no-store', signal: controller.signal});
          if (response.ok && (await response.json()).app === 'budget-lpc-v2') {
            location.replace('/');
            return;
          }
        } catch { /* A sleeping server or disconnected phone can fail temporarily. */ }
        finally { clearTimeout(timeout); }
        await new Promise(resolve => setTimeout(resolve, 3000));
      }
      status.textContent = 'Still unable to connect. Check your internet connection, or open the online version below. If that works, add that page to your Home Screen.';
    } finally { active = false; retry.disabled = false; }
  }
  retry.addEventListener('click', () => void reconnect());
  window.addEventListener('online', () => void reconnect());
  void reconnect();
})();
