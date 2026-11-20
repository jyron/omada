import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG = json.loads(Path(__file__).with_name("business.json").read_text())
TZ = ZoneInfo(CONFIG["timezone"])
SEED_TIME = datetime(2026, 11, 19, 7, 45, tzinfo=TZ)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT DEFAULT '', gate TEXT DEFAULT '',
    issue TEXT DEFAULT '', offers TEXT DEFAULT '[]', handoff TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS technicians (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY, customer INTEGER REFERENCES customers, tech INTEGER REFERENCES technicians,
    start TEXT NOT NULL, address TEXT NOT NULL, gate TEXT NOT NULL, issue TEXT NOT NULL,
    UNIQUE(tech,start), UNIQUE(customer,start)
);
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY, customer INTEGER REFERENCES customers, job TEXT, status TEXT,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, customer INTEGER REFERENCES customers, role TEXT, body TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, at TEXT, action TEXT, reason TEXT, source INTEGER REFERENCES messages
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY, report TEXT, weekday INTEGER, hour INTEGER, UNIQUE(report,weekday,hour)
);
CREATE TABLE IF NOT EXISTS deliveries (key TEXT PRIMARY KEY);
"""


@contextmanager
def database(write=False):
    path = Path(os.getenv("DESK_DB", "data/desk.sqlite3"))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield db
        db.commit()
    finally:
        db.close()


def rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]


def setting(db, key):
    return json.loads(db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0])


def put(db, key, value):
    db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))


def now(db):
    utc = datetime.now(timezone.utc) + timedelta(seconds=setting(db, "offset"))
    return utc.astimezone(TZ).replace(microsecond=0)


def message(db, customer, role, body, at):
    return db.execute("INSERT INTO messages(customer,role,body,at) VALUES (?,?,?,?)",
                      (customer, role, body, at.isoformat())).lastrowid


def event(db, at, action, reason, source=None):
    db.execute("INSERT INTO events(at,action,reason,source) VALUES (?,?,?,?)",
               (at.isoformat(), action, reason, source))


def initialize():
    with database(write=True) as db:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        if db.execute("SELECT 1 FROM settings LIMIT 1").fetchone():
            return
        put(db, "offset", SEED_TIME.timestamp() - datetime.now(timezone.utc).timestamp())
        put(db, "cursor", SEED_TIME.isoformat())
        put(db, "open_days", CONFIG["open_weekdays"])
        put(db, "facts", CONFIG["facts"])
        db.executemany("INSERT INTO technicians(name) VALUES (?)", [(n,) for n in CONFIG["technicians"]])
        db.executemany("INSERT INTO customers(name,address,gate) VALUES (?,?,?)", [
            ("Maya Reed", "18 Maple Lane", "1402"), ("Sam Park", "73 Oak Street", ""),
            ("Jo Ellis", "26 Cedar Court", ""), ("New customer", "", "")])
        tomorrow = (SEED_TIME + timedelta(days=1)).replace(hour=10, minute=0)
        db.execute("INSERT INTO visits(customer,tech,start,address,gate,issue) VALUES (2,1,?,?,?,?)",
                   (tomorrow.isoformat(), "73 Oak Street", "", "fixture repair"))
        old = (SEED_TIME - timedelta(days=4)).isoformat()
        db.executemany("INSERT INTO quotes(customer,job,status,updated) VALUES (?,?,?,?)",
                       [(2,"Kitchen fixture","sent",old), (3,"Basement drain","owed",old)])
        db.execute("INSERT INTO schedules(report,weekday,hour) VALUES ('rundown',-1,8)")
        prior = SEED_TIME - timedelta(days=12)
        message(db, 1, "customer", "Please keep 18 Maple Lane and gate code 1402 for my next visit.", prior)
        message(db, 1, "assistant", "I saved those access details for your next visit.", prior)
