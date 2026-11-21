const $ = (selector) => document.querySelector(selector);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const pages = {
  requirements: [
    "Requirements",
    "Where to find and verify each requirement.",
    "Requirements 1–5",
  ],
  customer: [
    "Customer chat & booking",
    "Customer conversations, saved details, available visits and confirmations.",
    "Requirements 1 & 2",
  ],
  inbox: [
    "Owner inbox",
    "Review handoffs, reply to customers and resume automatic replies.",
    "Requirement 3",
  ],
  appointments: [
    "Appointments",
    "Scheduled visits, assigned technicians and service details.",
    "Requirements 1 & 2",
  ],
  quotes: [
    "Quotes",
    "Track estimates owed, quotes sent and customer decisions.",
    "Requirements 1 & 4",
  ],
  rules: [
    "Business rules",
    "View business information and change rules through owner instructions.",
    "Requirements 2 & 3",
  ],
  automation: [
    "Automations & reports",
    "Reminders, quote follow-ups, report schedules and delivery history.",
    "Requirement 4",
  ],
  activity: [
    "Activity log",
    "Recorded actions with the reason and message that triggered them.",
    "Requirement 3",
  ],
  configuration: [
    "Configuration",
    "Business settings, AI provider and local delivery documentation.",
    "Requirements 1 & 5",
  ],
};
const kinds = {
  chat: "Message",
  confirmation: "Booking confirmation",
  reminder: "Visit reminder",
  quote_followup: "Quote follow-up",
  report: "Report",
  quote: "Sent quote",
};
const customerExamples = [
  ["Service request", "My kitchen faucet is leaking."],
  [
    "Saved address and gate code",
    "What address and gate code do you have for me?",
  ],
  ["Previous visit", "What happened on my last visit?"],
  ["Business hours", "What are your hours?"],
  ["Visit pricing", "How much does a visit cost?"],
  ["Tomorrow’s availability", "Tomorrow please"],
  ["Sunday availability", "Any openings on Sunday?"],
  ["Request an estimate", "I need a quote for a water heater replacement."],
  ["Request owner review", "I want to speak to the owner about a refund."],
];
let state,
  conversation,
  selected = 1,
  view = "requirements",
  busy = false,
  errorSource = null,
  refreshId = 0,
  renderedCustomer = null;
const notice = bootstrap.Toast.getOrCreateInstance($("#notice"), {
  delay: 4000,
});
const resetModal = bootstrap.Modal.getOrCreateInstance($("#reset-modal"));
function html(selector, content) {
  const el = $(selector);
  if (el.innerHTML !== content) el.innerHTML = content;
}
function date(value, options = {}) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: state?.timezone || "America/Chicago",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    ...options,
  }).format(new Date(value));
}
function empty(text) {
  return `<div class="empty-state">${esc(text)}</div>`;
}
function notify(text) {
  $("#notice-text").textContent = text;
  notice.show();
}
function showError(text, source = "request") {
  errorSource = source;
  $("#request-error").textContent = text;
  $("#request-error").hidden = false;
}
async function api(path, body, method) {
  const response = await fetch(path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : Array.isArray(error.detail)
          ? error.detail.map((v) => v.msg).join(", ")
          : `Request failed (${response.status}).`,
    );
  }
  return response.json();
}
async function refresh() {
  const request = ++refreshId,
    customer = selected;
  const result = await Promise.all([
    api("/api/state"),
    api(`/api/customers/${customer}/conversation`),
  ]);
  if (request !== refreshId) return;
  [state, conversation] = result;
  if (errorSource === "refresh") {
    $("#request-error").hidden = true;
    errorSource = null;
  }
  renderState();
  renderConversation();
}
async function mutate(path, body, method) {
  if (busy) return null;
  busy = true;
  document.body.setAttribute("aria-busy", "true");
  $("#request-error").hidden = true;
  document
    .querySelectorAll(
      'button[type="submit"], [data-step], [data-choice], [data-resume], [data-remove-schedule], #confirm-reset',
    )
    .forEach((button) => {
      button.disabled = true;
    });
  try {
    const result = await api(path, body, method);
    await refresh();
    return result;
  } catch (error) {
    showError(error.message);
    return null;
  } finally {
    busy = false;
    document.body.removeAttribute("aria-busy");
    document
      .querySelectorAll(
        'button[type="submit"], [data-step], [data-choice], [data-resume], [data-remove-schedule], #confirm-reset',
      )
      .forEach((button) => {
        button.disabled = false;
      });
  }
}
function route() {
  const requested = location.hash.slice(1);
  view = Object.hasOwn(pages, requested) ? requested : "requirements";
  for (const el of document.querySelectorAll(".view"))
    el.hidden = el.id !== `view-${view}`;
  for (const link of document.querySelectorAll("#main-nav .nav-link")) {
    const active = link.hash === `#${view}`;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  const [title, description, reference] = pages[view];
  $("#page-title").textContent = title;
  $("#page-description").textContent = description;
  $("#page-reference").textContent = reference;
  if (state) document.title = `${title} | ${state.business.name}`;
  bootstrap.Offcanvas.getInstance($("#navigation"))?.hide();
  if (conversation) renderConversation();
}
function renderMessages(selector, messages, perspective, force = false) {
  const el = $(selector),
    bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 70;
  const customerName = conversation?.customer.name || "Customer";
  const content = messages.length
    ? messages
        .map((m) => {
          const label =
            m.role === "assistant"
              ? "Assistant"
              : m.role === "owner"
                ? "Owner"
                : customerName;
          const outgoing =
            perspective === "customer"
              ? m.role === "customer"
              : m.role === "owner";
          return `<article class="message ${outgoing ? "outgoing" : ""}"><div class="message-meta"><span class="message-author">${esc(label)}</span><time datetime="${esc(m.at)}">${esc(date(m.at))}</time></div><div class="message-body">${esc(m.body)}</div>${m.kind !== "chat" ? `<div class="message-kind">${esc(kinds[m.kind] || m.kind)}</div>` : ""}</article>`;
        })
        .join("")
    : empty("No messages. Send a message to start this conversation.");
  if (el.innerHTML !== content) {
    el.innerHTML = content;
    if (bottom || force) el.scrollTop = el.scrollHeight;
  } else if (force) el.scrollTop = el.scrollHeight;
}
function renderState() {
  $("#brand-name").textContent = state.business.name;
  $("#mode").textContent = state.model
    ? `${state.mode} configured`
    : state.mode;
  $("#mode").classList.toggle("connected", Boolean(state.model));
  $("#clock").textContent = date(state.now, {
    weekday: "short",
    year: "numeric",
  });
  $("#clock").dateTime = state.now;
  $("#timezone").textContent = state.timezone;
  document.title = `${pages[view][0]} | ${state.business.name}`;
  const handoffs = state.customers.filter((c) => c.handoff).length;
  $("#handoff-count").textContent = handoffs || "";
  $("#seed-summary").textContent =
    `${state.customers.length} customers · ${state.technicians.length} technicians · ${state.visits.length} appointments · ${state.quotes.length} quotes`;
  $("#customer-total").textContent = state.customers.length;
  html(
    "#customer-list",
    state.customers
      .map(
        (c) =>
          `<button class="customer-row ${c.id === selected ? "active" : ""}" data-customer="${c.id}" aria-pressed="${c.id === selected}"><span class="customer-row-name">${esc(c.name)}</span><span class="customer-preview">${esc(c.last_message || "No messages")}</span><span class="customer-state ${c.handoff ? "needs-owner" : ""}">${c.handoff ? "Owner reply required" : "Automatic replies active"}</span></button>`,
      )
      .join(""),
  );
  html(
    "#customer-select",
    state.customers
      .map(
        (c) =>
          `<option value="${c.id}" ${c.id === selected ? "selected" : ""}>${esc(c.name)}</option>`,
      )
      .join(""),
  );
  const tech = $("#visit-tech").value;
  html(
    "#visit-tech",
    '<option value="all">All technicians</option>' +
      state.technicians
        .map(
          (t) =>
            `<option value="${t.id}" ${String(t.id) === tech ? "selected" : ""}>${esc(t.name)}</option>`,
        )
        .join(""),
  );
  renderVisits();
  renderQuotes();
  renderRules();
  renderAutomation();
  renderActivity();
  const roleName = (role) =>
    role === "owner" ? "Owner instruction" : "Assistant response";
  html(
    "#owner-messages",
    [...state.owner_messages]
      .reverse()
      .filter((m) => m.kind !== "report")
      .slice(0, 30)
      .map(
        (m) =>
          `<article class="instruction-record"><header><strong>${roleName(m.role)}</strong><time>${esc(date(m.at))}</time></header><p>${esc(m.body)}</p></article>`,
      )
      .join("") || empty("No owner instructions recorded."),
  );
  html(
    "#app-configuration",
    [
      ["Business", state.business.name],
      ["Timezone", state.timezone],
      ["AI mode", state.mode],
      ["Provider", state.provider],
      ["Configured model", state.configured_model],
      ["Messaging / calendar", "Simulated locally"],
      ["Application", "FastAPI"],
      ["Persistent storage", "SQLite"],
      ["Configuration file", "desk/business.json"],
      ["Provider configuration", ".env"],
    ]
      .map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`)
      .join(""),
  );
}
function renderVisits() {
  const range = $("#visit-range").value,
    tech = $("#visit-tech").value;
  const visits = state.visits.filter(
    (v) =>
      (range === "all" ||
        (range === "today"
          ? v.start.slice(0, 10) === state.now.slice(0, 10)
          : new Date(v.start) >= new Date(state.now))) &&
      (tech === "all" || v.tech === Number(tech)),
  );
  html(
    "#visits",
    visits.length
      ? `<table class="table align-middle"><thead><tr><th>Visit</th><th>Customer / service</th><th>Technician</th><th>Address</th><th>Gate / access</th></tr></thead><tbody>${visits.map((v) => `<tr><td>${esc(date(v.start))}<small>${v.duration_minutes} minutes${new Date(v.start) < new Date(state.now) ? " · Past" : ""}</small></td><td>${esc(v.customer_name)}<small>${esc(v.issue)}</small></td><td>${esc(v.technician)}</td><td>${esc(v.address)}</td><td>${esc(v.gate || "Not provided")}</td></tr>`).join("")}</tbody></table>`
      : empty("No appointments match these filters."),
  );
  $("#team-count").textContent = `${state.technicians.length} technicians`;
  html(
    "#team",
    `<table class="table"><thead><tr><th>Name</th><th>Visits today</th><th>Upcoming visits</th></tr></thead><tbody>${state.technicians.map((t) => `<tr><td>${esc(t.name)}</td><td>${state.visits.filter((v) => v.tech === t.id && v.start.slice(0, 10) === state.now.slice(0, 10)).length}</td><td>${state.visits.filter((v) => v.tech === t.id && new Date(v.start) >= new Date(state.now)).length}</td></tr>`).join("")}</tbody></table>`,
  );
}
function renderQuotes() {
  const filter = $("#quote-filter").value;
  const quotes = state.quotes.filter(
    (q) => filter === "all" || q.status === filter,
  );
  html(
    "#quotes",
    quotes.length
      ? `<table class="table align-middle"><thead><tr><th>Customer</th><th>Estimate / work</th><th>Last updated</th><th>Status</th><th>Next action</th></tr></thead><tbody>${quotes.map((q) => `<tr><td>${esc(q.customer_name)}</td><td>${esc(q.job)}</td><td>${esc(date(q.updated))}</td><td><select class="form-select form-select-sm" data-quote="${q.id}" aria-label="Quote status for ${esc(q.customer_name)}">${["owed", "sent", "accepted", "declined"].map((status) => `<option value="${status}" ${status === q.status ? "selected" : ""}>${status[0].toUpperCase() + status.slice(1)}</option>`).join("")}</select></td><td>${q.status === "owed" ? "Business needs to send estimate" : q.status === "sent" ? "Await customer; follow-up after 3 quiet days" : "No follow-up scheduled"}</td></tr>`).join("")}</tbody></table>`
      : empty("No quotes match this status."),
  );
}
function renderRules() {
  const b = state.business,
    days = [
      "Monday",
      "Tuesday",
      "Wednesday",
      "Thursday",
      "Friday",
      "Saturday",
      "Sunday",
    ];
  html(
    "#business-details",
    `<dl class="business-rule-list"><dt>Opening days</dt><dd>${b.open_weekdays.map((i) => days[i]).join(", ") || "Closed for new bookings"}</dd><dt>Hours</dt><dd>${String(b.opening_hour).padStart(2, "0")}:00 to ${String(b.closing_hour).padStart(2, "0")}:00 (${esc(b.timezone)})</dd><dt>Standard visit length</dt><dd>${b.visit_minutes} minutes</dd><dt>Services</dt><dd>${b.services.map(esc).join(", ")}</dd>${Object.entries(
      b.facts,
    )
      .map(
        ([k, v]) => `<dt>${esc(k.replaceAll("_", " "))}</dt><dd>${esc(v)}</dd>`,
      )
      .join(
        "",
      )}</dl><p class="section-note">Opening-day changes affect new bookings. Existing appointments remain scheduled. Services, hours and visit length are set in business configuration.</p>`,
  );
}
function renderAutomation() {
  html(
    "#schedules",
    state.schedules.length
      ? `<table class="table align-middle"><thead><tr><th>Report</th><th>Schedule</th><th>Next delivery</th><th><span class="visually-hidden">Actions</span></th></tr></thead><tbody>${state.schedules.map((s) => `<tr><td>${esc(s.label)}</td><td>${esc(s.cadence)}, ${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}</td><td>${esc(date(s.next_run))}</td><td><button class="btn btn-sm btn-outline-secondary" data-remove-schedule="${s.id}" aria-label="Remove ${esc(s.label)} schedule">Remove</button></td></tr>`).join("")}</tbody></table>`
      : empty("No report schedules. Add one below."),
  );
  const reports = state.owner_messages
    .filter((m) => m.kind === "report")
    .reverse();
  html(
    "#reports",
    reports
      .map(
        (m) =>
          `<article class="report-entry"><h3>${m.body.startsWith("Morning rundown") ? "Morning rundown" : m.body.startsWith("Waiting") ? "Quotes owed" : "Open handoffs"}<time>${esc(date(m.at))}</time></h3><p>${esc(m.body)}</p></article>`,
      )
      .join("") ||
      empty(
        "No reports delivered yet. Advance the demo clock to 8 am or request a report above.",
      ),
  );
  const filter = $("#delivery-filter").value,
    deliveries = state.deliveries.filter(
      (m) => filter === "all" || m.kind === filter,
    );
  html(
    "#deliveries",
    deliveries.length
      ? `<table class="table"><thead><tr><th>Delivered</th><th>Recipient</th><th>Type</th><th>Message</th></tr></thead><tbody>${deliveries.map((m) => `<tr><td>${esc(date(m.at))}</td><td>${esc(m.customer_name || "Owner")}</td><td>${esc(kinds[m.kind] || m.kind)}</td><td class="message-cell">${esc(m.body)}</td></tr>`).join("")}</tbody></table>`
      : empty(
          "No matching deliveries. Advance the demo clock to run scheduled work.",
        ),
  );
}
function renderActivity() {
  const query = $("#activity-search").value.toLowerCase().trim();
  const events = state.events.filter((e) =>
    `${e.action} ${e.reason} ${e.source_body || ""}`
      .toLowerCase()
      .includes(query),
  );
  html(
    "#events",
    events.length
      ? `<table class="table"><thead><tr><th>Time</th><th>Action</th><th>Reason</th><th>Source message</th></tr></thead><tbody>${events.map((e) => `<tr><td>${esc(date(e.at))}</td><td>${esc(e.action)}</td><td>${esc(e.reason)}</td><td class="message-cell">${esc(e.source_body || "Owner control or scheduled process")}</td></tr>`).join("")}</tbody></table>`
      : empty(
          query
            ? "No matching actions."
            : "No actions recorded. Book a visit, change a rule or advance the demo clock.",
        ),
  );
}
function details(ctx) {
  const c = ctx.customer;
  const future = ctx.visits.filter(
    (v) => new Date(v.start) >= new Date(state.now),
  );
  const past = ctx.visits.filter(
    (v) => new Date(v.start) < new Date(state.now),
  );
  const record = (v) =>
    `<div class="record-visit">${esc(date(v.start))}<small>${esc(v.issue)}</small><small>Technician: ${esc(v.technician)}</small></div>`;
  return `<dl class="record-list"><dt>Name</dt><dd>${esc(c.name)}</dd><dt>Service address</dt><dd>${esc(c.address || "Not provided")}</dd><dt>Gate / access code</dt><dd>${esc(c.gate || "Not provided")}</dd>${c.issue ? `<dt>Current request</dt><dd>${esc(c.issue)}</dd>` : ""}</dl><h3 class="record-subheading">Upcoming visits</h3>${future.map(record).join("") || '<p class="secondary-text">No upcoming visits.</p>'}<h3 class="record-subheading">Visit history</h3>${past.slice(-3).reverse().map(record).join("") || '<p class="secondary-text">No previous visits.</p>'}`;
}
function renderConversation() {
  const c = conversation.customer,
    changed = selected !== renderedCustomer;
  $("#thread-name").textContent = c.name;
  $("#thread-address").textContent = c.address || "Address not provided";
  $("#customer-status").textContent = c.handoff
    ? "Owner reply required"
    : "Automatic replies active";
  html(
    "#thread-state",
    `<span class="status-text ${c.handoff ? "waiting" : ""}">${c.handoff ? "Owner reply required" : "Assistant active"}</span>`,
  );
  $("#handoff-banner").hidden = !c.handoff;
  html(
    "#handoff-banner",
    c.handoff
      ? `<strong>Handoff summary</strong><p class="handoff-summary">${esc(c.handoff)}</p><p>Automatic replies are paused.</p><button class="btn btn-sm btn-outline-secondary" data-resume="${c.id}">Resume assistant</button>`
      : "",
  );
  $("#customer-paused").hidden = !c.handoff;
  renderMessages(
    "#customer-thread",
    conversation.messages,
    "customer",
    changed,
  );
  renderMessages("#owner-thread", conversation.messages, "owner", changed);
  html("#sim-details", details(conversation));
  html("#customer-details", details(conversation));
  html(
    "#slot-options",
    !c.handoff && c.offers.length
      ? "<h3>Select an available appointment</h3>" +
          c.offers
            .map(
              (s, i) =>
                `<button class="btn btn-outline-primary" data-choice="${i + 1}">${esc(date(s, { weekday: "short" }))}</button>`,
            )
            .join("")
      : "",
  );
  renderedCustomer = selected;
}
html(
  "#customer-examples",
  customerExamples
    .map(
      ([label, text]) =>
        `<button class="btn btn-sm btn-outline-secondary" data-fill-target="customer-text" data-example="${esc(text)}">${esc(label)}</button>`,
    )
    .join(""),
);
window.addEventListener("hashchange", route);
document.addEventListener("click", async (event) => {
  const b = event.target.closest("button");
  if (!b) return;
  if (b.dataset.fillTarget) {
    $("#" + b.dataset.fillTarget).value = b.dataset.example;
    $("#" + b.dataset.fillTarget).focus();
  }
  if (b.dataset.customer) {
    selected = Number(b.dataset.customer);
    await refresh().catch((e) => showError(e.message));
  }
  if (b.dataset.step) {
    if (await mutate("/api/clock/advance", { step: b.dataset.step }))
      notify("Demo clock advanced. Due work has run.");
  }
  if (b.dataset.choice)
    await mutate(`/api/customers/${selected}/messages`, {
      text: b.dataset.choice,
    });
  if (b.dataset.resume) {
    if (await mutate(`/api/customers/${b.dataset.resume}/resume`, {}))
      notify("Automatic replies resumed.");
  }
  if (b.dataset.removeSchedule) {
    if (
      await mutate(`/api/schedules/${b.dataset.removeSchedule}`, {}, "DELETE")
    )
      notify("Report schedule removed.");
  }
});
$("#customer-select").addEventListener("change", async (event) => {
  selected = Number(event.target.value);
  await refresh().catch((e) => showError(e.message));
});
$("#quotes").addEventListener("change", async (event) => {
  if (
    event.target.dataset.quote &&
    (await mutate(
      `/api/quotes/${event.target.dataset.quote}`,
      { status: event.target.value },
      "PATCH",
    ))
  )
    notify("Quote status updated.");
});
for (const [formId, inputId, routeFn, resultId] of [
  [
    "customer-form",
    "customer-text",
    () => `/api/customers/${selected}/messages`,
    null,
  ],
  ["reply-form", "reply-text", () => `/api/customers/${selected}/reply`, null],
  ["rule-form", "rule-text", () => "/api/owner/messages", "rule-result"],
  [
    "schedule-form",
    "schedule-text",
    () => "/api/owner/messages",
    "schedule-result",
  ],
]) {
  const form = $("#" + formId),
    input = $("#" + inputId);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || busy) return;
    const body = { text };
    if (formId === "reply-form") body.resume = $("#reply-resume").checked;
    const submit = form.querySelector('[type="submit"]'),
      label = submit.textContent;
    submit.textContent = "Sending…";
    const result = await mutate(routeFn(), body);
    submit.textContent = label;
    if (result) {
      input.value = "";
      if (formId === "reply-form") $("#reply-resume").checked = false;
      if (resultId) {
        $("#" + resultId).hidden = false;
        $("#" + resultId).textContent = result.reply;
      }
      if (result.status === "waiting_for_owner")
        notify("Message saved. The owner is handling this conversation.");
    }
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
}
for (const id of ["visit-range", "visit-tech"])
  $("#" + id).addEventListener("change", renderVisits);
$("#quote-filter").addEventListener("change", renderQuotes);
$("#delivery-filter").addEventListener("change", renderAutomation);
$("#activity-search").addEventListener("input", renderActivity);
$("#confirm-reset").addEventListener("click", async () => {
  if (busy) return;
  const result = await mutate("/api/demo/reset", {});
  if (result) {
    selected = 1;
    renderedCustomer = null;
    await refresh();
    resetModal.hide();
    notify("Demo data and clock restored.");
  }
});
route();
refresh().catch((error) => showError(error.message, "refresh"));
setInterval(() => {
  if (!busy && !document.hidden)
    refresh().catch((error) => showError(error.message, "refresh"));
}, 5000);
