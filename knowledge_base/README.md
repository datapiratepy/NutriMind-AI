# Seed Knowledge Base

Place nutrition PDFs here; they are indexed automatically on first startup
(and by `python scripts/seed_knowledge_base.py`).

Suggested public documents:
- WHO healthy diet fact sheets / nutrition guidelines
- ICMR–NIN Dietary Guidelines for Indians
- USDA Dietary Guidelines and FoodData Central extracts
- Indian Food Composition Tables (IFCT)
- Condition-specific guides: diabetes, heart disease, hypertension nutrition

Notes:
- Text-based PDFs only (scanned/OCR PDFs are out of scope — see ARCHITECTURE.md §11).
- Max 15 MB per file (configurable via MAX_UPLOAD_MB).
- User uploads at runtime go to `instance/uploads/`, not this folder.
