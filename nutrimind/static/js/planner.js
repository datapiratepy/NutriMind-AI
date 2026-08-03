/* Meal planner page: targets, generation via the agent API, saved plans. */
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  let currentPlanId = null;

  function chip(label, value) {
    return `<span class="nm-badge"><strong>${value}</strong>&nbsp;${label}</span>`;
  }

  async function loadTargets() {
    try {
      const { targets } = await NM.api("/api/targets");
      $("plan-targets").innerHTML =
        chip("kcal", targets.calories) + chip("protein", targets.protein_g + " g") +
        chip("fat", targets.fat_g + " g") + chip("carbs", targets.carbs_g + " g") +
        chip("fiber", targets.fiber_g + " g") + chip("water", targets.water_l + " L");
    } catch (error) {
      $("plan-targets").innerHTML = `<span class="text-2">
        ${NM.esc(error.message)} <a href="/profile">Create profile →</a></span>`;
      $("plan-generate").disabled = true;
    }
  }

  function renderPlan(plan, meta) {
    $("plan-title").textContent = plan.title;
    currentPlanId = meta.plan_id;
    $("plan-mode-badge").innerHTML = meta.llm_mode === "demo"
      ? `<span class="nm-badge nm-badge-amber"><i class="bi bi-cpu"></i> Demo</span>`
      : `<span class="nm-badge nm-badge-green"><i class="bi bi-cloud-check"></i> IBM Granite</span>`;
    $("plan-meals").innerHTML = plan.meals.map((meal) => `
      <div class="border-bottom py-2" style="border-color:var(--nm-border)!important">
        <div class="d-flex justify-content-between align-items-baseline">
          <strong>${NM.esc(meal.name)}</strong>
          <span class="text-2" style="font-size:.83rem">
            ≈ ${meal.calories} kcal · ${meal.protein_g} g protein</span>
        </div>
        <span class="text-2" style="font-size:.9rem">
          ${meal.items.map((i) => NM.esc(`${i.portion} ${i.food}`.trim())).join(" · ")}</span>
      </div>`).join("");
    const estimated = plan.meals.reduce((sum, m) => sum + (m.calories || 0), 0);
    $("plan-summary").innerHTML =
      `<div><strong>Nutrition summary</strong> · meals sum to ≈ ${estimated} kcal
        vs the deterministic ${meta.targets.calories} kcal target
        <span class="text-2">(per-meal numbers are the model's estimates)</span></div>
       ${plan.hydration ? `<div class="mt-1"><strong>Hydration</strong> · ${NM.esc(plan.hydration)}</div>` : ""}
       ${plan.notes ? `<div class="mt-1 text-2">${NM.esc(plan.notes)}</div>` : ""}`;
    $("plan-result").classList.remove("d-none");
  }

  $("plan-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const extra = $("plan-note").value.trim();
    $("plan-generate").disabled = true;
    $("plan-progress").classList.remove("d-none");
    $("plan-progress-text").textContent = "Computing targets and composing your plan…";
    try {
      const body = await NM.api("/api/chat", { method: "POST", json: {
        message: "Create a meal plan for me" + (extra ? ` — ${extra}` : ""),
        stream: false } });
      if (!body.meta.plan_id) throw new Error(body.reply);
      const detail = await NM.api(`/api/meal-plans/${body.meta.plan_id}`);
      renderPlan(detail.plan.plan, body.meta);
      NM.toast("Plan generated and saved", "success");
      loadSaved();
    } catch (error) {
      NM.toast(error.message, "error");
    } finally {
      $("plan-generate").disabled = false;
      $("plan-progress").classList.add("d-none");
    }
  });

  $("plan-pdf").addEventListener("click", async () => {
    if (!currentPlanId) return;
    const button = $("plan-pdf");
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = `<span class="spinner-border spinner-border-sm me-1"
      role="status" aria-hidden="true"></span>Generating…`;
    try {
      const response = await NM.fetch(`/api/export/meal-plan/${currentPlanId}.pdf`);
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error((body && body.error && body.error.message) ||
                        "PDF export failed — please try again.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = Object.assign(document.createElement("a"),
        { href: url, download: `nutrimind-plan-${currentPlanId}.pdf` });
      a.click();
      URL.revokeObjectURL(url);
      NM.toast("PDF downloaded", "success");
    } catch (error) { NM.toast(error.message, "error"); }
    finally { button.disabled = false; button.innerHTML = original; }
  });

  async function loadSaved() {
    try {
      const { plans } = await NM.api("/api/meal-plans");
      $("plan-saved").innerHTML = plans.length ? plans.map((p) => `
        <div class="d-flex justify-content-between align-items-center py-1 border-bottom"
             style="border-color:var(--nm-border)!important;font-size:.9rem">
          <button class="btn btn-link btn-sm p-0 text-start" data-open="${p.id}">
            ${NM.esc(p.title)}</button>
          <span class="text-2 flex-shrink-0">${p.targets.calories || "—"} kcal ·
            ${NM.timeAgo(p.created_at)}</span>
        </div>`).join("") :
        `<div class="nm-empty py-3"><i class="bi bi-calendar-plus"></i>
          No saved plans yet — generate your first one above.</div>`;
    } catch (_) { $("plan-saved").innerHTML = ""; }
  }
  $("plan-saved").addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-open]");
    if (!btn) return;
    try {
      const detail = await NM.api(`/api/meal-plans/${btn.dataset.open}`);
      renderPlan(detail.plan.plan, {
        plan_id: detail.plan.id, llm_mode: "—",
        targets: detail.plan.targets });
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) { NM.toast(error.message, "error"); }
  });

  loadTargets();
  loadSaved();
})();
