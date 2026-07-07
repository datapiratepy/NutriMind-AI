/* Meal analyzer page: non-streaming agent call, deterministic numbers UI. */
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);

  $("an-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = $("an-input").value.trim();
    if (!text) return;
    // Ensure the analyzer rule matches even for bare food lists.
    const message = /\b(i ate|i had|analyze)\b/i.test(text)
      ? text : `Today I ate ${text}`;
    $("an-submit").disabled = true;
    $("an-progress").classList.remove("d-none");
    $("an-progress-text").textContent =
      "Extracting foods and computing nutrition from the food table…";
    try {
      const body = await NM.api("/api/chat", { method: "POST",
                                               json: { message, stream: false } });
      const meta = body.meta;
      if (meta.agent !== "meal_analyzer")
        throw new Error("That didn't look like a meal description — try " +
                        "'2 rotis, dal and an apple'.");
      $("an-result").classList.remove("d-none");
      $("an-content").innerHTML = NM.md(body.reply);
      $("an-quality").innerHTML = meta.quality_score != null
        ? `<span class="nm-badge ${meta.quality_score >= 60 ? "nm-badge-green" : "nm-badge-amber"}">
             quality ${meta.quality_score}/100</span>`
        : "";
      const totals = meta.totals || {};
      const chip = (label, value, unit) => `<span class="nm-badge">
        <strong>${Math.round(value || 0)}${unit}</strong>&nbsp;${label}</span>`;
      $("an-macros").innerHTML =
        chip("calories", totals.calories, "") + chip("protein", totals.protein_g, " g") +
        chip("fat", totals.fat_g, " g") + chip("carbs", totals.carbs_g, " g") +
        chip("fiber", totals.fiber_g, " g");
      $("an-unmatched").innerHTML = (meta.unmatched || []).length
        ? `<div class="nm-badge nm-badge-amber"><i class="bi bi-question-circle"></i>
             Not recognized (not counted): ${meta.unmatched.map(NM.esc).join(", ")}</div>`
        : "";
      if (meta.quality_score != null) NM.toast("Meal logged", "success");
      $("an-result").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      NM.toast(error.message, "error");
    } finally {
      $("an-submit").disabled = false;
      $("an-progress").classList.add("d-none");
    }
  });
})();
