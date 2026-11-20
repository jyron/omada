import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

load_dotenv()


class Business(BaseModel):
    name: str
    timezone: str
    opening_hour: int = Field(ge=0, le=23)
    closing_hour: int = Field(ge=1, le=24)
    visit_minutes: int = Field(ge=15, le=480)
    open_weekdays: list[int]
    services: list[str]
    facts: dict[str, str]
    technicians: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_hours(self):
        ZoneInfo(self.timezone)
        if self.opening_hour >= self.closing_hour:
            raise ValueError("Opening hour must be before closing hour")
        if any(day not in range(7) for day in self.open_weekdays):
            raise ValueError("Weekdays must be between 0 and 6")
        if self.visit_minutes > (self.closing_hour - self.opening_hour) * 60:
            raise ValueError("Visit must fit within opening hours")
        return self


CONFIG_PATH = Path(os.getenv("DESK_CONFIG", Path(__file__).with_name("business.json")))
CONFIG = Business.model_validate_json(CONFIG_PATH.read_text()).model_dump()
TZ = ZoneInfo(CONFIG["timezone"])
SEED_TIME = datetime(2026, 11, 19, 7, 45, tzinfo=TZ)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT DEFAULT '', gate TEXT DEFAULT '',
    issue TEXT DEFAULT '', offers TEXT DEFAULT '[]', handoff TEXT DEFAULT '',
    pending TEXT DEFAULT '', requested_day TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS technicians (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS visits (
    id INTEGER PRIMARY KEY, customer INTEGER REFERENCES customers, tech INTEGER REFERENCES technicians,
    start TEXT NOT NULL, address TEXT NOT NULL, gate TEXT NOT NULL, issue TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 60,
    UNIQUE(tech,start), UNIQUE(customer,start)
);
CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY, customer INTEGER REFERENCES customers, job TEXT, status TEXT,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, customer INTEGER REFERENCES customers, role TEXT, body TEXT, at TEXT,
    kind TEXT NOT NULL DEFAULT 'chat'
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, at TEXT, action TEXT, reason TEXT, source INTEGER REFERENCES messages
);
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY, report TEXT, weekday INTEGER, hour INTEGER,
    minute INTEGER NOT NULL DEFAULT 0, created_at TEXT,
    UNIQUE(report,weekday,hour,minute)
);
CREATE TABLE IF NOT EXISTS deliveries (key TEXT PRIMARY KEY);
CREATE INDEX IF NOT EXISTS messages_customer_time ON messages(customer,at);
CREATE INDEX IF NOT EXISTS visits_start ON visits(start);
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
    except BaseException:
        db.rollback()
        raise
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


def message(db, customer, role, body, at, kind="chat"):
    return db.execute("INSERT INTO messages(customer,role,body,at,kind) VALUES (?,?,?,?,?)",
                      (customer, role, body, at.isoformat(), kind)).lastrowid


def event(db, at, action, reason, source=None):
    db.execute("INSERT INTO events(at,action,reason,source) VALUES (?,?,?,?)",
               (at.isoformat(), action, reason, source))


def initialize():
    with database(write=True) as db:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        # Upgrade databases created by the initial project scaffold.
        for table, additions in {
            "customers": {"pending": "TEXT DEFAULT ''", "requested_day": "TEXT DEFAULT ''"},
            "visits": {"duration_minutes": "INTEGER NOT NULL DEFAULT 60"},
            "messages": {"kind": "TEXT NOT NULL DEFAULT 'chat'"},
            "schedules": {"minute": "INTEGER NOT NULL DEFAULT 0", "created_at": "TEXT"},
        }.items():
            columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
            for column, definition in additions.items():
                if column not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        legacy_schedule_key = any(
            index["unique"] and [c["name"] for c in db.execute(f"PRAGMA index_info('{index['name']}')")]
            == ["report", "weekday", "hour"]
            for index in db.execute("PRAGMA index_list(schedules)")
        )
        if legacy_schedule_key:
            db.execute("""CREATE TABLE schedules_updated (
                id INTEGER PRIMARY KEY, report TEXT, weekday INTEGER, hour INTEGER,
                minute INTEGER NOT NULL DEFAULT 0, created_at TEXT,
                UNIQUE(report,weekday,hour,minute))""")
            db.execute("INSERT INTO schedules_updated SELECT id,report,weekday,hour,minute,created_at FROM schedules")
            db.execute("DROP TABLE schedules")
            db.execute("ALTER TABLE schedules_updated RENAME TO schedules")
        if db.execute("SELECT 1 FROM settings LIMIT 1").fetchone():
            return
        seed(db)


def seed(db):
    put(db, "offset", SEED_TIME.timestamp() - datetime.now(timezone.utc).timestamp())
    put(db, "cursor", SEED_TIME.isoformat())
    put(db, "open_days", CONFIG["open_weekdays"])
    put(db, "facts", CONFIG["facts"])
    db.executemany("INSERT INTO technicians(name) VALUES (?)", [(n,) for n in CONFIG["technicians"]])
    db.executemany("INSERT INTO customers(name,address,gate) VALUES (?,?,?)", [
        ("Maya Reed", "18 Maple Lane", "1402"), ("Sam Park", "73 Oak Street", ""),
        ("Jo Ellis", "26 Cedar Court", ""), ("New customer", "", "")])
    for customer, tech, days, hour, address, gate, issue in [
        (1, 2, -12, 11, "18 Maple Lane", "1402", "Kitchen faucet repair"),
        (2, 1, 1, 10, "73 Oak Street", "", "Fixture repair"),
        (3, 3, 0, 13, "26 Cedar Court", "", "Drain inspection"),
        (1, 4, 3, 11, "18 Maple Lane", "1402", "Water heater inspection"),
    ]:
        start = (SEED_TIME + timedelta(days=days)).replace(hour=hour, minute=0)
        actual_tech = 1 + (tech - 1) % len(CONFIG["technicians"])
        db.execute("INSERT INTO visits(customer,tech,start,address,gate,issue,duration_minutes) VALUES (?,?,?,?,?,?,?)",
                   (customer, actual_tech, start.isoformat(), address, gate, issue, CONFIG["visit_minutes"]))
    old = (SEED_TIME - timedelta(days=4)).isoformat()
    db.executemany("INSERT INTO quotes(customer,job,status,updated) VALUES (?,?,?,?)",
                   [(2,"Kitchen fixture replacement","sent",old), (3,"Basement drain repair","owed",old)])
    db.execute("INSERT INTO schedules(report,weekday,hour,minute,created_at) VALUES ('rundown',-1,8,0,?)",
               (SEED_TIME.isoformat(),))
    prior = SEED_TIME - timedelta(days=12)
    message(db, 1, "customer", "Please keep 18 Maple Lane and gate code 1402 for my next visit.", prior)
    message(db, 1, "assistant", "I saved those access details for your next visit.", prior)
    message(db, 2, "owner", "Your kitchen fixture replacement quote is ready. Let me know if you'd like to go ahead.",
            SEED_TIME - timedelta(days=4), "quote")
    message(db, 3, "customer", "Could you send an estimate for the basement drain repair?", SEED_TIME - timedelta(days=4))


def reset():
    with database(write=True) as db:
        for table in ("events", "deliveries", "messages", "visits", "quotes", "schedules", "customers", "technicians", "settings"):
            db.execute(f"DELETE FROM {table}")
        seed(db)
