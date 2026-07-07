/* Profile wizard: 3 steps, live BMI preview, PUT /api/profile. */
"use strict";

(function () {
  const form = document.getElementById("profile-form");
  const steps = [...form.querySelectorAll("fieldset[data-step]")];
  const dots = [...document.querySelectorAll(".nm-step-dot")];
  const back = document.getElementById("pf-back");
  const next = document.getElementById("pf-next");
  const save = document.getElementById("pf-save");
  let current = 0;

  function show(index) {
    current = index;
    steps.forEach((s, i) => s.classList.toggle("d-none", i !== index));
    dots.forEach((d, i) => {
      d.classList.toggle("active", i === index);
      d.classList.toggle("done", i < index);
    });
    back.classList.toggle("d-none", index === 0);
    next.classList.toggle("d-none", index === steps.length - 1);
    save.classList.toggle("d-none", index !== steps.length - 1);
  }

  function validStep(index) {
    let valid = true;
    steps[index].querySelectorAll("[required]").forEach((el) => {
      const fieldOk = el.checkValidity();
      el.classList.toggle("is-invalid", !fieldOk);
      if (!fieldOk) valid = false;
    });
    return valid;
  }

  next.addEventListener("click", () => { if (validStep(current)) show(current + 1); });
  back.addEventListener("click", () => show(current - 1));

  /* live BMI preview (same formula as the backend, preview only) */
  const height = document.getElementById("pf-height");
  const weight = document.getElementById("pf-weight");
  const bmiBox = document.getElementById("pf-bmi-live");
  function updateBMI() {
    const h = parseFloat(height.value), w = parseFloat(weight.value);
    if (!(h >= 50 && h <= 272 && w >= 20 && w <= 350)) return;
    const bmi = Math.round(w / ((h / 100) ** 2) * 10) / 10;
    const category = bmi < 18.5 ? "underweight" : bmi < 25 ? "normal" :
                     bmi < 30 ? "overweight" : "obese";
    bmiBox.innerHTML = `<i class="bi bi-calculator me-1"></i>
      Live BMI preview: <strong>${bmi}</strong>
      <span class="nm-badge ${category === "normal" ? "nm-badge-green" : "nm-badge-amber"} ms-1">${category}</span>
      <span class="text-2 ms-1" style="font-size:.8rem">final value is computed
        and tracked server-side on save</span>`;
  }
  height.addEventListener("input", updateBMI);
  weight.addEventListener("input", updateBMI);

  /* prefill from an existing profile */
  NM.api("/api/profile").then(({ profile }) => {
    if (!profile) return;
    const map = { "pf-name": "name", "pf-age": "age", "pf-gender": "gender",
      "pf-height": "height_cm", "pf-weight": "weight_kg",
      "pf-activity": "activity_level", "pf-diet": "food_preference",
      "pf-goal": "weight_goal", "pf-country": "country",
      "pf-calorie": "daily_calorie_goal" };
    Object.entries(map).forEach(([id, key]) => {
      if (profile[key] != null) document.getElementById(id).value = profile[key];
    });
    document.getElementById("pf-conditions").value =
      (profile.medical_conditions || []).join(", ");
    document.getElementById("pf-allergies").value =
      (profile.allergies || []).join(", ");
    updateBMI();
  }).catch(() => {});

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!validStep(current)) return;
    const csv = (id) => document.getElementById(id).value
      .split(",").map((s) => s.trim()).filter(Boolean);
    const payload = {
      name: document.getElementById("pf-name").value.trim(),
      age: Number(document.getElementById("pf-age").value),
      gender: document.getElementById("pf-gender").value,
      height_cm: Number(document.getElementById("pf-height").value),
      weight_kg: Number(document.getElementById("pf-weight").value),
      activity_level: document.getElementById("pf-activity").value,
      food_preference: document.getElementById("pf-diet").value,
      weight_goal: document.getElementById("pf-goal").value,
      medical_conditions: csv("pf-conditions"),
      allergies: csv("pf-allergies"),
    };
    const country = document.getElementById("pf-country").value.trim();
    if (country) payload.country = country;
    const calories = document.getElementById("pf-calorie").value;
    if (calories) payload.daily_calorie_goal = Number(calories);
    try {
      save.disabled = true;
      await NM.api("/api/profile", { method: "PUT", json: payload });
      NM.toast("Profile saved — BMI recorded", "success");
      setTimeout(() => (window.location.href = "/dashboard"), 700);
    } catch (error) {
      NM.toast(error.message, "error");
    } finally { save.disabled = false; }
  });

  show(0);
})();
