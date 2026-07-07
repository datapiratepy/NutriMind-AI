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
  async function api(path, options = {}) {
    if (options.json !== undefined) {
      options.body = JSON.stringify(options.json);
      options.headers = Object.assign({ "Content-Type": "application/json" },
                                      options.headers);
      delete options.json;
    }
    const response = await fetch(path, options);
    let body = null;
    try { body = await response.json(); } catch (_) { /* non-JSON */ }
    if (!response.ok) {
      const err = (body && body.error) || {};
      const message = err.hint ? `${err.message} ${err.hint}` :
        (err.message || `Request failed (${response.status})`);
      const error = new Error(message);
      error.status = response.status;
      error.code = err.code;
      throw error;
    }
    return body;
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

  return { toast, confirmDialog, api, md, timeAgo, esc };
})();
