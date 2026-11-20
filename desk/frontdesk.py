import json
import logging
import threading
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from threading import Lock

from fastapi import HTTPException
from .llm import AIError

from .intents import WEEK, CustomerTurn, OwnerTurn, decide
from .jobs import deliver, report, run_due
from .store import CONFIG, TZ, database, event, message, now, put, rows, setting

logger = logging.getLogger(__name__)
_locks = defaultdict(Lock)
_registry = threading.Lock()


def conversation_lock(customer):
    with _registry:
        return _locks[customer]


def profile(db, customer):
    record = db.execute("SELECT * FROM customers WHERE id=?", (customer,)).fetchone()
    if not record:
        raise HTTPException(404, "Customer not found")
    data = dict(record)
    data["offers"] = json.loads(data["offers"])
    return data


def context(db, customer):
    days = setting(db, "open_days")
    opening, closing = CONFIG["opening_hour"], CONFIG["closing_hour"]
    result = {"business": CONFIG["name"], "services": CONFIG["services"], "facts": setting(db, "facts"),
              "hours": f"{opening:02}:00 to {closing:02}:00 on " + (", ".join(WEEK[d].title() for d in days) or "no days"),
              "now": now(db).isoformat(), "timezone": CONFIG["timezone"]}
    recent = rows(db, """SELECT role,body,at,kind FROM messages WHERE customer IS ?
                         ORDER BY id DESC LIMIT 30""", (customer,))
    result["history"] = list(reversed(recent))
    if customer is not None:
        result["customer"] = profile(db, customer)
        result["visits"] = rows(db, """SELECT v.*,t.name AS technician FROM visits v JOIN technicians t ON t.id=v.tech
                                      WHERE customer=? ORDER BY start""", (customer,))
        result["quotes"] = rows(db, "SELECT * FROM quotes WHERE customer=?", (customer,))
    return result


def technician(db, at, customer, start):
    local = datetime.fromisoformat(start).astimezone(TZ)
    duration = timedelta(minutes=CONFIG["visit_minutes"])
    opening = datetime.combine(local.date(), time(CONFIG["opening_hour"]), TZ)
    closing = datetime.combine(local.date(), time(), TZ) + timedelta(hours=CONFIG["closing_hour"])
    if (local <= at or local.weekday() not in setting(db, "open_days")
            or local < opening or local + duration > closing
            or (local - opening).total_seconds() % duration.total_seconds()):
        return None
    end = local + duration
    # Overlap, rather than equal-start checks, covers appointments whose duration
    # differs after a configuration change. BEGIN IMMEDIATE serializes booking writes.
    return db.execute("""SELECT id,name FROM technicians t WHERE NOT EXISTS (
        SELECT 1 FROM visits v WHERE (v.tech=t.id OR v.customer=?)
        AND julianday(v.start)<julianday(?)
        AND julianday(v.start, '+' || v.duration_minutes || ' minutes')>julianday(?))
        ORDER BY (SELECT count(*) FROM visits WHERE tech=t.id AND substr(start,1,10)=?),t.id LIMIT 1""",
        (customer, end.isoformat(), local.isoformat(), local.date().isoformat())).fetchone()


def openings(db, at, customer, day):
    offers = []
    if day and (day < at.date() or day > at.date() + timedelta(days=90)):
        return offers
    for offset in range(1 if day else 14):
        candidate = (day or at.date()) + timedelta(days=offset)
        slot = datetime.combine(candidate, time(CONFIG["opening_hour"]), TZ)
        closing = datetime.combine(candidate, time(), TZ) + timedelta(hours=CONFIG["closing_hour"])
        step = timedelta(minutes=CONFIG["visit_minutes"])
        while slot + step <= closing:
            if technician(db, at, customer, slot.isoformat()):
                offers.append(slot.isoformat())
                if len(offers) == 3:
                    return offers
            slot += step
    return offers


def when(start):
    return datetime.fromisoformat(start).strftime("%a %b %d at %I:%M %p")


def offer(db, customer, at, day):
    choices = openings(db, at, customer, day)
    db.execute("UPDATE customers SET offers=? WHERE id=?", (json.dumps(choices), customer))
    if not choices:
        return "There are no openings on that schedule. Would another day work?"
    person = profile(db, customer)
    access = f" Gate code {person['gate']} is on file." if person["gate"] else ""
    return f"For {person['address']}.{access} Which visit works for you?\n" + "\n".join(f"{i}. {when(s)}" for i,s in enumerate(choices,1))


def customer_action(db, customer, turn, at, source):
    if turn.issue.strip() and turn.action in ("offer", "quote") and not turn.day:
        db.execute("UPDATE customers SET requested_day='',offers='[]' WHERE id=?", (customer,))
    for key in ("name", "address", "gate", "issue"):
        value = getattr(turn, key).strip()
        if value:
            db.execute(f"UPDATE customers SET {key}=? WHERE id=?", (value, customer))
            event(db, at, f"Saved customer {key}", "Details supplied in conversation", source)
    if turn.day:
        db.execute("UPDATE customers SET requested_day=? WHERE id=?", (turn.day.isoformat(), customer))
    person = profile(db, customer)
    if turn.action == "reply":
        return turn.body or "Could you tell me a little more?"
    if turn.action == "handoff":
        reason = turn.body or "Owner review requested"
        db.execute("UPDATE customers SET handoff=?,offers='[]' WHERE id=?", (reason, customer))
        event(db, at, f"Handoff for {person['name']}", reason, source)
        return "I’ve passed your message to the owner. They’ll reply here."
    action = "quote" if person["pending"] == "quote" and turn.action == "offer" else turn.action
    db.execute("UPDATE customers SET pending=? WHERE id=?", (action, customer))
    if not person["issue"]:
        return "What problem should the technician look at?"
    if person["name"] == "New customer" or not person["name"]:
        return "What name should I put on the request?"
    if not person["address"]:
        return "What is the service address?"
    if action == "quote":
        if not db.execute("SELECT 1 FROM quotes WHERE customer=? AND job=? AND status='owed'", (customer,person["issue"])).fetchone():
            db.execute("INSERT INTO quotes(customer,job,status,updated) VALUES (?,?,'owed',?)", (customer,person["issue"],at.isoformat()))
            event(db, at, f"Quote requested for {person['name']}", person["issue"], source)
        db.execute("UPDATE customers SET pending='',issue='',offers='[]',requested_day='' WHERE id=?", (customer,))
        return "I’ve added your request to the owner’s quote list."
    day = date.fromisoformat(person["requested_day"]) if person["requested_day"] else None
    if action == "offer":
        return offer(db, customer, at, day)
    choices = person["offers"]
    if not turn.choice or turn.choice > len(choices):
        return offer(db, customer, at, day)
    start = choices[turn.choice - 1]
    tech = technician(db, at, customer, start)
    if not tech:
        db.execute("UPDATE customers SET requested_day='' WHERE id=?", (customer,))
        return "That opening is no longer available. " + offer(db, customer, at, None)
    visit = db.execute("INSERT INTO visits(customer,tech,start,address,gate,issue,duration_minutes) VALUES (?,?,?,?,?,?,?)",
                       (customer,tech["id"],start,person["address"],person["gate"],person["issue"],CONFIG["visit_minutes"])).lastrowid
    db.execute("UPDATE customers SET offers='[]',issue='',pending='',requested_day='' WHERE id=?", (customer,))
    event(db, at, f"Booked {tech['name']} for {person['name']}", f"Visit #{visit}: customer selected option {turn.choice}; availability rechecked", source)
    body = f"Confirmed for {when(start)} with {tech['name']} at {person['address']} for {person['issue']}."
    if person["gate"]:
        body += f" Gate code {person['gate']} is included for the technician."
    deliver(db, f"confirmation/{visit}", customer, body, at, f"Booking #{visit} confirmed", "confirmation")
    return None  # Confirmation was already delivered to the conversation.


def owner_action(db, turn, at, source):
    if turn.action == "hours" and turn.weekday in range(7) and turn.opened is not None:
        days = set(setting(db, "open_days"))
        (days.add if turn.opened else days.discard)(turn.weekday)
        put(db, "open_days", sorted(days))
        body = f"{WEEK[turn.weekday].title()} is now {'open' if turn.opened else 'closed'} for new bookings. Existing appointments stay scheduled."
    elif turn.action == "fact" and turn.key.strip() and turn.body.strip():
        facts = setting(db, "facts")
        facts[turn.key.strip().lower()] = turn.body.strip()
        put(db, "facts", facts)
        body = f"Saved {turn.key}: {turn.body}"
    elif turn.action == "schedule" and turn.report and turn.weekday is not None:
        found = db.execute("SELECT 1 FROM schedules WHERE report=? AND weekday=? AND hour=? AND minute=?",
                           (turn.report,turn.weekday,turn.hour,turn.minute)).fetchone()
        if found:
            return "That report is already scheduled."
        db.execute("INSERT INTO schedules(report,weekday,hour,minute,created_at) VALUES (?,?,?,?,?)",
                   (turn.report,turn.weekday,turn.hour,turn.minute,at.isoformat()))
        cadence = "day" if turn.weekday == -1 else WEEK[turn.weekday].title()
        body = f"Scheduled {turn.report.replace('_',' ')} every {cadence} at {turn.hour:02}:{turn.minute:02} ({CONFIG['timezone']})."
    elif turn.action == "report" and turn.report:
        event(db, at, "Ran owner report", turn.report, source)
        return report(db, turn.report, at.date())
    else:
        return (turn.body if turn.action == "reply" else "") or "Please clarify the rule or report you want."
    event(db, at, body, "Owner instruction", source)
    return body


def chat(customer, text):
    with conversation_lock(customer):
        with database(write=True) as db:
            run_due(db)
            ctx = context(db, customer)
        paused = customer is not None and bool(ctx["customer"]["handoff"])
        turn = None
        if not paused:
            try:
                turn = decide(text, ctx, owner=customer is None)
            except (AIError, ValueError, TypeError, KeyError) as exc:
                logger.warning("Assistant decision failed: %s", type(exc).__name__)
                if customer is None:
                    turn = OwnerTurn(action="reply", body="The AI service could not process this instruction. Please retry.")
                else:
                    turn = CustomerTurn(action="handoff", body="Assistant unavailable. Owner reply needed: " + text[:200])
        with database(write=True) as db:
            run_due(db)
            at = now(db)
            source = message(db, customer, "owner" if customer is None else "customer", text, at)
            if paused:
                event(db, at, "Message held for owner", "Conversation is paused", source)
                return {"reply": None, "status": "waiting_for_owner"}
            body = owner_action(db, turn, at, source) if customer is None else customer_action(db, customer, turn, at, source)
            if body is not None:
                message(db, customer, "assistant", body, at)
            else:
                body = db.execute("SELECT body FROM messages WHERE customer=? ORDER BY id DESC LIMIT 1", (customer,)).fetchone()[0]
            return {"reply": body, "status": "waiting_for_owner" if customer is not None and profile(db, customer)["handoff"] else "active"}
