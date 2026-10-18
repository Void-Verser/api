"use strict";
// Owner dashboard. All data is rendered with textContent (no innerHTML),
// every mutating call sends the per-session CSRF token.
(function () {
  const $ = (id) => document.getElementById(id);
  let csrf = "";
  let baseUrl = "";

  function show(id, on) { $(id).hidden = !on; }

  function toast(msg, bad) {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (bad ? " bad" : "");
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { t.hidden = true; }, 3500);
  }

  async function api(path, opts) {
    opts = opts || {};
    const headers = { Accept: "application/json" };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (opts.method && opts.method !== "GET") headers["X-CSRF-Token"] = csrf;
    let res;
    try {
      res = await fetch(path, {
        method: opts.method || "GET", headers, credentials: "same-origin",
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
    } catch (e) {
      throw new Error("Network error — is the API reachable?");
    }
    let json = null;
    try { json = await res.json(); } catch (e) { /* non-JSON */ }
    if (res.status === 401 && path !== "/dashboard/api/login") { renderLogin(true); throw new Error("Session expired — sign in again."); }
    if (!res.ok || !json || json.success === false) {
      const msg = (json && json.error && json.error.message) || ("Request failed (HTTP " + res.status + ")");
      throw new Error(msg);
    }
    return json.data;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      if (!ok) throw new Error("Copy failed — select and copy manually.");
    }
  }

  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-copy-target]");
    if (!btn) return;
    const text = $(btn.getAttribute("data-copy-target")).textContent;
    try { await copyText(text); toast("Copied to clipboard"); } catch (e) { toast(e.message, true); }
  });

  function fmtDate(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString();
  }

  function el(tag, text, cls) {
    const e = document.createElement(tag);
    if (text !== undefined && text !== null) e.textContent = text;
    if (cls) e.className = cls;
    return e;
  }

  // --- views -------------------------------------------------------------
  function renderLogin(sessionLost, ownerConfigured) {
    show("loading", false); show("app-view", false); show("who", false);
    show("login-view", true);
    if (ownerConfigured === false) show("no-owner-hint", true);
    if (sessionLost) { $("login-error").textContent = "Your session ended. Please sign in again."; show("login-error", true); }
    $("email").focus();
  }

  async function renderApp(email) {
    show("loading", false); show("login-view", false); show("app-view", true); show("who", true);
    $("who-email").textContent = email;
    await Promise.all([loadOverview(), loadKeys()]);
  }

  function botConfig(key) {
    return "MEDIA_API_URL=" + baseUrl + "\nMEDIA_API_KEY=" + key;
  }

  function renderExamples() {
    const k = "YOUR_MEDIA_API_KEY";
    $("ex-search").textContent =
      'curl -H "X-API-Key: ' + k + '" \\\n  "' + baseUrl + '/v1/search?q=Raiden%20Shogun&limit=5"';
    $("ex-convert").textContent =
      'curl -X POST "' + baseUrl + '/v1/convert" \\\n  -H "X-API-Key: ' + k + '" -H "Content-Type: application/json" \\\n' +
      '  -d \'{"url": "https://youtube.com/shorts/VIDEO_ID", "format": "mp3"}\'\n\n' +
      '# then poll until status is "succeeded" and download data.result.url\n' +
      'curl -H "X-API-Key: ' + k + '" "' + baseUrl + '/v1/jobs/JOB_ID"';
    $("ex-python").textContent = [
      "import os, time, httpx",
      "",
      'BASE = os.environ["MEDIA_API_URL"]   # ' + baseUrl,
      'H = {"X-API-Key": os.environ["MEDIA_API_KEY"]}',
      "",
      'hit = httpx.get(f"{BASE}/v1/search", params={"q": "Raiden Shogun"}, headers=H).json()["data"]["results"][0]',
      'job = httpx.post(f"{BASE}/v1/convert", json={"id": hit["id"], "format": "mp3"}, headers=H).json()["data"]',
      'while job["status"] in ("queued", "running"):',
      "    time.sleep(2)",
      '    job = httpx.get(job["status_url"], headers=H).json()["data"]',
      'audio_url = job["result"]["url"]  # expiring link -> send to Telegram',
    ].join("\n");
  }

  async function loadOverview() {
    try {
      const d = await api("/dashboard/api/overview");
      baseUrl = d.api_base_url;
      $("api-url").textContent = d.api_base_url;
      $("api-url-source").textContent = d.api_base_url_source;
      const badge = $("env-badge");
      badge.textContent = d.environment; badge.hidden = false;
      badge.className = "badge " + (d.environment === "production" ? "ok" : "warn");
      const s = d.stats;
      $("stats").textContent = s.active_keys + " active / " + s.total_keys + " total · " + s.total_requests + " requests";

      const w = $("warnings"); w.replaceChildren();
      d.warnings.forEach((m) => w.appendChild(el("li", m)));

      const pl = $("providers"); pl.replaceChildren();
      d.providers.forEach((p) => {
        const li = el("li");
        const head = el("div");
        head.appendChild(el("strong", p.name + " "));
        head.appendChild(el("span", p.enabled ? "enabled" : "not configured", "badge " + (p.enabled ? "ok" : "bad")));
        li.appendChild(head);
        const caps = el("div", null, "caps");
        (p.capabilities.length ? p.capabilities : ["none"]).forEach((c) => caps.appendChild(el("span", c, "badge")));
        li.appendChild(caps);
        if (p.note) li.appendChild(el("div", p.note, "muted small"));
        pl.appendChild(li);
      });

      const pr = d.processing, dl = $("processing"); dl.replaceChildren();
      const rows = [
        ["Conversion", pr.extraction_enabled ? "enabled" : "disabled"],
        ["Storage", pr.storage_backend + (pr.storage_ok ? " (ok)" : " (" + pr.storage_detail + ")")],
        ["Workers online", String(pr.workers_online.length)],
        ["Database", d.persistent_database ? "persistent" : "NOT persistent"],
        ["Jobs", Object.keys(s.jobs).length ? Object.entries(s.jobs).map((e) => e[0] + ": " + e[1]).join(", ") : "none yet"],
        ["Version", d.version],
      ];
      rows.forEach((r) => { dl.appendChild(el("dt", r[0])); dl.appendChild(el("dd", r[1])); });
      renderExamples();
    } catch (e) { toast(e.message, true); }
  }

  async function loadKeys() {
    try {
      const d = await api("/dashboard/api/keys");
      const body = $("keys-body"); body.replaceChildren();
      show("keys-empty", d.keys.length === 0);
      show("keys-table", d.keys.length > 0);
      d.keys.forEach((k) => {
        const tr = el("tr");
        tr.appendChild(el("td", k.name));
        const kc = el("td"); kc.appendChild(el("code", k.masked_key)); tr.appendChild(kc);
        const st = el("td"); st.appendChild(el("span", k.status, "badge " + (k.status === "active" ? "ok" : "bad"))); tr.appendChild(st);
        tr.appendChild(el("td", fmtDate(k.created_at)));
        tr.appendChild(el("td", fmtDate(k.last_used_at)));
        tr.appendChild(el("td", String(k.request_count)));
        const act = el("td", null, "actions-cell");
        if (k.active) {
          const rot = el("button", "Replace", "btn small");
          rot.type = "button";
          rot.setAttribute("aria-label", "Replace key " + k.name);
          rot.addEventListener("click", () => rotateKey(k, rot));
          const rev = el("button", "Revoke", "btn small danger");
          rev.type = "button";
          rev.setAttribute("aria-label", "Revoke key " + k.name);
          rev.addEventListener("click", () => revokeKey(k, rev));
          act.appendChild(rot); act.appendChild(rev);
        }
        tr.appendChild(act);
        body.appendChild(tr);
      });
    } catch (e) { toast(e.message, true); }
  }

  function revealKey(data) {
    $("newkey-value").textContent = data.api_key;
    $("newkey-config").textContent = botConfig(data.api_key);
    show("newkey-result", true);
    $("newkey-result").focus();
  }

  async function revokeKey(k, btn) {
    if (!confirm('Revoke "' + k.name + '"? Bots using it stop working immediately.')) return;
    btn.disabled = true;
    try { await api("/dashboard/api/keys/" + k.id + "/revoke", { method: "POST" }); toast("Key revoked"); await loadKeys(); loadOverview(); }
    catch (e) { toast(e.message, true); btn.disabled = false; }
  }

  async function rotateKey(k, btn) {
    if (!confirm('Replace "' + k.name + '"? The old key is revoked immediately and a new key is shown once.')) return;
    btn.disabled = true;
    try {
      const d = await api("/dashboard/api/keys/" + k.id + "/rotate", { method: "POST" });
      revealKey(d); toast("New key issued — copy it now"); await loadKeys();
    } catch (e) { toast(e.message, true); btn.disabled = false; }
  }

  // --- events --------------------------------------------------------------
  $("login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    show("login-error", false);
    const btn = $("login-btn"); btn.disabled = true; btn.textContent = "Signing in…";
    try {
      const d = await api("/dashboard/api/login", { method: "POST", body: { email: $("email").value, password: $("password").value } });
      csrf = d.csrf_token;
      $("password").value = "";
      await renderApp(d.email);
    } catch (e) {
      $("login-error").textContent = e.message; show("login-error", true);
    } finally { btn.disabled = false; btn.textContent = "Sign in"; }
  });

  $("newkey-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = $("getkey-btn"); btn.disabled = true; btn.textContent = "Generating…";
    try {
      const d = await api("/dashboard/api/keys", { method: "POST", body: { name: $("key-name").value } });
      $("key-name").value = "";
      revealKey(d);
      toast("API key created — copy it now");
      await loadKeys(); loadOverview();
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; btn.textContent = "Get API Key"; }
  });

  $("newkey-done").addEventListener("click", () => {
    $("newkey-value").textContent = ""; $("newkey-config").textContent = "";
    show("newkey-result", false);
    $("getkey-btn").focus();
  });

  $("logout-btn").addEventListener("click", async () => {
    try { await api("/dashboard/api/logout", { method: "POST" }); } catch (e) { /* ignore */ }
    csrf = ""; renderLogin(false);
  });

  // --- boot ------------------------------------------------------------------
  (async function boot() {
    try {
      const s = await api("/dashboard/api/session");
      if (s.authenticated) { csrf = s.csrf_token; await renderApp(s.email); }
      else renderLogin(false, s.owner_configured);
    } catch (e) {
      show("loading", false);
      $("loading").textContent = e.message; show("loading", true);
    }
  })();
})();
