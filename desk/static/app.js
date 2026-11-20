const $ = (selector) => document.querySelector(selector);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let state,
  selected = 1,
  view = "inbox",
  busy = false,
  refreshId = 0,
  renderedCustomer = null;
let toastTimer;
const titles = {
  inbox: [
    "A good day starts here.",
    "Your conversations, visits, and follow-ups in one place.",
  ],
  customer: [
    "A helpful voice. Every time.",
    "See the front desk from your customer’s side.",
  ],
  operations: [
    "The shop, at a glance.",
    "A clear view of the work ahead and what’s already handled.",
  ],
};
const sampleCustomers = [
  ["Leaking faucet", "My kitchen faucet is leaking."],
  ["Saved details", "What address and gate code do you have for me?"],
  ["Last visit", "What happened on my last visit?"],
  ["Pricing", "How much does a visit cost?"],
  ["Tomorrow", "Tomorrow please"],
  ["Sunday availability", "Any openings on Sunday?"],
  ["Request a quote", "I need a quote for a water heater replacement."],
  ["Talk to the owner", "I want to speak to the owner about a refund."],
];
const sampleOwners = [
  "We stopped doing Sunday calls",
  "Every Friday, tell me who is still waiting on a quote",
  "Show today's rundown",
  "Every day at 9:30 am, show open handoffs",
];
function formatDate(value, options = {}) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: state?.timezone || "America/Chicago",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    ...options,
  }).format(new Date(value));
}
function initials(name) {
  return name
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("");
}
function toast(text, error = false) {
  clearTimeout(toastTimer);
  $("#toast").textContent = text;
  $("#toast").classList.toggle("error", error);
  $("#toast").hidden = false;
  toastTimer = setTimeout(
    () => {
      $("#toast").hidden = true;
    },
    error ? 7000 : 3500,
  );
}
async function api(path, body, method = "POST") {
  const response = await fetch(path, {
    method: body === undefined && method === "POST" ? "GET" : method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : Array.isArray(data.detail)
          ? data.detail.map((v) => v.msg).join(", ")
          : "Could not complete that request.",
    );
  }
  return response.json();
}
async function refresh() {
  const request = ++refreshId,
    customerId = selected;
  const [newState, conversation] = await Promise.all([
    api("/api/state"),
    api(`/api/customers/${customerId}/conversation`),
  ]);
  if (request !== refreshId) return;
  state = newState;
  renderState();
  renderConversation(conversation);
}
async function mutate(path, body, success = "", method = "POST") {
  if (busy) return false;
  busy = true;
  document.body.setAttribute("aria-busy", "true");
  document.querySelectorAll('button[type="submit"]').forEach((el) => {
    el.disabled = true;
    el.dataset.label = el.textContent;
    el.textContent = "Sending…";
  });
  try {
    await api(path, body, method);
    await refresh();
    if (success) toast(success);
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  } finally {
    busy = false;
    document.body.removeAttribute("aria-busy");
    document.querySelectorAll('button[type="submit"]').forEach((el) => {
      el.disabled = false;
      el.textContent = el.dataset.label || "Send ↗";
    });
  }
}
function switchView(next) {
  view = next;
  document.querySelectorAll(".view").forEach((el) => {
    el.hidden = el.id !== `view-${view}`;
  });
  document.querySelectorAll("[data-view]").forEach((el) => {
    el.classList.toggle("active", el.dataset.view === view);
    el.setAttribute(
      "aria-current",
      el.dataset.view === view ? "page" : "false",
    );
  });
  $("#page-title").textContent = titles[view][0];
  $("#page-subtitle").textContent = titles[view][1];
  $("#owner-assistant").hidden = view === "customer";
}
function renderMessages(selector, messages, perspective, forceScroll = false) {
  const el = $(selector),
    nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 65;
  const content = messages.length
    ? messages
        .map((m) => {
          const outgoing =
            perspective === "customer"
              ? m.role === "customer"
              : m.role === "owner";
          const label =
            m.role === "assistant"
              ? m.kind === "report"
                ? "Scheduled report"
                : "Front desk"
              : m.role === "owner"
                ? "Owner"
                : "Customer";
          const kind =
            m.kind !== "chat" && m.kind !== "report"
              ? ` · ${m.kind.replaceAll("_", " ")}`
              : "";
          return `<article class="message ${outgoing ? "outgoing" : ""} ${m.kind !== "chat" ? "automated" : ""}"><div class="message-meta"><span>${esc(label + kind)}</span><time datetime="${esc(m.at)}">${esc(formatDate(m.at))}</time></div><div class="bubble">${esc(m.body)}</div></article>`;
        })
        .join("")
    : `<div class="empty-state"><strong>${perspective === "assistant" ? "Your desk is ready." : "A fresh conversation."}</strong>${perspective === "assistant" ? "Give an instruction or move the demo clock to 8 am for your morning rundown." : "Send a message to get started. Your front desk will take it from here."}</div>`;
  if (el.innerHTML !== content) {
    el.innerHTML = content;
    if (nearBottom || forceScroll) el.scrollTop = el.scrollHeight;
  }
}
function renderState() {
  $("#brand-name").innerHTML =
    `${esc(state.business.name)}<small>YOUR FRONT DESK, HANDLED.</small>`;
  document.title = `${state.business.name} | Front desk`;
  $("#mode").textContent = state.mode;
  $("#footer-mode").textContent = state.model
    ? `AI: ${state.model}`
    : "Guided demo: sample conversations are ready to try.";
  $("#timezone").textContent = state.timezone;
  $("#clock").textContent = formatDate(state.now, {
    weekday: "short",
    year: "numeric",
  });
  const handoffs = state.customers.filter((c) => c.handoff).length;
  const today = state.visits.filter(
    (v) => v.start.slice(0, 10) === state.now.slice(0, 10),
  ).length;
  const owed = state.quotes.filter((q) => q.status === "owed").length;
  const sent = state.quotes.filter((q) => q.status === "sent").length;
  $("#handoff-count").textContent = handoffs;
  const stats = [
    ["Today’s visits", today, "Across your team", "▦"],
    [
      "Needs your attention",
      handoffs,
      handoffs ? "Ready for your reply" : "Conversations are covered",
      "↗",
    ],
    ["Quotes to send", owed, "Customers waiting on you", "▤"],
    ["Quotes sent", sent, "Follow-ups handled automatically", "◷"],
  ];
  $("#stats").innerHTML = stats
    .map(
      ([label, value, hint, icon], i) =>
        `<div class="stat ${i === 1 && handoffs ? "attention" : ""}"><div><span class="stat-label">${label}</span><strong class="stat-value">${value}</strong><p class="stat-hint">${hint}</p></div><span class="stat-icon" aria-hidden="true">${icon}</span></div>`,
    )
    .join("");
  $("#customer-total").textContent = state.customers.length;
  $("#customer-list").innerHTML = state.customers
    .map(
      (c) =>
        `<button class="customer-row ${c.id === selected ? "active" : ""}" data-customer="${c.id}" aria-pressed="${c.id === selected}"><span class="avatar ${c.handoff ? "amber" : ""}">${esc(initials(c.name))}</span><span class="customer-row-text"><span class="customer-row-name">${esc(c.name)}</span><span class="customer-preview">${esc(c.last_message || "Start a new conversation")}</span><span class="customer-state ${c.handoff ? "needs-owner" : ""}">${c.handoff ? "● Needs your reply" : "Assistant active"}</span></span></button>`,
    )
    .join("");
  const select = $("#customer-select");
  const options = state.customers
    .map(
      (c) =>
        `<option value="${c.id}" ${selected === c.id ? "selected" : ""}>${esc(c.name)}</option>`,
    )
    .join("");
  if (select.innerHTML !== options) select.innerHTML = options;
  const visits = state.visits
    .filter((v) => v.start >= state.now.slice(0, 10))
    .slice(0, 60);
  $("#visits").innerHTML = visits.length
    ? `<table><thead><tr><th>WHEN</th><th>CUSTOMER / JOB</th><th>TECHNICIAN</th></tr></thead><tbody>${visits.map((v) => `<tr class="${v.start < state.now ? "past" : ""}"><td>${esc(formatDate(v.start))}<small>${v.duration_minutes} min</small></td><td>${esc(v.customer_name)}<small>${esc(v.issue)}</small><small>${esc(v.address)}${v.gate ? ` · Gate ${esc(v.gate)}` : ""}</small></td><td>${esc(v.technician)}</td></tr>`).join("")}</tbody></table>`
    : '<div class="empty-state">No upcoming visits.</div>';
  $("#team-count").textContent = `${state.technicians.length} TECHNICIANS`;
  $("#team").innerHTML = state.technicians
    .map(
      (t) =>
        `<div class="team-row"><span class="avatar">${esc(initials(t.name))}</span><strong>${esc(t.name)}</strong><span>${state.visits.filter((v) => v.tech === t.id && v.start.slice(0, 10) === state.now.slice(0, 10)).length} visits today</span></div>`,
    )
    .join("");
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  $("#business-details").innerHTML =
    `<strong>${state.business.opening_hour}:00 to ${state.business.closing_hour}:00 · ${state.business.visit_minutes} min visits</strong><p>${state.business.open_weekdays.map((i) => days[i]).join(" · ") || "Closed for new bookings"}</p><p>${state.business.services.map(esc).join(" · ")}</p>${Object.entries(
      state.business.facts,
    )
      .map(
        ([k, v]) =>
          `<p><strong>${esc(k.replaceAll("_", " "))}:</strong> ${esc(v)}</p>`,
      )
      .join("")}`;
  $("#quotes").innerHTML =
    state.quotes
      .map(
        (q) =>
          `<div class="quote-row"><div><h3>${esc(q.customer_name)}</h3><p>${esc(q.job)}</p><p>${q.status === "owed" ? "The shop owes this estimate." : q.status === "sent" ? "Waiting for the customer." : "Resolved"}</p></div><select data-quote="${q.id}" aria-label="Quote status for ${esc(q.customer_name)}">${[
            ["owed", "Owed"],
            ["sent", "Sent"],
            ["accepted", "Accepted"],
            ["declined", "Declined"],
          ]
            .map(
              ([v, label]) =>
                `<option value="${v}" ${q.status === v ? "selected" : ""}>${label}</option>`,
            )
            .join("")}</select></div>`,
      )
      .join("") || '<p class="muted">No quotes yet.</p>';
  $("#schedules").innerHTML =
    state.schedules
      .map(
        (s) =>
          `<div class="schedule-row"><div><h3>${esc(s.label)}</h3><p>${esc(s.cadence)} at ${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}</p><small>Next: ${esc(formatDate(s.next_run))}</small></div><button class="text-button" data-remove-schedule="${s.id}" aria-label="Remove ${esc(s.label)} schedule">Remove</button></div>`,
      )
      .join("") ||
    '<p class="muted">Ask the owner assistant to schedule a report.</p>';
  $("#events").innerHTML =
    state.events
      .map(
        (e) =>
          `<div class="event-row"><time datetime="${esc(e.at)}">${esc(formatDate(e.at))}</time><div><strong>${esc(e.action)}</strong><p>${esc(e.reason)}</p>${e.source_body ? `<div class="event-source">Trigger: “${esc(e.source_body)}”</div>` : ""}</div></div>`,
      )
      .join("") ||
    '<p class="muted">Bookings, rule changes, handoffs and deliveries will appear here.</p>';
  renderMessages("#owner-messages", state.owner_messages, "assistant");
}
function detailHTML(ctx) {
  const c = ctx.customer;
  const future = ctx.visits.filter((v) => v.start >= state.now).slice(0, 3);
  const previous = ctx.visits.filter((v) => v.start < state.now).slice(-1);
  return `<div class="detail-row"><span class="detail-label">NAME</span><div class="detail-value"><strong>${esc(c.name)}</strong></div></div><div class="detail-row"><span class="detail-label">SERVICE ADDRESS</span><div class="detail-value">${esc(c.address || "Not provided yet")}</div></div><div class="detail-row"><span class="detail-label">GATE / ACCESS</span><div class="detail-value">${esc(c.gate || "No gate code on file")}</div></div>${c.issue ? `<div class="detail-row"><span class="detail-label">CURRENT REQUEST</span><div class="detail-value">${esc(c.issue)}</div></div>` : ""}<hr class="detail-divider"><div class="detail-row"><span class="detail-label">UPCOMING VISITS</span>${future.map((v) => `<div class="detail-visit">${esc(formatDate(v.start))}<span>${esc(v.technician)} · ${esc(v.issue)}</span></div>`).join("") || '<div class="detail-value muted">Nothing scheduled yet.</div>'}</div>${previous.map((v) => `<div class="detail-row"><span class="detail-label">PREVIOUS VISIT</span><div class="detail-value">${esc(v.issue)}<span class="muted"><br>${esc(formatDate(v.start))}</span></div></div>`).join("")}`;
}
function renderConversation(ctx) {
  const c = ctx.customer,
    changed = renderedCustomer !== selected;
  $("#thread-name").textContent = c.name;
  $("#thread-address").textContent = c.address || "Address not provided yet";
  $("#thread-state").innerHTML =
    `<span class="status-pill ${c.handoff ? "amber" : ""}">${c.handoff ? "Waiting for owner" : "Assistant active"}</span>`;
  $("#handoff-banner").hidden = !c.handoff;
  $("#handoff-banner").innerHTML = c.handoff
    ? `<strong>Needs your judgment</strong><br>${esc(c.handoff)}<br><button data-resume="${c.id}">Resume assistant</button>`
    : "";
  $("#customer-paused").hidden = !c.handoff;
  renderMessages("#owner-thread", ctx.messages, "owner", changed);
  renderMessages("#customer-thread", ctx.messages, "customer", changed);
  $("#customer-details").innerHTML = detailHTML(ctx);
  $("#sim-details").innerHTML = detailHTML(ctx);
  $("#slot-options").innerHTML = !c.handoff
    ? c.offers
        .map(
          (s, i) =>
            `<button data-choice="${i + 1}">${i + 1}. ${esc(formatDate(s))} ↗</button>`,
        )
        .join("")
    : "";
  renderedCustomer = selected;
}
$("#customer-examples").innerHTML = sampleCustomers
  .map(
    ([label, text]) =>
      `<button data-sample-customer="${esc(text)}">${esc(label)}</button>`,
  )
  .join("");
$("#owner-examples").innerHTML = sampleOwners
  .map(
    (text) =>
      `<button data-sample-owner="${esc(text)}">${esc(text)} ↗</button>`,
  )
  .join("");
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.view) switchView(b.dataset.view);
  if (b.dataset.customer) {
    selected = Number(b.dataset.customer);
    await refresh().catch((err) => toast(err.message, true));
  }
  if (b.dataset.step)
    await mutate(
      "/api/clock/advance",
      { step: b.dataset.step },
      "Demo clock advanced. Due work has run.",
    );
  if (b.dataset.choice)
    await mutate(`/api/customers/${selected}/messages`, {
      text: b.dataset.choice,
    });
  if (b.dataset.sampleCustomer)
    await mutate(`/api/customers/${selected}/messages`, {
      text: b.dataset.sampleCustomer,
    });
  if (b.dataset.sampleOwner)
    await mutate("/api/owner/messages", { text: b.dataset.sampleOwner });
  if (b.dataset.resume)
    await mutate(
      `/api/customers/${b.dataset.resume}/resume`,
      {},
      "Assistant resumed.",
    );
  if (b.dataset.removeSchedule)
    await mutate(
      `/api/schedules/${b.dataset.removeSchedule}`,
      {},
      "Report schedule removed.",
      "DELETE",
    );
});
$("#customer-select").addEventListener("change", async (e) => {
  selected = Number(e.target.value);
  await refresh().catch((err) => toast(err.message, true));
});
$("#quotes").addEventListener("change", async (e) => {
  if (!e.target.dataset.quote) return;
  await mutate(
    `/api/quotes/${e.target.dataset.quote}`,
    { status: e.target.value },
    "Quote updated.",
    "PATCH",
  );
});
for (const [form, input, route] of [
  [
    "#customer-form",
    "#customer-text",
    () => `/api/customers/${selected}/messages`,
  ],
  ["#owner-form", "#owner-text", () => "/api/owner/messages"],
  ["#reply-form", "#reply-text", () => `/api/customers/${selected}/reply`],
]) {
  $(form).addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $(input).value.trim();
    if (!text) return;
    const body = { text };
    if (form === "#reply-form") body.resume = $("#reply-resume").checked;
    if (await mutate(route(), body)) {
      $(input).value = "";
      if (form === "#reply-form") $("#reply-resume").checked = false;
    }
  });
  $(input).addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $(form).requestSubmit();
    }
  });
}
$("#go-owner").addEventListener("click", () => {
  $("#owner-assistant").scrollIntoView({ behavior: "smooth" });
  $("#owner-text").focus({ preventScroll: true });
});
$("#reset").addEventListener("click", async () => {
  if (
    busy ||
    !confirm(
      "Reset conversations, bookings, rules, and reports to the starting demo?",
    )
  )
    return;
  selected = 1;
  renderedCustomer = null;
  await mutate(
    "/api/demo/reset",
    {},
    "Demo restored to Thursday, November 19 at 7:45 am.",
  );
});
refresh().catch((err) => toast(err.message, true));
setInterval(() => {
  if (!busy && !document.hidden)
    refresh().catch((err) => toast(err.message, true));
}, 5000);
