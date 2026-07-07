"""PDF export of saved meal plans (ReportLab platypus).

Produces a clean, branded one-page document: header band, user context,
deterministic daily targets, per-meal tables, hydration, AI notes, timestamp
and disclaimer. Pure function over the ``MealPlan`` row — no HTTP concerns.
"""

from __future__ import annotations

import datetime as dt
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_ACCENT = colors.HexColor("#0f62fe")
_INK = colors.HexColor("#161616")
_MUTED = colors.HexColor("#55595f")
_LINE = colors.HexColor("#dde2e8")
_SOFT = colors.HexColor("#eef3ff")

_BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5,
                       leading=13, textColor=_INK)
_H_TITLE = ParagraphStyle("title", parent=_BASE, fontName="Helvetica-Bold",
                          fontSize=17, textColor=colors.white)
_H_SUB = ParagraphStyle("subtitle", parent=_BASE, fontSize=9,
                        textColor=colors.HexColor("#d0defb"))
_H_MEAL = ParagraphStyle("meal", parent=_BASE, fontName="Helvetica-Bold",
                         fontSize=11, textColor=_ACCENT, spaceBefore=10)
_SMALL = ParagraphStyle("small", parent=_BASE, fontSize=8, leading=11,
                        textColor=_MUTED)

_DISCLAIMER = (
    "NutriMind AI is an educational tool built with IBM watsonx.ai and Granite. "
    "Daily targets are computed deterministically (Mifflin-St Jeor); per-meal "
    "values are AI estimates. This document is not medical advice — consult a "
    "clinician or registered dietitian for personal health decisions."
)


def _header() -> Table:
    inner = Table(
        [[Paragraph("NutriMind AI", _H_TITLE)],
         [Paragraph("Personalized Meal Plan · IBM watsonx.ai + Granite + RAG", _H_SUB)]],
        colWidths=[170 * mm])
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (0, 0), 10),
        ("BOTTOMPADDING", (0, 1), (0, 1), 10),
    ]))
    return inner


def _targets_table(targets: dict) -> Table:
    labels = ["Calories", "Protein", "Fat", "Carbs", "Fiber", "Water"]
    values = [f"{targets.get('calories', '—')} kcal",
              f"{targets.get('protein_g', '—')} g",
              f"{targets.get('fat_g', '—')} g",
              f"{targets.get('carbs_g', '—')} g",
              f"{targets.get('fiber_g', '—')} g",
              f"{targets.get('water_l', '—')} L"]
    table = Table([labels, values], colWidths=[170 * mm / 6] * 6)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _SOFT),
        ("TEXTCOLOR", (0, 0), (-1, 0), _MUTED),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, _LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _meal_block(meal: dict) -> list:
    rows = [[Paragraph(f"<b>{item.get('food', '')}</b>", _BASE),
             Paragraph(item.get("portion", "") or "—", _BASE)]
            for item in meal.get("items", [])]
    table = Table(rows or [[Paragraph("—", _BASE), Paragraph("—", _BASE)]],
                  colWidths=[120 * mm, 50 * mm])
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, _LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    estimate = Paragraph(
        f"≈ {meal.get('calories', '—')} kcal · {meal.get('protein_g', '—')} g "
        "protein <font color='#55595f' size='7'>(AI estimate)</font>", _SMALL)
    return [Paragraph(meal.get("name", "Meal"), _H_MEAL), table,
            Spacer(0, 1.5 * mm), estimate]


def build_meal_plan_pdf(plan_row, profile=None) -> bytes:
    """Render a saved ``MealPlan`` row (with a ``plan`` JSON dict) to PDF bytes."""
    plan = plan_row.plan or {}
    targets = plan_row.targets or {}
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=plan.get("title", "NutriMind meal plan"), author="NutriMind AI")

    generated = dt.datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")
    user_line = "Personalized plan"
    if profile is not None:
        user_line = (f"For <b>{profile.name}</b> · {profile.age} y · "
                     f"{profile.food_preference.replace('_', '-')} · goal: "
                     f"{profile.weight_goal}")

    story = [
        _header(), Spacer(0, 6 * mm),
        Paragraph(f"<b>{plan.get('title', 'Day plan')}</b>",
                  ParagraphStyle("plan-title", parent=_BASE, fontSize=13,
                                 fontName="Helvetica-Bold")),
        Paragraph(f"{user_line} &nbsp;·&nbsp; generated {generated}", _SMALL),
        Spacer(0, 4 * mm),
        Paragraph("Daily targets — computed deterministically", _SMALL),
        Spacer(0, 1.5 * mm),
        _targets_table(targets),
        Spacer(0, 2 * mm),
    ]
    for meal in plan.get("meals", []):
        story.extend(_meal_block(meal))
    if plan.get("hydration"):
        story += [Paragraph("Hydration", _H_MEAL),
                  Paragraph(plan["hydration"], _BASE)]
    if plan.get("notes"):
        story += [Paragraph("Notes from the Meal Planner agent", _H_MEAL),
                  Paragraph(plan["notes"], _BASE)]
    story += [Spacer(0, 6 * mm),
              HRFlowable(width="100%", thickness=0.6, color=_LINE),
              Spacer(0, 2 * mm), Paragraph(_DISCLAIMER, _SMALL)]

    doc.build(story)
    return buffer.getvalue()
