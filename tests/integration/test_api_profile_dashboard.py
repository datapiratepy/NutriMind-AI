"""Integration tests: profile lifecycle, BMI, water, targets, dashboard."""


def test_profile_lifecycle(client, sample_profile):
    assert client.get("/api/profile").get_json()["profile"] is None

    created = client.put("/api/profile", json=sample_profile)
    assert created.status_code == 201
    assert created.get_json()["created"] is True

    fetched = client.get("/api/profile").get_json()["profile"]
    assert fetched["name"] == "Harsh" and fetched["allergies"] == ["peanuts"]

    # Creating the profile auto-records a BMI snapshot.
    history = client.get("/api/bmi/history").get_json()["records"]
    assert len(history) == 1 and history[0]["category"] == "normal"

    # Partial update: weight change appends another BMI record.
    updated = client.put("/api/profile?partial=1", json={"weight_kg": 80})
    assert updated.status_code == 200
    assert len(client.get("/api/bmi/history").get_json()["records"]) == 2


def test_profile_rejects_invalid_payload(client, sample_profile):
    response = client.put("/api/profile", json={**sample_profile, "age": 500})
    assert response.status_code == 400
    assert "age" in response.get_json()["error"]["message"]


def test_targets_require_profile(client, sample_profile):
    assert client.get("/api/targets").status_code == 400
    client.put("/api/profile", json=sample_profile)
    targets = client.get("/api/targets").get_json()["targets"]
    assert targets["calories"] > 1200 and targets["protein_g"] > 0


def test_bmi_endpoint(client):
    response = client.post("/api/bmi", json={"height_cm": 175, "weight_kg": 70})
    assert response.status_code == 201
    assessment = response.get_json()["assessment"]
    assert assessment["bmi"] == 22.9 and assessment["category"] == "normal"


def test_water_logging(client):
    assert client.get("/api/water/today").get_json()["water"]["glasses"] == 0
    client.post("/api/water", json={"glasses": 3})
    body = client.post("/api/water", json={}).get_json()   # default +1
    assert body["water"]["glasses"] == 4


def test_dashboard_summary_shape(client, sample_profile):
    client.put("/api/profile", json=sample_profile)
    client.post("/api/meals", json={"items": [{"name": "roti", "quantity": 2}]})
    client.post("/api/water", json={"glasses": 5})

    body = client.get("/api/dashboard/summary").get_json()
    assert body["profile_exists"] is True
    assert body["today"]["calories"] > 0
    assert body["today"]["water_glasses"] == 5
    assert len(body["week"]["calorie_series"]) == 7
    assert all("water_glasses" in day for day in body["week"]["calorie_series"])
    assert body["week"]["meals_today"] == 1
    ai = body["week"]["ai_activity"]
    assert set(ai) == {"responses", "grounded", "tokens_used", "agents"}
    score = body["health_score"]
    assert 0 <= score["total"] <= 100
    assert sum(c["max_points"] for c in score["components"]) == 100
