# Northside Plumbing front desk

A local FastAPI + SQLite app with customer chat, an owner inbox, booking, simulated messages, and recurring reports. Four technicians, returning customers, existing visits, and two different quote states are ready to explore.

## Start

With Docker running, from this folder:

```sh
docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000). SQLite is stored in the `front-desk-data` Docker volume and survives container restarts. The app runs as one server process. No frontend build step or hosted service is required.

Without a key, **Guided demo** uses the exact sample messages shown in the interface. It also accepts `Name:`, `Address:`, `Gate:`, and `Remember key = value` inputs. This adapter is a small set of fixtures; OpenAI handles free-form language when configured.

To enable OpenAI, copy `.env.example` to `.env`, set `OPENAI_API_KEY`, and restart with `docker compose up --build`. `OPENAI_MODEL` defaults to `gpt-4.1-mini`. The [official OpenAI Python SDK](https://github.com/openai/openai-python) parses structured decisions into Pydantic models. The model proposes actions; Python checks and commits them. An unavailable AI service sends customer requests to the owner and leaves owner instructions unapplied.

## Five-minute walkthrough

The demo starts on **Thursday, November 19, 2026, at 7:45 am America/Chicago**. It advances with elapsed time, and the clock buttons jump forward. **Reset demo** restores the seeded records and clock.

1. **Returning customer to confirmed visit.** Open **Customer chat** as Maya Reed. Click **Saved details** and **Last visit** to see her address, gate code `1402`, and prior faucet repair. Click **Leaking faucet**, then one of the offered visits. The confirmation names the technician and address. **Shop activity** shows the visit and its triggering message in **The paper trail**. The appointment includes the saved gate code.
2. **Gather missing details.** Choose **New customer**, click **Leaking faucet**, then send `Name: Alex Morgan` and `Address: 55 Birch Road`. The desk asks for each missing detail before offering visits. Select a visit to book it. `Gate: 5522` saves an access code for subsequent visits.
3. **Change a business rule.** In the owner assistant, click **We stopped doing Sunday calls**. New Sunday openings disappear. Existing Sunday appointments remain visible. To see the stale-offer check, start another **Leaking faucet** request and request Sunday availability before closing Sunday, then select that old offer after the rule changes. Python declines it and offers current availability. Send `We are open on Sundays again` to restore Sunday service.
4. **Hand off and take control.** In customer chat, click **Talk to the owner**. The owner inbox displays the summary and a waiting status. Send another customer message; it is recorded without an automatic reply. Reply from the owner inbox. Check **Resume assistant after sending**, or click **Resume assistant** separately, to restore automation.
5. **Watch the morning work.** Click **+15 min** from the starting clock. Sam receives a reminder for Friday’s appointment and a follow-up on his sent quote, which has been quiet for four days. The owner assistant receives the morning rundown. Shop activity records each delivery. Repeated refreshes and restarts do not send duplicates.
6. **Schedule a report in plain language.** Click **Every Friday, tell me who is still waiting on a quote**, then **Friday, 8 am**. The delivered report lists Jo Ellis, whose estimate the shop owes. Sam’s already-sent quote is excluded. The schedule and its next run are visible under **On autopilot**. A second example schedules daily handoff reports at 9:30 am. Remove schedules using their **Remove** buttons.
7. **Close the loop on quotes.** Under **Shop activity → Quotes**, change Jo’s quote from **Owed** to **Sent**. This adds a simulated quote message. Jo leaves the quotes-owed report and becomes eligible for a follow-up after three quiet days. **Accepted** and **Declined** stop follow-ups. Customer activity restarts the quiet period; open handoffs pause customer automation.
8. **Save a fact.** Tell the owner assistant `Remember parking = Use the side lot`. In customer chat, send `parking` in Guided demo, or ask a natural-language parking question with OpenAI enabled.

## Business configuration

Edit `desk/business.json`, then restart the app. Docker mounts this file directly. The file controls the business name, timezone, services, opening/closing hours, standard visit length, seed opening days, seed facts, and technician names. Hours use a same-day window; `closing_hour: 24` is supported. All technicians cover the configured services with identical working hours.

Opening days and facts become persistent records on first startup, so owner changes survive restarts. To apply changed seed opening days, facts, or technicians, use **Reset demo**. Opening/closing hours, service list, timezone, and visit length load from the file on restart. Existing visits retain their recorded start times and durations. Use a fresh demo when changing timezone.

Bookings use a fixed visit grid, search the next 14 days by default, and support explicit dates up to 90 days out. Python rechecks opening rules, technician overlap, and customer overlap inside a SQLite write transaction. Four simultaneous bookings can use four technicians; a fifth cannot take an occupied opening.

Reports are deliberately limited to **morning rundown**, **quotes owed**, and **open handoffs**, scheduled daily or on one weekday at a local hour and minute. Recurrence and timezone calculations use [APScheduler](https://apscheduler.readthedocs.io/en/3.x/). A five-second background job processes due work even when no browser is open. Schedules, the clock offset, the processing cursor, and delivery keys are persistent. Missed runs catch up on startup; catch-up reports use the records available when processed.

Day-before reminders run at 8 am. Visits booked for tomorrow later in the day receive their reminder on the next background pass. Sent quotes receive one follow-up per sent version after three quiet days, at the next 8 am sweep. Re-sending requires moving the quote out of **Sent** and back to **Sent**. The demo records messages locally in conversation history; it does not contact customers or external calendars.

## Local development

Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn desk.api:app --host 127.0.0.1 --port 8000
```

`DESK_DB` changes the SQLite path, which defaults to `data/desk.sqlite3`. `DESK_CONFIG` selects a different business configuration file. Environment variables override `.env`. [API documentation](http://localhost:8000/docs) is available while running.

Run the focused workflow checks with `.venv/bin/python -m pytest -q`. They cover booking races, overlapping visits, changed rules, missing details, handoff/resume, late reminders, quote status and silence, report recurrence, persistence, delivery deduplication, and the SDK parsing/error boundary. They use temporary databases and no live AI calls.

See [DESIGN.md](DESIGN.md) for the one-page implementation note and verification record.
