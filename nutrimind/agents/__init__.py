"""Agent registry and coordinator factory.

Adding an agent = one module + one registry entry + one prompt file
(FOLDER_STRUCTURE.md §4).
"""

from __future__ import annotations

from nutrimind.agents.coordinator import Coordinator, RoutingDecision, apply_rules
from nutrimind.agents.health_advisor_agent import HealthAdvisorAgent
from nutrimind.agents.knowledge_agent import KnowledgeAgent
from nutrimind.agents.meal_analyzer_agent import MealAnalyzerAgent
from nutrimind.agents.meal_planner_agent import MealPlannerAgent

#: registry id -> class; ids must match VALID_AGENTS in coordinator.py.
AGENT_REGISTRY: dict[str, type] = {
    KnowledgeAgent.name: KnowledgeAgent,
    MealPlannerAgent.name: MealPlannerAgent,
    MealAnalyzerAgent.name: MealAnalyzerAgent,
    HealthAdvisorAgent.name: HealthAdvisorAgent,
}

#: Human-readable labels for UI badges.
AGENT_LABELS: dict[str, str] = {
    "coordinator": "Coordinator",
    "knowledge_agent": "Knowledge Agent",
    "meal_planner": "Meal Planner",
    "meal_analyzer": "Meal Analyzer",
    "health_advisor": "Health Advisor",
}


def get_coordinator() -> Coordinator:
    return Coordinator(AGENT_REGISTRY)


__all__ = ["AGENT_REGISTRY", "AGENT_LABELS", "Coordinator", "RoutingDecision",
           "apply_rules", "get_coordinator"]
