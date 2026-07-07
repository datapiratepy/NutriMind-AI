"""Unit tests for the deterministic routing stage (pure function, 0 tokens)."""

import pytest

from nutrimind.agents.coordinator import RoutingDecision, apply_rules


@pytest.mark.parametrize("message,agent,intent", [
    ("hello", "coordinator", "Small Talk"),
    ("Namaste!", "coordinator", "Small Talk"),
    ("what is my BMI?", "coordinator", "BMI Check"),
    ("what's my ideal weight", "coordinator", "BMI Check"),
    ("Today I ate 2 rotis, dal and paneer", "meal_analyzer", "Meal Analysis"),
    ("analyze my breakfast please", "meal_analyzer", "Meal Analysis"),
    ("for lunch I had biryani", "meal_analyzer", "Meal Analysis"),
    ("create a meal plan for me", "meal_planner", "Meal Planning"),
    ("suggest a 2500 calorie plan", "meal_planner", "Meal Planning"),
    ("I need a diet chart", "meal_planner", "Meal Planning"),
    ("Can diabetics eat bananas?", "health_advisor", "Health Advice"),
    ("foods for high blood pressure", "health_advisor", "Health Advice"),
    ("I want to build muscle", "health_advisor", "Health Advice"),
    ("nutrition during pregnancy", "health_advisor", "Health Advice"),
    ("how much protein is in paneer", "knowledge_agent", "Nutrition Question"),
    ("what foods are rich in iron", "knowledge_agent", "Nutrition Question"),
    ("is coconut oil healthy", "knowledge_agent", "Nutrition Question"),
    ("explain vitamin B12", "knowledge_agent", "Nutrition Question"),
])
def test_rule_stage_classification(message, agent, intent):
    decision = apply_rules(message)
    assert decision is not None, f"no rule matched {message!r}"
    assert decision.agent == agent
    assert decision.intent == intent
    assert decision.method == "rules"
    assert decision.reason  # explanation always present


@pytest.mark.parametrize("message", [
    "tell me something interesting",
    "what do you think about mondays",
])
def test_ambiguous_messages_fall_through(message):
    assert apply_rules(message) is None


def test_greeting_inside_longer_question_not_smalltalk():
    decision = apply_rules("hello, how much protein is in paneer?")
    assert decision is not None and decision.agent == "knowledge_agent"


def test_decision_is_serializable():
    decision = apply_rules("what is my bmi")
    assert isinstance(decision, RoutingDecision)
    data = decision.to_dict()
    assert set(data) == {"intent", "agent", "reason", "method"}
