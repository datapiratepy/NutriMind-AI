"""Integration tests: meal estimation, logging, food search."""


def test_estimate_meal_deterministic(client):
    payload = {"items": [{"name": "roti", "quantity": 2}, {"name": "dal"}]}
    first = client.post("/api/meals/estimate", json=payload).get_json()
    second = client.post("/api/meals/estimate", json=payload).get_json()
    assert first == second
    assert first["estimate"]["totals"]["calories"] > 0
    assert first["estimate"]["unmatched"] == []


def test_estimate_rejects_bad_payload(client):
    response = client.post("/api/meals/estimate", json={"items": []})
    assert response.status_code == 400
    body = response.get_json()["error"]
    assert body["code"] == "validation_error"
    assert "request_id" in body


def test_log_then_list_then_delete_meal(client):
    created = client.post("/api/meals", json={
        "items": [{"name": "poha"}], "meal_type": "breakfast",
        "raw_text": "poha for breakfast",
    })
    assert created.status_code == 201
    meal = created.get_json()["meal"]
    assert meal["meal_type"] == "breakfast" and meal["calories"] > 0

    listed = client.get("/api/meals?days=7").get_json()["meals"]
    assert len(listed) == 1

    deleted = client.delete(f"/api/meals/{meal['id']}")
    assert deleted.status_code == 200
    assert client.get("/api/meals").get_json()["meals"] == []


def test_delete_missing_meal_400(client):
    response = client.delete("/api/meals/999")
    assert response.status_code == 400


def test_food_search_endpoint(client):
    results = client.get("/api/foods/search?q=chapati").get_json()["results"]
    assert results and results[0]["name"] == "roti"


def test_meal_plans_list_empty(client):
    """The list is now paged, so the envelope carries the page metadata too."""
    body = client.get("/api/meal-plans").get_json()
    assert body["plans"] == []
    assert body["page"] == {"total": 0, "limit": 50, "offset": 0, "has_more": False}


def test_meal_plan_detail_and_export_stub(client, sample_profile):
    client.put("/api/profile", json=sample_profile)
    generated = client.post("/api/chat", json={
        "message": "create a meal plan for me", "stream": False}).get_json()
    plan_id = generated["meta"]["plan_id"]

    detail = client.get(f"/api/meal-plans/{plan_id}")
    assert detail.status_code == 200
    plan = detail.get_json()["plan"]
    assert [m["name"] for m in plan["plan"]["meals"]] == \
        ["Breakfast", "Lunch", "Snack", "Dinner"]

    export = client.get(f"/api/export/meal-plan/{plan_id}.pdf")
    assert export.status_code == 200
    assert export.mimetype == "application/pdf"
    assert export.data.startswith(b"%PDF-")
    assert "attachment" in export.headers.get("Content-Disposition", "")

    assert client.get("/api/meal-plans/9999").status_code == 400
    assert client.get("/api/export/meal-plan/9999.pdf").status_code == 400
