from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from desk.api import app, tick
from desk.frontdesk import chat, technician
from desk.llm import AIError
from desk.store import database, initialize, now, put, rows, setting


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DESK_DB", str(tmp_path / "desk.sqlite3"))
    monkeypatch.setenv("LLM_MODE", "demo")
    with TestClient(app) as client:
        yield client


def send(client, customer, text):
    url = "/api/owner/messages" if customer is None else f"/api/customers/{customer}/messages"
    result = client.post(url, json={"text": text})
    assert result.status_code == 200, result.text
    return result.json()


def test_returning_customer_and_new_customer_booking(client):
    assert "1402" in send(client, 1, "What address and gate code do you have for me?")["reply"]
    assert "Kitchen faucet repair" in send(client, 1, "What happened on my last visit?")["reply"]
    assert "18 Maple Lane" in send(client, 1, "My kitchen faucet is leaking.")["reply"]
    assert "Confirmed" in send(client, 1, "1")["reply"]
    assert "name" in send(client, 4, "My drain is clogged.")["reply"]
    assert "address" in send(client, 4, "Name: Alex Morgan")["reply"]
    assert "Which visit" in send(client, 4, "Address: 55 Birch Road")["reply"]
    assert "Confirmed" in send(client, 4, "1")["reply"]
    state = client.get("/api/state").json()
    visits = [v for v in state["visits"] if v["issue"] == "Kitchen faucet leak"]
    assert len(visits) == 1 and visits[0]["gate"] == "1402"
    messages = client.get("/api/customers/1/conversation").json()["messages"]
    assert len([m for m in messages if m["kind"] == "confirmation"]) == 1
    booking = next(e for e in state["events"] if e["action"].startswith("Booked"))
    assert booking["source_body"] == "1"
    initialize()
    assert client.get("/api/state").json()["visits"] == state["visits"]


def test_rule_change_invalidates_offers_but_keeps_existing_visits(client):
    send(client, 1, "My kitchen faucet is leaking.")
    send(client, 1, "Any openings on Sunday?")
    before = client.get("/api/state").json()["visits"]
    send(client, None, "We stopped doing Sunday calls")
    reply = send(client, 1, "1")["reply"]
    assert "no longer available" in reply
    assert client.get("/api/state").json()["visits"] == before
    choices = client.get("/api/customers/1/conversation").json()["customer"]["offers"]
    assert all("2026-11-22" not in c for c in choices)
    state = client.get("/api/state").json()
    assert 6 not in state["business"]["open_weekdays"]
    assert any(e["source_body"] == "We stopped doing Sunday calls" for e in state["events"])


def test_competing_bookings_and_overlapping_visits(client):
    with database(write=True) as db:
        ids = [db.execute("INSERT INTO customers(name,address) VALUES (?,?)", (f"Client {i}", f"{i} Birch Rd")).lastrowid for i in range(5)]
    for customer in ids:
        chat(customer, "My kitchen faucet is leaking.")
    with ThreadPoolExecutor(max_workers=5) as pool:
        replies = list(pool.map(lambda c: chat(c, "1")["reply"], ids))
    assert sum(r.startswith("Confirmed") for r in replies) == 4
    assert sum("no longer available" in r for r in replies) == 1
    with database() as db:
        visits = rows(db, "SELECT * FROM visits WHERE customer>=5")
        assert len(visits) == len({v["tech"] for v in visits}) == 4
        at = now(db)
        assert technician(db, at, 4, at.replace(hour=9,minute=0,second=0).isoformat()) is None
        assert technician(db, at, 4, at.replace(hour=9,minute=30,second=0).isoformat()) is None
    # A preexisting appointment that starts halfway through a grid slot blocks it.
    with database(write=True) as db:
        at = now(db)
        start = at.replace(hour=14,minute=30,second=0).isoformat()
        for i, customer in enumerate(ids[:4], 1):
            db.execute("INSERT INTO visits(customer,tech,start,address,gate,issue,duration_minutes) VALUES (?,?,?,'a','','b',90)", (customer, i, start))
        assert technician(db, at, 4, at.replace(hour=14,minute=0,second=0).isoformat()) is None
        assert technician(db, at, 4, at.replace(hour=15,minute=0,second=0).isoformat()) is None
        assert technician(db, at, 4, at.replace(hour=16,minute=0,second=0).isoformat()) is not None


def test_handoff_pauses_until_owner_resumes(client):
    send(client, 2, "I want to speak to the owner about a refund.")
    before = client.get("/api/customers/2/conversation").json()["messages"]
    assert send(client, 2, "My kitchen faucet is leaking.")["reply"] is None
    client.post("/api/clock/advance", json={"step":"15_minutes"})
    after = client.get("/api/customers/2/conversation").json()["messages"]
    assert len(after) == len(before) + 1
    result = client.post("/api/customers/2/reply", json={"text":"I can review that refund today.", "resume": True})
    assert result.status_code == 200
    assert client.get("/api/customers/2/conversation").json()["customer"]["handoff"] == ""
    assert "Which visit" in send(client, 2, "My kitchen faucet is leaking.")["reply"]


def test_reminders_quiet_quote_followups_reports_and_restart_deduplication(client):
    send(client, None, "Every Friday, tell me who is still waiting on a quote")
    send(client, None, "Every Friday, tell me who is still waiting on a quote")
    assert len(client.get("/api/state").json()["schedules"]) == 2
    client.post("/api/clock/advance", json={"step":"15_minutes"})
    sam = client.get("/api/customers/2/conversation").json()["messages"]
    assert sum(m["kind"] == "reminder" for m in sam) == 1
    assert sum(m["kind"] == "quote_followup" for m in sam) == 1
    client.post("/api/clock/advance", json={"step":"friday_morning"})
    state = client.get("/api/state").json()
    waiting = [m for m in state["owner_messages"] if m["kind"] == "report" and m["body"].startswith("Waiting")]
    assert len(waiting) == 1 and "Jo Ellis" in waiting[0]["body"] and "Sam Park" not in waiting[0]["body"]
    send(client, 1, "My kitchen faucet is leaking.")
    send(client, 1, "Tomorrow please")
    send(client, 1, "1")
    tick()
    maya = client.get("/api/customers/1/conversation").json()["messages"]
    assert sum(m["kind"] == "reminder" for m in maya) == 1
    before = len(client.get("/api/state").json()["events"])
    initialize()
    tick()
    tick()
    assert len(client.get("/api/state").json()["events"]) == before


def test_customer_activity_delays_followup_and_quote_status_stops_it(client):
    send(client, 2, "How much does a visit cost?")
    client.post("/api/clock/advance", json={"step":"15_minutes"})
    messages = client.get("/api/customers/2/conversation").json()["messages"]
    assert not any(m["kind"] == "quote_followup" for m in messages)
    client.patch("/api/quotes/1", json={"status":"accepted"})
    for _ in range(4):
        client.post("/api/clock/advance", json={"step":"1_day"})
    messages = client.get("/api/customers/2/conversation").json()["messages"]
    assert not any(m["kind"] == "quote_followup" for m in messages)
    send(client, 4, "I need a quote for a water heater replacement.")
    send(client, 4, "Name: Alex Morgan")
    send(client, 4, "Address: 55 Birch Road")
    quote = client.get("/api/state").json()["quotes"][-1]
    assert quote["customer"] == 4 and quote["status"] == "owed"
    assert "Alex Morgan" in send(client, None, "Who is still waiting on a quote?")["reply"]
    client.patch(f"/api/quotes/{quote['id']}", json={"status":"sent"})
    assert "Alex Morgan" not in send(client, None, "Who is still waiting on a quote?")["reply"]


def test_minute_schedule_and_removal(client):
    send(client, None, "Every day at 9:30 am, show open handoffs")
    for _ in range(7):
        client.post("/api/clock/advance", json={"step":"15_minutes"})
    state = client.get("/api/state").json()
    assert any(m["kind"] == "report" and m["body"] == "Open handoffs: none." for m in state["owner_messages"])
    assert client.delete("/api/schedules/2").status_code == 200
    client.post("/api/clock/advance", json={"step":"1_day"})
    messages = client.get("/api/state").json()["owner_messages"]
    assert sum(m["body"] == "Open handoffs: none." for m in messages) == 1


def test_provider_service_failure_hands_off_without_changing_rules(client, monkeypatch):
    def fail(*args, **kwargs):
        raise AIError("Provider unavailable")
    monkeypatch.setattr("desk.frontdesk.decide", fail)
    assert send(client, 1, "I need help")["status"] == "waiting_for_owner"
    assert "could not process" in send(client, None, "Close Sunday")["reply"]
    assert 6 in client.get("/api/state").json()["business"]["open_weekdays"]
