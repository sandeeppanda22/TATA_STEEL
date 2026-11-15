/* Live data refresh - one poll per page, reload only the tabs whose tables
   changed. Server side: GET /api/db/live/changes returns one change_seq per
   watched table (db/05_live_change.sql triggers bump them on commit).

   Rules (see the project brief):
   - the FIRST reply is only a baseline, it reloads nothing;
   - later replies are compared per table (no global cursor);
   - a changed table marks every tab that shows it; the active tab reloads
     silently unless busy() (user is editing) - then a "new updates" banner
     appears instead; inactive tabs reload when next opened;
   - 5 s while active, 15 s after 60 s without change, paused while the
     browser tab is hidden (one poll on return), 4/8/16/30 s on errors,
     429 Retry-After raises the wait.
   The pure helpers are exported so node can unit-test them. */
(function (root) {
  const ACTIVE_MS = 5000, IDLE_MS = 15000, IDLE_AFTER_MS = 60000;
  const BACKOFF_MS = [4000, 8000, 16000, 30000];
  const LOCAL_ECHO_MS = 25000;   // > the slowest poll interval (15 s) + a request

  // prev: {table: seq} | null (null = no baseline yet); next: [{table_name, change_seq}]
  function diffChanged(prev, next) {
    const seqs = {};
    (next || []).forEach(r => { seqs[r.table_name] = r.change_seq; });
    if (prev == null) return { baseline: true, changed: [], seqs };
    const changed = Object.keys(seqs).filter(t => prev[t] !== seqs[t]);
    return { baseline: false, changed, seqs };
  }

  function tabsForTables(tableTabs, tables) {
    const out = new Set();
    tables.forEach(t => (tableTabs[t] || []).forEach(tab => out.add(tab)));
    return [...out];
  }

  function pollDelay(o) {
    if (o.errors > 0) {
      const b = BACKOFF_MS[Math.min(o.errors, BACKOFF_MS.length) - 1];
      return Math.max(b, o.retryAfterMs || 0);
    }
    if (o.retryAfterMs) return Math.max(o.retryAfterMs, ACTIVE_MS);
    return (o.now - o.lastChangeAt) > IDLE_AFTER_MS ? IDLE_MS : ACTIVE_MS;
  }

  function start(cfg) {
    // cfg: { endpoint, tableTabs, tabs: {tab: {key?, reload, busy?}}, activeTab(), onChange(tables), onEnabled(bool) }
    const keyOf = tab => (cfg.tabs[tab] && cfg.tabs[tab].key) || tab;
    const reloaders = {};
    Object.keys(cfg.tabs).forEach(tab => { reloaders[keyOf(tab)] = reloaders[keyOf(tab)] || cfg.tabs[tab]; });
    let seqs = null, errors = 0, retryAfterMs = 0, lastChangeAt = Date.now(), timer = null;
    let lastLocalWrite = 0;     // this browser's own save; its echo through the feed isn't news
    const dirty = new Set();    // keys to reload when their tab is next opened
    const pending = new Set();  // keys blocked by busy() on the active tab
    let banner = null;

    function showBanner() {
      if (!banner) {
        banner = document.createElement('div');
        banner.className = 'live-banner no-print';
        banner.innerHTML = '<span>New updates available.</span> <button type="button" class="btn-small">Refresh</button>';
        banner.querySelector('button').addEventListener('click', applyPending);
        document.body.appendChild(banner);
      }
      banner.hidden = false;
    }
    function hideBanner() { if (banner) banner.hidden = true; }

    async function runReload(key) {
      try { await reloaders[key].reload(); } catch (e) { /* tab shows its own error */ }
    }
    async function applyPending() {
      const keys = [...pending]; pending.clear(); hideBanner();
      for (const k of keys) await runReload(k);
    }

    function handleChanged(tables) {
      const active = cfg.activeTab();
      tabsForTables(cfg.tableTabs, tables).forEach(tab => {
        const key = keyOf(tab);
        if (!reloaders[key]) return;
        if (tab === active || keyOf(active) === key) {
          const busy = reloaders[key].busy && reloaders[key].busy();
          if (busy && Date.now() - lastLocalWrite < LOCAL_ECHO_MS) {
            dirty.add(key);                       // our own save: page already shows it, no banner
          } else if (busy) { pending.add(key); showBanner(); }
          else { dirty.delete(key); runReload(key); }
        } else {
          dirty.add(key);
        }
      });
      if (cfg.onChange) cfg.onChange(tables);
    }

    async function poll() {
      timer = null;
      if (document.hidden) return;           // resumed by visibilitychange
      retryAfterMs = 0;
      try {
        const res = await fetch(cfg.endpoint, { cache: 'no-store' });
        if (res.status === 429) {
          retryAfterMs = (Number(res.headers.get('Retry-After')) || 30) * 1000;
          errors = 0;
        } else if (!res.ok) {
          throw new Error('HTTP ' + res.status);
        } else {
          errors = 0;
          const body = await res.json();
          if (cfg.onEnabled) cfg.onEnabled(body.enabled !== false);
          const d = diffChanged(seqs, body.changed);
          seqs = d.seqs;
          if (!d.baseline && d.changed.length) { lastChangeAt = Date.now(); handleChanged(d.changed); }
        }
      } catch (e) {
        errors += 1;
      }
      schedule();
    }

    function schedule() {
      if (timer || document.hidden) return;
      timer = setTimeout(poll, pollDelay({ errors, retryAfterMs, now: Date.now(), lastChangeAt }));
    }

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) { if (timer) { clearTimeout(timer); timer = null; } }
      else { lastChangeAt = Date.now(); if (timer) clearTimeout(timer); timer = null; poll(); }
    });

    poll();
    return {
      // Call from tab activation. `selfReloads` = the tab's own loader just ran.
      noteLocalWrite() { lastLocalWrite = Date.now(); },
      onTabActivated(tab, selfReloads) {
        const key = keyOf(tab);
        const was = dirty.delete(key);
        if (was && !selfReloads && reloaders[key]) runReload(key);
      },
    };
  }

  const api = { diffChanged, tabsForTables, pollDelay, start };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.LiveRefresh = api;
})(typeof window !== 'undefined' ? window : globalThis);
