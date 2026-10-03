/* Renew only after a real interaction; background polling never counts. */
(() => {
  if (window.__trendIdleInstalled || location.pathname.startsWith('/login')) return;
  window.__trendIdleInstalled = true;
  let lastSent = 0, pending = false, timer;
  const expired = () => location.replace('/login?next=' + encodeURIComponent(location.pathname + location.search + location.hash));
  const schedule = expires => {
    clearTimeout(timer);
    // Another tab may have renewed the shared cookie. Recheck without touching
    // activity before redirecting; an idle account will receive 401.
    timer = setTimeout(() => check(false), Math.max(1000, expires * 1000 - Date.now()));
  };
  async function check(touch) {
    if (pending) return;
    pending = true;
    try {
      const response = await fetch('/api/access/session/' + (touch ? 'activity' : 'status'), {
        method: touch ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
        ...(touch ? {headers: {'Content-Type': 'application/json'}, body: '{}'} : {})
      });
      if (response.status === 401) { expired(); return; }
      if (!response.ok) return;
      const state = await response.json();
      if (state.ok && Number.isFinite(state.expires_at)) schedule(state.expires_at);
      if (touch) lastSent = Date.now();
    } catch (_) { /* A transport error is not a confirmed logout. */ }
    finally { pending = false; }
  }
  for (const name of ['pointerdown', 'keydown', 'wheel', 'touchstart']) {
    window.addEventListener(name, event => {
      if (event.isTrusted && document.visibilityState === 'visible' && Date.now() - lastSent >= 30000) check(true);
    }, {passive: true});
  }
  check(false);
})();
