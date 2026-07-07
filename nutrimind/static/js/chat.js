/* NutriMind chat client: SSE streaming, agent badges, workflow panel.
   Consumes the documented event protocol (docs/AGENTS.md §6) verbatim —
   nothing is inferred client-side. */
"use strict";

(function () {
  const AGENT_LABELS = { coordinator: "Coordinator", knowledge_agent: "Knowledge Agent",
    meal_planner: "Meal Planner", meal_analyzer: "Meal Analyzer",
    health_advisor: "Health Advisor" };

  const scroll = document.getElementById("chat-scroll");
  const messages = document.getElementById("chat-messages");
  const emptyState = document.getElementById("chat-empty");
  const statusLine = document.getElementById("chat-status");
  const statusText = document.getElementById("chat-status-text");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendBtn = document.getElementById("chat-send");

  let sessionId = sessionStorage.getItem("nm-session") || null;
  let busy = false;

  /* ---------------- rendering -------------------------------------------- */

  function autoScroll() { scroll.scrollTop = scroll.scrollHeight; }

  function addUserMessage(text) {
    emptyState.classList.add("d-none");
    const el = document.createElement("div");
    el.className = "nm-msg nm-msg-user";
    el.innerHTML = `<div class="nm-msg-body"></div>
      <span class="text-2 mt-1" style="font-size:.72rem">${new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}</span>`;
    el.querySelector(".nm-msg-body").textContent = text;
    messages.appendChild(el);
    autoScroll();
  }

  function badge(cls, icon, text, title) {
    return `<span class="nm-badge ${cls}" ${title ? `title="${NM.esc(title)}"` : ""}>
      <i class="bi ${icon}" aria-hidden="true"></i> ${NM.esc(text)}</span>`;
  }

  function workflowHTML(meta) {
    const steps = [["You", false], ["Coordinator · " + meta.routing.method, false]];
    if (meta.agent !== "coordinator") steps.push([AGENT_LABELS[meta.agent] || meta.agent, true]);
    (meta.tools_used || []).forEach((t) => {
      if (t.tool === "retrieve_knowledge")
        steps.push([`Retriever · ${meta.retrieved_chunks} chunks`, false]);
      if (t.tool === "calculate_targets") steps.push(["Nutrition Service", false]);
      if (t.tool === "lookup_foods") steps.push(["Food Table", false]);
      if (t.tool === "compute_bmi") steps.push(["BMI Service", false]);
    });
    if (meta.tokens.total > 0 || meta.tokens.estimated)
      steps.push([meta.llm_mode === "live" ? "IBM Granite" : "Demo engine", true]);
    steps.push(["Response", false]);
    return `<div class="nm-flow" aria-label="AI workflow">` + steps.map(([label, accent], i) =>
      (i ? `<span class="nm-flow-arrow" aria-hidden="true">→</span>` : "") +
      `<span class="nm-flow-step${accent ? " accent" : ""}">${NM.esc(label)}</span>`
    ).join("") + `</div>`;
  }

  function metaPanelHTML(meta) {
    const rows = [];
    rows.push(`<div class="mb-2">${workflowHTML(meta)}</div>`);
    rows.push(`<div><strong>Routing</strong> · ${NM.esc(meta.routing.intent)}
      <span class="text-2">(${NM.esc(meta.routing.method)})</span> —
      ${NM.esc(meta.routing.reason)}</div>`);
    if ((meta.tools_used || []).length)
      rows.push(`<div class="mt-1"><strong>Tools</strong> · ` + meta.tools_used
        .map((t) => `<code>${NM.esc(t.tool)}</code> <span class="text-2">${NM.esc(t.summary)}</span>`)
        .join(" · ") + `</div>`);
    if ((meta.citations || []).length)
      rows.push(`<div class="mt-2 d-flex flex-wrap gap-1"><strong class="me-1">Sources</strong>` +
        meta.citations.map((c) => `<span class="nm-cite"><i class="bi bi-file-earmark-text"></i>
          ${NM.esc(c.filename)} · p.${c.page}</span>`).join("") + `</div>`);
    rows.push(`<div class="mt-2 text-2">Embedding provider: ${NM.esc(meta.embedding_provider)}
      · LLM: ${NM.esc(meta.llm_mode)} · ${meta.generation_ms} ms ·
      ${meta.tokens.total}${meta.tokens.estimated ? " (est.)" : ""} tokens</div>`);
    return rows.join("");
  }

  function addAssistantShell() {
    const el = document.createElement("div");
    el.className = "nm-msg nm-msg-assistant";
    el.innerHTML = `<div class="nm-msg-card">
        <div class="nm-msg-head"><span class="nm-badge nm-badge-accent">
          <i class="bi bi-robot"></i> NutriMind</span></div>
        <div class="nm-msg-content nm-typing"></div>
        <div class="nm-msg-foot d-none">
          <button class="btn btn-sm btn-link p-0 text-2 nm-copy" title="Copy response">
            <i class="bi bi-clipboard"></i> Copy</button>
          <button class="btn btn-sm btn-link p-0 text-2 nm-details"
                  aria-expanded="false"><i class="bi bi-info-circle"></i> How this was answered</button>
          <span class="ms-auto nm-time"></span>
        </div>
        <div class="nm-meta-panel d-none"></div>
      </div>`;
    messages.appendChild(el);
    autoScroll();
    return el;
  }

  function finalizeAssistant(el, text, meta) {
    const head = el.querySelector(".nm-msg-head");
    const content = el.querySelector(".nm-msg-content");
    const foot = el.querySelector(".nm-msg-foot");
    const panel = el.querySelector(".nm-meta-panel");

    content.classList.remove("nm-typing");
    content.innerHTML = NM.md(text);

    const badges = [badge("nm-badge-accent", "bi-robot",
                          AGENT_LABELS[meta.agent] || meta.agent, meta.routing.reason)];
    badges.push(meta.grounded
      ? badge("nm-badge-green", "bi-patch-check", "Grounded", "Answer grounded in your documents")
      : badge("", "bi-lightbulb", meta.response_source.replace(/_/g, " "),
              "Not grounded in knowledge-base documents"));
    if ((meta.citations || []).length)
      badges.push(badge("", "bi-file-earmark-text",
        `${meta.citations.length} source${meta.citations.length > 1 ? "s" : ""}`));
    if (meta.llm_mode === "demo") badges.push(badge("nm-badge-amber", "bi-cpu", "Demo"));
    head.innerHTML = badges.join(" ");

    foot.classList.remove("d-none");
    foot.querySelector(".nm-time").textContent =
      new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    panel.innerHTML = metaPanelHTML(meta);

    foot.querySelector(".nm-copy").addEventListener("click", async (e) => {
      // Copy clean markdown: strip the demo-mode footer if present.
      const clean = text.replace(/\n+_\(Demo mode:[\s\S]*?\)_\s*$/, "").trim();
      await navigator.clipboard.writeText(clean);
      const btn = e.currentTarget;
      btn.innerHTML = `<i class="bi bi-clipboard-check"></i> Copied`;
      setTimeout(() => { btn.innerHTML = `<i class="bi bi-clipboard"></i> Copy`; }, 1500);
    });
    foot.querySelector(".nm-details").addEventListener("click", (e) => {
      const open = panel.classList.toggle("d-none") === false;
      e.currentTarget.setAttribute("aria-expanded", String(open));
      autoScroll();
    });
    autoScroll();
  }

  /* ---------------- SSE over fetch ---------------------------------------- */

  async function send(message) {
    busy = true;
    sendBtn.disabled = true;
    addUserMessage(message);
    statusLine.classList.remove("d-none");
    statusText.textContent = "Contacting the coordinator…";

    let shell = null;
    let streamed = "";
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId, stream: true }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error((body && body.error && body.error.message) ||
                        `Request failed (${response.status})`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const raw = buffer.slice(0, idx); buffer = buffer.slice(idx + 2);
          const eventMatch = raw.match(/^event: (.+)$/m);
          const dataMatch = raw.match(/^data: (.+)$/m);
          if (!eventMatch || !dataMatch) continue;
          handleEvent(eventMatch[1], JSON.parse(dataMatch[1]));
        }
      }
    } catch (error) {
      statusLine.classList.add("d-none");
      if (shell) shell.remove();
      NM.toast(error.message, "error");
    } finally {
      busy = false;
      sendBtn.disabled = false;
      input.focus();
    }

    function handleEvent(kind, data) {
      if (kind === "status") {
        statusText.textContent = data.message;
      } else if (kind === "routing") {
        statusText.textContent =
          `${data.intent} → ${AGENT_LABELS[data.agent] || data.agent} (${data.method})`;
      } else if (kind === "token") {
        if (!shell) shell = addAssistantShell();
        streamed += data.text;
        shell.querySelector(".nm-msg-content").innerHTML = NM.md(streamed);
        shell.querySelector(".nm-msg-content").classList.add("nm-typing");
        autoScroll();
      } else if (kind === "final") {
        statusLine.classList.add("d-none");
        if (!shell) shell = addAssistantShell();
        sessionId = data.session_id;
        sessionStorage.setItem("nm-session", sessionId);
        finalizeAssistant(shell, data.text, data.meta);
      } else if (kind === "error") {
        statusLine.classList.add("d-none");
        if (shell) shell.remove();
        NM.toast(data.hint ? `${data.message} ${data.hint}` : data.message, "error");
      }
    }
  }

  /* ---------------- history restore + input wiring -------------------------- */

  async function restoreHistory() {
    if (!sessionId) return;
    try {
      const body = await NM.api(`/api/chat/history?session_id=${sessionId}`);
      if (!body.messages.length) return;
      emptyState.classList.add("d-none");
      body.messages.forEach((m) => {
        if (m.role === "user") { addUserMessage(m.content); return; }
        const shell = addAssistantShell();
        finalizeAssistant(shell, m.content, {
          agent: m.agent || "coordinator", grounded: m.rag_used,
          response_source: m.rag_used ? "grounded" : "general_knowledge",
          citations: m.sources || [], retrieved_chunks: (m.sources || []).length,
          tools_used: [], embedding_provider: "—", llm_mode: "—",
          generation_ms: 0, tokens: { total: m.tokens_used, estimated: false },
          routing: { intent: "Restored", method: "history",
                     reason: "Loaded from conversation history." },
        });
      });
      document.getElementById("chat-session-note").textContent =
        "Restored previous conversation.";
    } catch (_) { /* fresh session */ }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || busy) return;
    input.value = "";
    input.style.height = "auto";
    send(message);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  });

  /* scroll-to-bottom button: show when the user has scrolled up */
  const jump = document.getElementById("chat-jump");
  scroll.addEventListener("scroll", () => {
    const nearBottom =
      scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 120;
    jump.classList.toggle("d-none", nearBottom);
  });
  jump.addEventListener("click", () => {
    scroll.scrollTo({ top: scroll.scrollHeight, behavior: "smooth" });
  });

  const params = new URLSearchParams(window.location.search);

const sessionFromUrl = params.get("session_id");

if (sessionFromUrl) {
  sessionId = sessionFromUrl;
  sessionStorage.setItem("nm-session", sessionId);
}

restoreHistory().then(() => {
    const prompt = params.get("prompt");
    if (prompt) {
        input.value = prompt;
        input.focus();
    }
});
})();
