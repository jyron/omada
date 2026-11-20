import os
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Literal

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .frontdesk import chat, context, conversation_lock, profile
from .intents import REPORT_NAMES, WEEK, model_name
from .jobs import next_run, run_due
from .store import CONFIG, database, event, initialize, message, now, put, reset, rows, setting


class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Please enter a message")
        return value


class ReplyInput(TextInput):
    resume: bool = False


class AdvanceInput(BaseModel):
    step: Literal["15_minutes", "1_day", "friday_morning", "next_morning"]


class QuoteInput(BaseModel):
    status: Literal["owed", "sent", "accepted", "declined"]


def tick():
    with database(write=True) as db:
        run_due(db)


@asynccontextmanager
async def lifespan(app):
    initialize()
    scheduler = BackgroundScheduler(timezone=CONFIG["timezone"])
    scheduler.add_job(tick, "interval", seconds=5, id="desk-work", max_instances=1, coalesce=True)
    scheduler.start()
    tick()
    yield
    scheduler.shutdown(wait=True)


app = FastAPI(title="Northside Plumbing front desk", lifespan=lifespan)
STATIC = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    with database() as db:
        db.execute("SELECT 1 FROM settings LIMIT 1")
    return {"status": "ok"}


@app.get("/api/state")
def state():
    with database() as db:
        at = now(db)
        schedules = rows(db, "SELECT * FROM schedules ORDER BY id")
        for schedule in schedules:
            schedule["label"] = REPORT_NAMES[schedule["report"]]
            schedule["cadence"] = "Every day" if schedule["weekday"] == -1 else "Every " + WEEK[schedule["weekday"]].title()
            schedule["next_run"] = next_run(schedule, at).isoformat()
        return {
            "now": at.isoformat(), "timezone": CONFIG["timezone"],
            "mode": "OpenAI" if os.getenv("OPENAI_API_KEY") else "Guided demo",
            "model": model_name() if os.getenv("OPENAI_API_KEY") else None,
            "business": {**CONFIG, "open_weekdays": setting(db, "open_days"), "facts": setting(db, "facts")},
            "customers": rows(db, """SELECT c.*, (SELECT body FROM messages WHERE customer=c.id ORDER BY id DESC LIMIT 1) AS last_message,
                                      (SELECT at FROM messages WHERE customer=c.id ORDER BY id DESC LIMIT 1) AS last_at
                                      FROM customers c ORDER BY (handoff!='') DESC,c.id"""),
            "technicians": rows(db, "SELECT * FROM technicians ORDER BY id"),
            "visits": rows(db, """SELECT v.*,c.name AS customer_name,t.name AS technician FROM visits v
                                  JOIN customers c ON c.id=v.customer JOIN technicians t ON t.id=v.tech ORDER BY start"""),
            "quotes": rows(db, "SELECT q.*,c.name AS customer_name FROM quotes q JOIN customers c ON c.id=q.customer ORDER BY q.id"),
            "schedules": schedules,
            "owner_messages": rows(db, "SELECT * FROM (SELECT * FROM messages WHERE customer IS NULL ORDER BY id DESC LIMIT 100) ORDER BY id"),
            "events": rows(db, """SELECT e.*,m.body AS source_body,m.customer AS customer FROM events e
                                  LEFT JOIN messages m ON m.id=e.source ORDER BY e.id DESC LIMIT 100"""),
        }


@app.get("/api/customers/{customer}/conversation")
def conversation(customer: int):
    with database() as db:
        ctx = context(db, customer)
        ctx["messages"] = rows(db, "SELECT * FROM messages WHERE customer=? ORDER BY id", (customer,))
        return ctx


@app.post("/api/customers/{customer}/messages")
def customer_message(customer: int, body: TextInput):
    return chat(customer, body.text)


@app.post("/api/owner/messages")
def owner_message(body: TextInput):
    return chat(None, body.text)


@app.post("/api/customers/{customer}/reply")
def owner_reply(customer: int, body: ReplyInput):
    with conversation_lock(customer), database(write=True) as db:
        run_due(db)
        person = profile(db, customer)
        at = now(db)
        source = message(db, customer, "owner", body.text, at)
        if body.resume:
            db.execute("UPDATE customers SET handoff='',offers='[]' WHERE id=?", (customer,))
        elif not person["handoff"]:
            db.execute("UPDATE customers SET handoff='Owner is handling this conversation',offers='[]' WHERE id=?", (customer,))
        event(db, at, "Owner replied to " + person["name"], "Automatic replies resumed" if body.resume else "Automatic replies paused", source)
    return {"status": "sent"}


@app.post("/api/customers/{customer}/resume")
def resume(customer: int):
    with conversation_lock(customer), database(write=True) as db:
        person = profile(db, customer)
        at = now(db)
        db.execute("UPDATE customers SET handoff='',offers='[]' WHERE id=?", (customer,))
        event(db, at, "Resumed assistant for " + person["name"], "Owner clicked Resume assistant")
    return {"status": "active"}


@app.patch("/api/quotes/{quote_id}")
def update_quote(quote_id: int, body: QuoteInput):
    with database(write=True) as db:
        run_due(db)
        quote = db.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
        if not quote:
            raise HTTPException(404, "Quote not found")
        if quote["status"] == body.status:
            return {"status": body.status}
        at = now(db)
        db.execute("UPDATE quotes SET status=?,updated=? WHERE id=?", (body.status, at.isoformat(), quote_id))
        source = None
        if body.status == "sent":
            source = message(db, quote["customer"], "owner", f"Your quote for {quote['job']} has been sent. Let us know if you would like to go ahead.", at, "quote")
        event(db, at, f"Quote #{quote_id} marked {body.status}", "Owner updated the quote in the inbox", source)
    return {"status": body.status}


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    with database(write=True) as db:
        run_due(db)
        schedule = db.execute("SELECT * FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        if not schedule:
            raise HTTPException(404, "Schedule not found")
        db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
        event(db, now(db), "Removed report schedule", f"Owner removed {schedule['report']} schedule #{schedule_id}")
    return {"status": "removed"}


@app.post("/api/clock/advance")
def advance(body: AdvanceInput):
    with database(write=True) as db:
        at = now(db)
        if body.step == "15_minutes":
            target = at + timedelta(minutes=15)
        elif body.step == "1_day":
            target = at + timedelta(days=1)
        else:
            target = at.replace(hour=8, minute=0, second=0)
            if body.step == "friday_morning":
                target += timedelta(days=(4 - at.weekday()) % 7)
                if target <= at:
                    target += timedelta(days=7)
            elif target <= at:
                target += timedelta(days=1)
        put(db, "offset", setting(db, "offset") + target.timestamp() - at.timestamp())
        event(db, target, "Advanced demo clock", f"Owner advanced from {at.isoformat()} to {target.isoformat()}")
        run_due(db)
        return {"now": now(db).isoformat()}


@app.post("/api/demo/reset")
def reset_demo():
    reset()
    return {"status": "reset"}
