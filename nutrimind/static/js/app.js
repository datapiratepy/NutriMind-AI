/* NutriMind AI — core client utilities (theme, toasts, fetch, markdown).
   Exposed as window.NM; every page script builds on these helpers. */
"use strict";

window.NM = (function () {
  /* ---------------- theme (light / dark / auto with persistence) ---------- */
  const THEME_KEY = "nutrimind-theme";
  const THEME_ORDER = ["auto", "light", "dark"];
  const THEME_ICONS = { auto: "bi-circle-half", light: "bi-sun", dark: "bi-moon-stars" };

  function themeApply() {
    const saved = localStorage.getItem(THEME_KEY) || "auto";
    const dark = saved === "dark" || (saved === "auto" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.setAttribute("data-bs-theme", dark ? "dark" : "light");
    const icon = document.querySelector("#nm-theme-toggle i");
    if (icon) icon.className = "bi " + THEME_ICONS[saved];
    const btn = document.getElementById("nm-theme-toggle");
    if (btn) btn.title = "Theme: " + saved;
  }
  function themeCycle() {
    const saved = localStorage.getItem(THEME_KEY) || "auto";
    const next = THEME_ORDER[(THEME_ORDER.indexOf(saved) + 1) % THEME_ORDER.length];
    localStorage.setItem(THEME_KEY, next);
    themeApply();
    toast("Theme: " + next, "info");
  }
  window.matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", themeApply);

  /* ---------------- toasts ------------------------------------------------ */
  function toast(message, kind = "info") {
    const colors = { info: "text-bg-primary", success: "text-bg-success",
                     warning: "text-bg-warning", error: "text-bg-danger" };
    const el = document.createElement("div");
    el.className = `toast align-items-center border-0 ${colors[kind] || colors.info}`;
    el.setAttribute("role", "status");
    el.innerHTML = `<div class="d-flex"><div class="toast-body"></div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto"
              data-bs-dismiss="toast" aria-label="Close"></button></div>`;
    el.querySelector(".toast-body").textContent = message;
    document.getElementById("nm-toasts").appendChild(el);
    const t = new bootstrap.Toast(el, { delay: 3500 });
    t.show();
    el.addEventListener("hidden.bs.toast", () => el.remove());
  }

  /* ---------------- confirm dialog (Promise-based) ------------------------- */
  function confirmDialog(message) {
    return new Promise((resolve) => {
      const modalEl = document.getElementById("nm-confirm");
      document.getElementById("nm-confirm-text").textContent = message;
      const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
      const yes = document.getElementById("nm-confirm-yes");
      const onYes = () => { cleanup(); modal.hide(); resolve(true); };
      const onHide = () => { cleanup(); resolve(false); };
      function cleanup() {
        yes.removeEventListener("click", onYes);
        modalEl.removeEventListener("hidden.bs.modal", onHide);
      }
      yes.addEventListener("click", onYes);
      modalEl.addEventListener("hidden.bs.modal", onHide);
      modal.show();
    });
  }

  /* ---------------- fetch with the API error envelope ---------------------- */
  /* CSRF token, rendered into <meta> by base.html. Flask-WTF checks the
     X-CSRFToken header on every state-changing request; the JSON API no longer
     carries a blanket exemption now that a session cookie exists for a
     cross-site request to ride. */
  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  /* The ONLY place this app calls fetch().

     Every request goes through here so the CSRF header and the signed-out
     redirect are applied in one place. Callers that need the raw Response —
     file uploads, the SSE chat stream — use this directly; `api()` layers JSON
     parsing on top. A bare fetch() elsewhere silently omits the token, which is
     how document upload broke: it failed with an HTML 400 that the caller then
     tried to parse as JSON. tests/unit/test_frontend_contract.py fails the build
     if a bare fetch() reappears. */
  async function nmFetch(path, options = {}) {
    if (options.json !== undefined) {
      options.body = JSON.stringify(options.json);
      options.headers = Object.assign({ "Content-Type": "application/json" },
                                      options.headers);
      delete options.json;
    }
    const method = (options.method || "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      options.headers = Object.assign({ "X-CSRFToken": csrfToken() },
                                      options.headers);
    }
    const response = await fetch(path, options);
    /* A signed-out session must not look like a broken page. Reload so the
       server can redirect to the sign-in screen with a real explanation. */
    if (response.status === 401) {
      window.location.href = "/login?next=" +
        encodeURIComponent(window.location.pathname);
      throw new Error("Your session has expired. Please sign in again.");
    }
    return response;
  }

  /* Extract a human message from a failed response without ever throwing.

     Not every error body is JSON: a proxy timeout, a size limit hit before the
     app sees the request, or any unhandled framework error returns HTML. Calling
     response.json() on those throws "unexpected character at line 1 column 1",
     which replaces a useful message with a confusing one. */
  async function readError(response) {
    let body = null;
    try { body = await response.json(); } catch (_) { /* HTML or empty body */ }
    const err = (body && body.error) || {};
    if (err.message) return err.hint ? `${err.message} ${err.hint}` : err.message;
    return `Request failed (${response.status}${
      response.statusText ? " " + response.statusText : ""})`;
  }

  async function api(path, options = {}) {
    const response = await nmFetch(path, options);
    if (!response.ok) {
      const error = new Error(await readError(response));
      error.status = response.status;
      throw error;
    }
    try { return await response.json(); } catch (_) { return null; }
  }

  /* ---------------- markdown (sanitized) ----------------------------------- */
  function md(text) {
    const html = marked.parse(text || "", { breaks: true, mangle: false,
                                            headerIds: false });
    return DOMPurify.sanitize(html, { ADD_ATTR: ["target"] });
  }

  /* ---------------- misc ---------------------------------------------------- */
  function timeAgo(iso) {
    if (!iso) return "";
    const secs = Math.max(0, (Date.now() - new Date(iso + "Z").getTime()) / 1000);
    if (secs < 60) return "just now";
    if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
    return new Date(iso + "Z").toLocaleDateString();
  }
  function esc(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  /* ---------------- boot ------------------------------------------------------ */
  document.addEventListener("DOMContentLoaded", () => {
    themeApply();
    const toggle = document.getElementById("nm-theme-toggle");
    if (toggle) toggle.addEventListener("click", themeCycle);

    // Live / Demo badge in the topbar.
    api("/api/health").then((h) => {
      const badge = document.getElementById("nm-mode-badge");
      if (!badge) return;
      const live = h.mode === "live";
      badge.className = "nm-badge " + (live ? "nm-badge-green" : "nm-badge-amber");
      badge.innerHTML = `<i class="bi ${live ? "bi-cloud-check" : "bi-cpu"}"></i> ` +
        (live ? "IBM Live" : "Demo mode");
      badge.title = h.mode_detail || "";
    }).catch(() => {});

    // Sample prompt chips: fill the chat input, or navigate to /chat.
    document.body.addEventListener("click", (event) => {
      const chip = event.target.closest("[data-prompt]");
      if (!chip) return;
      const prompt = chip.dataset.prompt;
      const input = document.getElementById("chat-input");
      if (input) {
        input.value = prompt;
        input.focus();
      } else {
        window.location.href = "/chat?prompt=" + encodeURIComponent(prompt);
      }
    });

    // "/" focuses the chat input when present (and not typing already).
    document.addEventListener("keydown", (event) => {
      if (event.key === "/" && !/(INPUT|TEXTAREA)/.test(document.activeElement.tagName)) {
        const input = document.getElementById("chat-input");
        if (input) { event.preventDefault(); input.focus(); }
      }
    });
  });

  return { toast, confirmDialog, api, fetch: nmFetch, readError, md, timeAgo, esc };
})();
