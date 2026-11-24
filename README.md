# Business front desk

A local FastAPI + SQLite app with customer chat, an owner inbox, booking, simulated messages, and recurring reports. The Northside Plumbing demo seeds four technicians, returning customers, existing visits, and owed/sent quotes. Business name, services and hours are configurable.

## Start

With Docker running, from this folder:

```sh
docker compose up --build
```

Open [http://localhost:8000](http://localhost:8000). SQLite is stored in the `front-desk-data` Docker volume and survives container restarts. The app runs as one server process. No frontend build step or hosted service is required.

Without a key, **Guided demo** uses the exact sample messages shown in the interface. It also accepts `Name:`, `Address:`, `Gate:`, and `Remember key = value` inputs. This adapter is a small set of fixtures; the selected AI provider handles free-form language when configured.

## AI provider adapter

**Anthropic Claude is the default**, using `anthropic/claude-sonnet-5-5`. Copy `.env.example` to `.env`, set `ANTHROPIC_API_KEY`, and restart with `docker compose up --build`.

`desk/llm.py` exposes one structured-decision interface. Claude uses the [official Anthropic SDK](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), including its schema conversion and validation. Other providers use [LiteLLM](https://docs.litellm.ai/docs/providers). Provider protocols, authentication and request translation come from these libraries. Switch providers through configuration; booking and scheduling code stays the same.

| Setting | Purpose |
| --- | --- |
| `LLM_MODEL` | LiteLLM `provider/model` identifier; defaults to `anthropic/claude-sonnet-5-5` |
| `LLM_API_KEY` | Optional generic key for the selected provider; overrides its native key |
| `LLM_API_BASE` | Optional custom endpoint URL |
| `LLM_API_KEY_ENV` | Optional name of the environment variable holding a provider key; defaults to `<PROVIDER>_API_KEY` |
| `LLM_MODE` | `auto` enables AI with a key, endpoint or Ollama provider; `demo` forces sample conversations; `live` enables provider-native authentication such as cloud IAM |
| `LLM_OUTPUT_MODE` | `schema` requests structured output; `json` supplies the schema in the prompt for plain chat endpoints |

Examples for `.env`:

```dotenv
# Anthropic (default)
LLM_MODEL=anthropic/claude-sonnet-5-5
ANTHROPIC_API_KEY=your-key
```

```dotenv
# Another hosted provider, using the same adapter
LLM_MODEL=openai/gpt-4.1-mini
LLM_API_KEY=your-key
```

```dotenv
# Local Ollama from Docker Desktop
LLM_MODEL=ollama_chat/llama3.2
LLM_API_BASE=http://host.docker.internal:11434
LLM_OUTPUT_MODE=json
```

```dotenv
# A custom OpenAI-compatible server (the prefix selects its wire protocol)
LLM_MODEL=openai/your-model-name
LLM_API_BASE=http://host.docker.internal:1234/v1
LLM_API_KEY=local
LLM_OUTPUT_MODE=json
```

Other LiteLLM providers use their documented model prefix and credentials. Compose loads `.env` into the container, including provider-specific variables; the optional `env_file` requires Docker Compose 2.24 or newer. For provider-specific credentials set only in the host shell, use `LLM_API_KEY`, write them to `.env`, or explicitly forward them in Compose. Native Python execution reads `.env` too. For a local model outside Docker, use `localhost` instead of `host.docker.internal`.

Both output modes validate the response with Pydantic before performing an action. Incomplete or malformed output and provider failures follow the existing owner-handoff path. There is no automatic switch to another provider. Without credentials, the default remains Guided demo. The model proposes actions; Python checks and commits them.

## Interface and requirement locations

The app opens to **Requirements**, a linked index of every requested function. Navigation uses the same names as the workflows:

| Page | Functions |
| --- | --- |
| Customer chat & booking | Customer identity, saved address/gate code, visit history, business questions, missing details, offered visits and confirmation |
| Owner inbox | Handoff summary, paused replies, owner response and resume control |
| Appointments | Scheduled visits, technician assignments, access details and all four seeded technicians |
| Quotes | Owed/sent/accepted/declined records and status updates |
| Business rules | Current services, hours and facts; plain-language owner instructions |
| Automations & reports | Reminder and follow-up rules, report schedules, delivered reports and message delivery log |
| Activity log | Actions, reasons and triggering messages |
| Configuration | Active business/provider settings, startup and storage details, README and implementation note |

The demo clock controls are visible on every page. Each workflow heading identifies its corresponding requirement number. Business identity comes from configuration.

## Walkthrough

The demo starts on **Thursday, November 19, 2026, at 7:45 am America/Chicago**. It advances with elapsed time, and the clock buttons jump forward. **Reset demo** restores the seeded records and clock.

1. **Customer history and booking.** Open **Customer chat & booking** as Maya Reed. Ask for the saved address, gate code and previous visit. Describe a leaking kitchen faucet, then select an offered appointment. The confirmation identifies the technician and address. **Appointments** shows the visit, saved gate code and technician. **Activity log** shows what triggered the booking.
2. **Missing details.** Select **New customer** and describe a service request. Provide `Name: Alex Morgan` and `Address: 55 Birch Road` when asked. The desk gathers the details before offering visits. `Gate: 5522` saves an access code for subsequent visits. Expand **Example requests** to fill the message field with a sample, then click **Send message**.
3. **Business rules.** Open **Business rules**. Enter `We stopped doing Sunday calls` and click **Apply instruction**. The opening-day list changes; existing Sunday appointments remain. To verify a stale offer, request Sunday availability before closing Sunday, then select the old offer after the change. Python declines it and offers current availability.
4. **Owner handoff.** In customer chat, request the owner about a refund. **Owner inbox** shows the summary and paused status. Additional customer messages receive no automatic reply. Send an owner reply, optionally checking **Resume assistant after sending**, or use **Resume assistant** separately.
5. **Reminders and follow-up.** From the starting clock, click **+15 minutes**. In **Automations & reports → Message delivery log**, Sam receives a Friday visit reminder and a follow-up on his four-day-old sent quote. **Delivered reports** shows the morning rundown. These messages also appear in the relevant customer conversations.
6. **Report scheduling.** Under **Automations & reports**, enter `Every Friday, tell me who is still waiting on a quote` and click **Send instruction**. Inspect **Report schedules**, then click **Next Friday, 8 am**. **Delivered reports** lists Jo Ellis, whose estimate the business owes, and excludes Sam’s already-sent quote. A schedule can be removed with its **Remove** button.
7. **Quote status.** In **Quotes**, change Jo’s quote from **Owed** to **Sent**. A simulated message is recorded in the delivery log. Jo leaves the quotes-owed report and becomes eligible for a follow-up after three quiet days. **Accepted** and **Declined** stop follow-ups. Customer activity restarts the quiet period; handoffs pause customer automation.
8. **Saved business information.** In **Business rules**, enter `Remember parking = Use the side lot`. Ask about parking in customer chat. In Guided demo, send `parking` exactly.

## Business configuration

Edit `desk/business.json`, then restart the app. Docker mounts this file directly. The file controls the business name, timezone, services, opening/closing hours, standard visit length, seed opening days, seed facts, and technician names. Hours use a same-day window; `closing_hour: 24` is supported. All technicians cover the configured services with identical working hours.

Opening days and facts become persistent records on first startup, so owner changes survive restarts. To apply changed seed opening days, facts, or technicians, use **Reset demo**. Opening/closing hours, service list, timezone, and visit length load from the file on restart. Existing visits retain their recorded start times and durations. Use a fresh demo when changing timezone.

Bookings use a fixed visit grid, search the next 14 days by default, and support explicit dates up to 90 days out. Python rechecks opening rules, technician overlap, and customer overlap inside a SQLite write transaction. Four simultaneous bookings can use four technicians; a fifth cannot take an occupied opening.

Reports are deliberately limited to **morning rundown**, **quotes owed**, and **open handoffs**, scheduled daily or on one weekday at a local hour and minute. Recurrence and timezone calculations use [APScheduler](https://apscheduler.readthedocs.io/en/3.x/). A five-second background job processes due work even when no browser is open. Schedules, the clock offset, the processing cursor, and delivery keys are persistent. Missed runs catch up on startup; catch-up reports use the records available when processed.

Day-before reminders run at 8 am. Visits booked for tomorrow later in the day receive their reminder on the next background pass. Sent quotes receive one follow-up per sent version after three quiet days, at the next 8 am sweep. Re-sending requires moving the quote out of **Sent** and back to **Sent**. The demo records messages locally in conversation history; it does not contact customers or external calendars.

## Cloudflare demo

Live demo: [omada.jyron-dev.workers.dev](https://omada.jyron-dev.workers.dev).

The Cloudflare project is named `omada`. `cloudflare/worker.ts` routes requests to one shared Cloudflare Container running the same Python app and fictional business records. The Anthropic key is a Worker secret passed to the container at startup. It is not included in the image or browser assets.

The deployment uses Cloudflare's native container snapshots after successful changes and every five minutes. A scheduled Worker keeps background processing active without an open browser. On a restart, the last snapshot for the current image is restored. All visitors share this demo, including bookings and the Reset demo control.

Cloudflare snapshots are tied to an image version and have a 30-day retention window refreshed by restoration. Deploying a changed container image starts fresh fictional records; snapshots are for continuity of the hosted demonstration, while the local Docker volume remains the persistent local installation. See [Cloudflare's snapshot documentation](https://developers.cloudflare.com/containers/guides/snapshots/).

Deployment requires Docker, Node.js, Cloudflare authentication, and Containers enabled on the account. From the repository, run `npm ci`, `npx wrangler types`, and `npm run deploy`. For a new deployment, set `ANTHROPIC_API_KEY` with `npx wrangler secret put ANTHROPIC_API_KEY`. `npm run deploy:check` validates types and builds without publishing. Wrangler prints the public `omada` URL after deployment.

The initial hosted verification passed TypeScript checks and a container build, then verified the public health endpoint, interface assets, documentation, live Claude appointment offers, booking confirmation, and snapshot creation. The resulting appointment was also checked in the internal browser.

## Local Python development

Python 3.11 or newer:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn desk.api:app --host 127.0.0.1 --port 8000
```

`DESK_DB` changes the SQLite path, which defaults to `data/desk.sqlite3`. `DESK_CONFIG` selects a different business configuration file. Environment variables override `.env`. [API documentation](http://localhost:8000/docs) is available while running.

Run the focused workflow checks with `.venv/bin/python -m pytest -q`. They cover booking races, overlapping visits, changed rules, missing details, handoff/resume, late reminders, quote status and silence, report recurrence, persistence, delivery deduplication, and the provider-adapter validation/error boundary. They use temporary databases and no live AI calls.

See [DESIGN.md](DESIGN.md) for the one-page implementation note and verification record.

## Interface dependencies

Bootstrap 5.3.9 provides forms, buttons, tables, mobile navigation, reset confirmation and notifications. Bootstrap Icons supplies navigation icons. IBM Plex Sans 5.3.0 is bundled through Fontsource in 400/500/600 weights. All assets and their licenses are served locally from `desk/static/vendor`; the running app needs no CDN or frontend build step.

To update bundled assets after changing dependencies, run `npm ci && npm run vendor`. `npm run format` formats the three frontend source files. `package-lock.json` records the dependency versions.

## Design references

The redesign removes oversized promotional headings, repeated decorative cards, invented feature names, emoji decoration and marketing copy. It uses a persistent workflow menu, literal labels, standard controls, record tables and a requirements index. These changes address recurring patterns described in [925Studios’ design analysis](https://www.925studios.co/blog/ai-slop-design-tells) and [the OIM3690 design guide](https://oim3690.github.io/guides/ai-slop/). Navigation follows [Nielsen Norman Group’s menu guidance](https://www.nngroup.com/articles/menu-design/): visible desktop navigation, familiar placement and clear location labels.
