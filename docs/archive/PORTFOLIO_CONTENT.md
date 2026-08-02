# Portfolio & Social Content

## GitHub repository description (short)

Multi-agent AI nutrition assistant · IBM watsonx.ai Granite · RAG with
citations · deterministic nutrition math · Flask + ChromaDB · 156 tests

**Topics:** `ibm-watsonx` `granite` `rag` `agentic-ai` `flask` `chromadb`
`nutrition` `python` `ibm-skillsbuild`

## LinkedIn project description

NutriMind AI — AI Nutrition Assistant (IBM SkillsBuild internship)

Built a multi-agent nutrition assistant on IBM watsonx.ai: a coordinator
routes each request to specialized agents (knowledge Q&A, meal planning,
meal analysis, health guidance), factual answers are grounded in nutrition
documents with page-level citations, and every number — calories, BMI,
macro targets — is computed deterministically in Python rather than by the
LLM. Includes streaming chat with explainable routing metadata, drag-and-
drop knowledge base management, branded PDF export, dark/light UI, a
zero-credential demo mode, 156 automated tests (~88% coverage) and CI.
Tech: Python, Flask, IBM Granite, ChromaDB, SQLAlchemy, Bootstrap 5.

## Portfolio website blurb

An AI application that treats trust as a feature: NutriMind shows *which*
agent answered, *why* it was chosen, *what* evidence it retrieved (with
page-level citations), and computes every nutrition number outside the LLM.
Built end-to-end in ten engineering phases — architecture docs to CI — for
the IBM SkillsBuild internship.

## 100-word summary

NutriMind AI is a multi-agent nutrition assistant built with IBM watsonx.ai
and Granite. A coordinator agent classifies every request — deterministic
rules first, LLM classification only when needed — and routes it to one of
four specialist agents. Factual answers use retrieval-augmented generation
over user-uploaded nutrition PDFs, with page-level citations and an explicit
"general knowledge" label when retrieval confidence is low. All nutrition
math (Mifflin-St Jeor targets, food-table macros, BMI, health score) is
deterministic Python. The app features streaming chat with explainable AI
metadata, PDF meal-plan export, a zero-credential demo mode, 156 automated
tests, and CI.

## 250-word summary

NutriMind AI is a full-stack AI nutrition assistant developed for the IBM
SkillsBuild + Edunet Foundation internship, designed to demonstrate that
LLM applications can be transparent and trustworthy rather than black
boxes.

Instead of a single-prompt chatbot, the system runs a coordinator agent
that classifies every message using ordered deterministic rules (zero token
cost for common intents — including BMI checks it answers itself) and IBM
Granite JSON classification only for ambiguous requests. Four specialist
agents handle the work: a Knowledge Agent performing retrieval-augmented
generation over user-uploaded nutrition PDFs stored in ChromaDB, a Meal
Planner that composes plans around deterministically computed calorie and
macro targets, a Meal Analyzer that extracts foods from natural language
and prices them against a curated 85-food composition table, and a Health
Advisor for condition-aware guidance. Every response carries structured
metadata — the agent used, the routing reason, whether the answer was
grounded, the citations (filename and page), the tools invoked, and token
usage — rendered in the UI as badges and an AI-workflow panel.

The engineering emphasizes honesty and reproducibility: retrieval below a
similarity threshold produces an explicit "general knowledge" label; all
arithmetic is pure, unit-tested Python; and a deterministic demo mode runs
the entire application without IBM credentials. The project ships with 156
automated tests (~88% coverage), GitHub Actions CI, streaming SSE chat,
branded PDF export via ReportLab, and fifteen documentation files covering
architecture, setup, testing, demo scripts and interview preparation.

Stack: Python, Flask, IBM watsonx.ai (Granite), ChromaDB, SQLAlchemy,
Bootstrap 5.
