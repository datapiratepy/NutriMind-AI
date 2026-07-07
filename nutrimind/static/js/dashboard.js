/* Dashboard: renders /api/dashboard/summary, /api/system/info, /api/documents. */
"use strict";

(async function () {
  const $ = (id) => document.getElementById(id);

  function macroRow(label, value, target, unit) {
    const pct = target ? Math.min(100, Math.round(value / target * 100)) : 0;
    return `<div class="mb-2">
      <div class="d-flex justify-content-between" style="font-size:.85rem">
        <span>${label}</span><span class="text-2">${Math.round(value)}${target ? " / " + Math.round(target) : ""} ${unit}</span>
      </div>
      <div class="nm-progress progress" role="progressbar" aria-label="${label}">
        <div class="progress-bar" style="width:${pct}%"></div></div></div>`;
  }

  try {
    const dash = await NM.api("/api/dashboard/summary");
    if (!dash.profile_exists) $("dash-noprofile").classList.remove("d-none");

    // BMI
    if (dash.bmi) {
      $("dash-bmi").textContent = dash.bmi.bmi;
      $("dash-bmi-detail").textContent =
        `${dash.bmi.category} · healthy ${dash.bmi.ideal_weight_min_kg}–${dash.bmi.ideal_weight_max_kg} kg`;
    } else {
      $("dash-bmi").textContent = "—";
      $("dash-bmi-detail").textContent = "no measurement yet";
    }

    // Health score with explainable components
    const score = dash.health_score;
    $("dash-score").textContent = `${score.total}/100`;
    $("dash-score-bar").style.width = score.total + "%";
    $("dash-score-detail").innerHTML = score.components.map((c) =>
      `<div class="d-flex justify-content-between">
        <span>${NM.esc(c.name)}</span>
        <span class="text-2">${c.points}/${c.max_points}</span></div>
       <div class="text-2 mb-1" style="font-size:.76rem">${NM.esc(c.detail)}</div>`).join("");
    $("dash-score-why").addEventListener("click", (e) => {
      const open = $("dash-score-detail").classList.toggle("d-none") === false;
      e.currentTarget.setAttribute("aria-expanded", String(open));
    });

    // Calories + water
    const target = dash.targets ? dash.targets.calories : 0;
    $("dash-cal").textContent = Math.round(dash.today.calories);
    if (target) {
      $("dash-cal-bar").style.width =
        Math.min(100, dash.today.calories / target * 100) + "%";
      $("dash-cal-target").textContent = `target ${target} kcal`;
    }
    $("dash-water").textContent = dash.today.water_glasses;

    // Targets card
    if (dash.targets) {
      $("dash-targets").innerHTML =
        macroRow("Protein", dash.today.protein_g, dash.targets.protein_g, "g") +
        macroRow("Fat", dash.today.fat_g, dash.targets.fat_g, "g") +
        macroRow("Carbs", dash.today.carbs_g, dash.targets.carbs_g, "g") +
        macroRow("Fiber", dash.today.fiber_g, dash.targets.fiber_g, "g") +
        `<div class="text-2 mt-2" style="font-size:.8rem">Water target
          ${dash.targets.water_l} L · consumed today vs daily target</div>`;
    } else {
      $("dash-targets").innerHTML =
        `<div class="nm-empty py-3">Create a <a href="/profile">profile</a> to see targets.</div>`;
    }

    $("dash-water-add").addEventListener("click", async () => {
      try {
        const r = await NM.api("/api/water", { method: "POST", json: { glasses: 1 } });
        $("dash-water").textContent = r.water.glasses;
        NM.toast("Water logged", "success");
      } catch (e) { NM.toast(e.message, "error"); }
    });

    // Weekly trends: dependency-free bars; honest empty state for sparse data.
    const series = dash.week.calorie_series;
    const daysWithData = series.filter((d) => d.calories > 0).length;
    if (daysWithData >= 2) {
      const maxCal = Math.max(...series.map((d) => d.calories), target || 0);
      const bars = series.map((d) => {
        const calHeight = maxCal ? Math.round(d.calories / maxCal * 100) : 0;
        const waterHeight = Math.round(Math.min(d.water_glasses, 12) / 12 * 100);
        const day = new Date(d.date + "T00:00:00Z")
          .toLocaleDateString([], { weekday: "short" });
        return `<div class="nm-bar-col" title="${d.date}: ${Math.round(d.calories)} kcal, ${d.water_glasses} glasses">
          <div class="d-flex align-items-end gap-1 w-100 justify-content-center" style="height:100%">
            <div class="nm-bar ${d.calories ? "" : "muted"}" style="height:${calHeight}%"></div>
            <div class="nm-bar water" style="height:${waterHeight}%;max-width:14px"></div>
          </div>
          <span class="nm-bar-label">${day}</span></div>`;
      }).join("");
      $("dash-trends").innerHTML = `<div class="nm-bars" role="img"
          aria-label="Daily calories and water for the last 7 days">${bars}</div>` +
        (target ? `<div class="text-2 mt-2" style="font-size:.78rem">
          Daily target: ${target} kcal · hover a day for exact values</div>` : "");
    } else {
      $("dash-trends").innerHTML = `<div class="nm-empty py-4">
        <i class="bi bi-bar-chart"></i>Log meals on a couple of days and the
        weekly trend will appear here — no fake charts.
        <div class="mt-2"><a class="btn btn-sm btn-outline-secondary" href="/analyzer">
          Log today's meals</a></div></div>`;
    }

    // AI activity card
    const ai = dash.week.ai_activity;
    const groundedPct = ai.responses
      ? Math.round(ai.grounded / ai.responses * 100) : 0;
    $("dash-ai").innerHTML = ai.responses ? `
      <div class="d-flex justify-content-between py-1"><span class="text-2">AI responses</span>
        <strong>${ai.responses}</strong></div>
      <div class="d-flex justify-content-between py-1"><span class="text-2">Grounded in documents</span>
        <strong>${ai.grounded} (${groundedPct}%)</strong></div>
      <div class="d-flex justify-content-between py-1"><span class="text-2">Tokens used</span>
        <strong>${ai.tokens_used.toLocaleString()}</strong></div>
      <div class="d-flex justify-content-between py-1"><span class="text-2">Meals logged today</span>
        <strong>${dash.week.meals_today}</strong></div>
      <div class="pt-1"><span class="text-2" style="font-size:.8rem">Agents used:</span>
        ${ai.agents.map((a) => `<span class="nm-badge nm-badge-accent ms-1">${NM.esc(a.replace("_", " "))}</span>`).join("")}</div>`
      : `<div class="nm-empty py-4"><i class="bi bi-chat-square-text"></i>
         No AI activity yet — <a href="/chat">ask your first question</a>.</div>`;
  } catch (e) { NM.toast("Could not load dashboard: " + e.message, "error"); }

  // Recent meals
  try {
    const { meals } = await NM.api("/api/meals?days=7");
    $("dash-meals").innerHTML = meals.length ? meals.slice(-5).reverse().map((m) =>
      `<div class="d-flex justify-content-between py-1 border-bottom" style="font-size:.88rem">
        <span class="text-truncate me-2">${NM.esc((m.items || []).map(i => i.food).join(", ") || m.raw_text || m.meal_type)}</span>
        <span class="text-2 flex-shrink-0">${Math.round(m.calories)} kcal · ${NM.timeAgo(m.ts)}</span>
      </div>`).join("") :
      `<div class="nm-empty py-3"><i class="bi bi-clipboard-data"></i>
        No meals logged yet — try the <a href="/analyzer">Analyzer</a>.</div>`;
  } catch (e) { $("dash-meals").innerHTML = ""; }

  // Recent conversations
  try {
    const { sessions } = await NM.api("/api/chat/sessions");
    $("dash-chats").innerHTML = sessions.length ? sessions.slice(0, 5).map((s) =>
      `<div class="d-flex justify-content-between py-1 border-bottom" style="font-size:.88rem">
        <span class="text-truncate me-2">${NM.esc(s.preview)}</span>
        <span class="text-2 flex-shrink-0">${s.messages} msgs · ${NM.timeAgo(s.last_at)}</span>
      </div>`).join("") :
      `<div class="nm-empty py-3"><i class="bi bi-chat-square-text"></i>
        No conversations yet — <a href="/chat">start one</a>.</div>`;
  } catch (e) { $("dash-chats").innerHTML = ""; }

  // System + knowledge status
  try {
    const [info, docs] = await Promise.all([
      NM.api("/api/system/info"), NM.api("/api/documents")]);
    const chroma = typeof info.storage.chroma === "object" ? info.storage.chroma : null;
    const indexed = docs.documents.filter((d) => d.status === "indexed").length;
    const line = (label, value, good) => `<div class="d-flex justify-content-between py-1">
      <span class="text-2">${label}</span><span>${good === undefined ? value :
        `<span class="nm-badge ${good ? "nm-badge-green" : "nm-badge-amber"}">${value}</span>`}</span></div>`;
    $("dash-system").innerHTML =
      line("Mode", info.mode.demo_active ? "Demo" : "IBM Live", !info.mode.demo_active) +
      line("Chat model", `<code>${NM.esc(info.ibm.chat_model)}</code>`) +
      line("Embeddings", NM.esc(chroma ? chroma.provider : "—")) +
      line("Database", info.storage.database, info.storage.database === "ok") +
      line("Vector store", chroma ? `${chroma.chunks} chunks` : "unavailable", !!chroma) +
      line("Documents indexed", `${indexed} of ${docs.documents.length}`);
  } catch (e) { $("dash-system").innerHTML = ""; }
})();
