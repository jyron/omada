from datetime import datetime, timedelta

from apscheduler.triggers.cron import CronTrigger

from .store import TZ, event, message, now, put, rows, setting

REPORTS = ("rundown", "waiting_quotes", "handoffs")


def report(db, kind, day):
    if kind == "waiting_quotes":
        items = rows(db, """SELECT name,job FROM quotes JOIN customers ON customers.id=customer
                            WHERE status='owed' ORDER BY updated""")
        return "Waiting on a quote: " + ("; ".join(f"{r['name']} ({r['job']})" for r in items) or "nobody") + "."
    handoffs = rows(db, "SELECT name,handoff FROM customers WHERE handoff!='' ORDER BY name")
    if kind == "handoffs":
        return "Open handoffs: " + ("; ".join(f"{r['name']}: {r['handoff']}" for r in handoffs) or "none") + "."
    visits = rows(db, """SELECT start,c.name AS client,t.name AS tech FROM visits
                         JOIN customers c ON c.id=customer JOIN technicians t ON t.id=tech
                         WHERE substr(start,1,10)=? ORDER BY start""", (day.isoformat(),))
    schedule = "; ".join(f"{r['start'][11:16]} {r['client']} with {r['tech']}" for r in visits) or "no visits"
    owed = db.execute("SELECT count(*) FROM quotes WHERE status='owed'").fetchone()[0]
    return f"Morning rundown for {day:%a %b %d}: {schedule}. {owed} quotes owed. {len(handoffs)} open handoffs."


def deliver(db, key, recipient, body, at, reason, kind):
    if db.execute("INSERT OR IGNORE INTO deliveries VALUES (?)", (key,)).rowcount:
        source = message(db, recipient, "assistant", body, at, kind)
        event(db, at, "Delivered " + kind.replace("_", " "), reason, source)


def trigger(schedule):
    return CronTrigger(day_of_week="*" if schedule["weekday"] == -1 else schedule["weekday"],
                       hour=schedule["hour"], minute=schedule["minute"], timezone=TZ)


def next_run(schedule, at):
    return trigger(schedule).get_next_fire_time(None, at + timedelta(seconds=1))


def due_times(cron, after, through):
    due = cron.get_next_fire_time(None, after + timedelta(seconds=1))
    while due is not None and due <= through:
        yield due
        due = cron.get_next_fire_time(due, due + timedelta(seconds=1))


def send_reminders(db, at):
    next_day = (at.date() + timedelta(days=1)).isoformat()
    for visit in rows(db, """SELECT v.*,t.name FROM visits v JOIN technicians t ON t.id=tech
                             JOIN customers c ON c.id=v.customer
                             WHERE substr(start,1,10)=? AND c.handoff=''""", (next_day,)):
        body = f"Reminder: {visit['name']} is scheduled for tomorrow at {visit['start'][11:16]} at {visit['address']}."
        deliver(db, f"reminder/{visit['id']}", visit["customer"], body, at,
                f"Visit #{visit['id']} is tomorrow", "reminder")


def run_due(db):
    at = now(db)
    cursor = datetime.fromisoformat(setting(db, "cursor"))
    work = []
    for schedule in rows(db, "SELECT * FROM schedules ORDER BY id"):
        created = datetime.fromisoformat(schedule["created_at"]) if schedule["created_at"] else cursor
        for due in due_times(trigger(schedule), max(cursor, created), at):
            work.append((due, "report", schedule))
    for due in due_times(CronTrigger(hour=8, minute=0, timezone=TZ), cursor, at):
        work.append((due, "outreach", None))
    for due, kind, schedule in sorted(work, key=lambda item: item[0]):
        if kind == "report":
            deliver(db, f"report/{schedule['id']}/{due.isoformat()}", None,
                    report(db, schedule["report"], due.date()), due,
                    f"Schedule #{schedule['id']}: {schedule['report']}", "report")
            continue
        send_reminders(db, due)
        cutoff = (due - timedelta(days=3)).isoformat()
        for quote in rows(db, """SELECT q.* FROM quotes q JOIN customers c ON c.id=q.customer
                                 WHERE status='sent' AND c.handoff='' AND julianday(q.updated)<=julianday(?)
                                 AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.customer=q.customer
                                   AND m.role='customer' AND julianday(m.at)>julianday(?)
                                   AND julianday(m.at)<=julianday(?))""", (cutoff, cutoff, due.isoformat())):
            body = f"Checking in on your quote for {quote['job']}. Would you like the office to follow up?"
            deliver(db, f"followup/{quote['id']}/{quote['updated']}", quote["customer"], body, due,
                    f"Quote #{quote['id']} sent with three quiet days", "quote_followup")
    # Tomorrow bookings made after the morning sweep still receive a reminder.
    if at.hour >= 8:
        send_reminders(db, at)
    put(db, "cursor", at.isoformat())
