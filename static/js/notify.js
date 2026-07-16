/*
 * Keeps the "Requests" and "Chats" sidebar badges current on every page,
 * without needing a page reload or WebSocket server.
 *
 * Kept deliberately cheap:
 *  - one small JSON endpoint (two indexed COUNT queries, no row data)
 *  - polls only while the tab is actually visible
 *  - backs off automatically if a request fails (e.g. brief network blip)
 *  - only touches the DOM when a number actually changed
 *  - broadcasts a `pingback:summary` event so other scripts (e.g. the
 *    inbox page) can react to a new message without polling twice
 */
(function () {
  const sidebar = document.querySelector('.sidebar[data-notify-url]');
  if (!sidebar) return;

  const url = sidebar.dataset.notifyUrl;
  const requestsBadge = document.getElementById('requests-badge');
  const chatsBadge = document.getElementById('chats-badge');

  const BASE_INTERVAL = 12000; // 12s: frequent enough to feel live, cheap enough to run everywhere
  const MAX_INTERVAL = 60000; // back off up to 60s if requests start failing
  let currentInterval = BASE_INTERVAL;
  let timer = null;
  let last = { pending_requests: null, unread_messages: null };

  function setBadge(el, count) {
    if (!el) return;
    const shown = count > 0;
    el.style.display = shown ? '' : 'none';
    if (shown) el.textContent = count > 99 ? '99+' : String(count);
  }

  async function poll() {
    if (document.hidden) return;
    try {
      const res = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      if (!res.ok) throw new Error('bad status');
      const data = await res.json();

      currentInterval = BASE_INTERVAL; // reset backoff on success

      if (data.pending_requests !== last.pending_requests) {
        setBadge(requestsBadge, data.pending_requests);
      }
      if (data.unread_messages !== last.unread_messages) {
        setBadge(chatsBadge, data.unread_messages);
      }

      const changed = {
        pending_requests: data.pending_requests !== last.pending_requests,
        unread_messages: data.unread_messages !== last.unread_messages,
      };
      last = data;

      window.dispatchEvent(new CustomEvent('pingback:summary', { detail: { ...data, changed } }));
    } catch (e) {
      currentInterval = Math.min(currentInterval * 2, MAX_INTERVAL);
    } finally {
      schedule();
    }
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(poll, currentInterval);
  }

  // Poll right away, then keep going; pause while the tab is hidden and
  // catch up immediately when the person comes back to it.
  poll();
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      clearTimeout(timer);
      poll();
    }
  });
})();
