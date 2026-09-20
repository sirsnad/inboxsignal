/* Today screen renderer. Read-only in Phase 1: clicking action-shaped
   things explains that actions arrive in Phase 2. */

let selected = null;

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._h);
  toast._h = setTimeout(() => t.classList.add("hidden"), 3500);
}

async function load() {
  const data = await (await fetch("/api/today")).json();
  render(data);
}

function render(d) {
  $("date").textContent = d.date;
  $("account").textContent = d.account || "no account synced";

  const c = d.counters;
  $("c-open").textContent = c.open;
  $("c-open").className = c.open > 0 ? "hot" : "ok";
  $("c-waiting").textContent = c.waiting;
  $("c-in").textContent = c.arrived_today;
  $("c-filed").textContent = c.filed_today;
  $("nav-open").textContent = c.open === 0 ? "clear" : `${c.open} open`;
  $("nav-open").className = "badge " + (c.open === 0 ? "ok" : "hot");
  $("nav-waiting").textContent = c.waiting;
  const newN = d.new_senders.length;
  $("nav-senders").textContent = newN ? `${newN} new` : "";
  $("nav-senders").className = "badge " + (newN ? "hot" : "");

  const filed = c.arrived_today ? Math.round((c.filed_today / c.arrived_today) * 100) : 0;
  $("foot-line").textContent = `${c.filed_today} of ${c.arrived_today} filed for you`;
  $("foot-meter").style.width = `${filed}%`;
  $("foot-sub").textContent =
    c.open === 0 ? "Everything handled." : `${c.open} still need a look.`;

  renderPeople(d.people);
  renderNeeds(d.needs_you);
  renderNewSenders(d.new_senders);
  renderHeads(d.heads_up);
  renderOnWay(d.on_its_way);
  renderWaiting(d.waiting);
  renderMoney(d.money);
  renderLanes(d.lanes);
  renderPromos(d.promotions);
}

function renderPeople(items) {
  const box = $("people-list");
  box.innerHTML = "";
  box.style.display = "flex";
  box.style.flexDirection = "column";
  box.style.gap = "8px";
  if (!items.length) {
    box.innerHTML = `<div class="banner green">No one is waiting on you.</div>`;
    return;
  }
  for (const p of items) {
    const el = document.createElement("div");
    el.className = "card selectable person-row";
    el.dataset.thread = p.thread_id;
    el.innerHTML = `
      <div class="avatar ${p.following ? "faded" : ""}">${esc(p.initials)}</div>
      <div class="grow">
        <div class="row-top">
          <div class="row-name">${esc(p.name)}</div>
          ${p.following
            ? `<span class="chip">Following</span>`
            : `<div class="row-age ${p.age_hot ? "hot" : ""}">${esc(p.age_label)}</div>`}
        </div>
        <div class="row-body">${esc(p.summary)}</div>
        ${p.note ? `<div class="row-sub">${esc(p.note)}</div>` : ""}
      </div>`;
    el.addEventListener("click", () => select(el, p.thread_id));
    box.appendChild(el);
  }
}

function renderNeeds(items) {
  const box = $("needs-list");
  box.innerHTML = "";
  if (!items.length) {
    box.innerHTML = `<div class="banner green">✓&nbsp; Nothing needs you.</div>`;
    return;
  }
  const list = document.createElement("div");
  list.className = "card-list";
  for (const n of items) {
    const row = document.createElement("div");
    row.className = "row selectable";
    row.dataset.thread = n.thread_id;
    row.innerHTML = `
      <div class="grow">
        <div class="row-name" style="font-size:14px">${esc(n.title)}</div>
        <div class="row-sub">${esc(n.detail)}
          ${n.age_label ? ` · <span class="${n.age_hot ? "row-age hot" : ""}" style="display:inline">${esc(n.age_label)}</span>` : ""}
        </div>
      </div>
      <button class="pill" data-phase2>Act</button>`;
    row.addEventListener("click", () => select(row, n.thread_id));
    list.appendChild(row);
  }
  box.appendChild(list);
}

function renderNewSenders(items) {
  const sec = $("sec-new-senders");
  sec.classList.toggle("hidden", !items.length);
  const box = $("new-senders-list");
  box.innerHTML = "";
  for (const s of items) {
    const row = document.createElement("div");
    row.className = "row";
    const opts = ["person", "service", "promo"]
      .map((t) =>
        `<button class="pill ${s.guess === t ? "tinted" : ""}" data-phase2>${t[0].toUpperCase() + t.slice(1)}</button>`)
      .join("");
    row.innerHTML = `
      <div class="grow">
        <div class="row-name" style="font-size:13px">${esc(s.name)}</div>
        <div class="row-sub">${esc(s.hint || `first email this week · guessed ${s.guess || "?"}`)}</div>
      </div>${opts}`;
    box.appendChild(row);
  }
}

function renderHeads(h) {
  const sec = $("sec-heads");
  sec.classList.toggle("hidden", !h.count);
  if (h.count) {
    $("heads-banner").innerHTML =
      `<div class="grow"><b>${h.count} heads-up${h.count > 1 ? "s" : ""}, nothing to do.</b> ${esc(h.line)}</div>`;
  }
}

function renderOnWay(items) {
  $("col-onway").classList.toggle("hidden", !items.length);
  const box = $("onway-list");
  box.innerHTML = items
    .map((i) => `<div class="mini-row"><div>${esc(i.title)}</div><div class="ok">${esc(i.eta || i.status)}</div></div>`)
    .join("");
}

function renderWaiting(items) {
  $("col-waiting").classList.toggle("hidden", !items.length);
  const box = $("waiting-list");
  box.innerHTML = items
    .map((w) => `<div class="mini-row"><div>${esc(w.name)} <span class="dim">${esc(w.what)}</span></div><div class="dim">${esc(w.days_label)}</div></div>`)
    .join("");
}

function renderMoney(items) {
  $("sec-money").classList.toggle("hidden", !items.length);
  $("money-list").innerHTML = items
    .map((m) => `<div class="mini-row">
        <div>${esc(m.merchant)} ${m.tag ? `<span class="dim">${esc(m.tag)}</span>` : ""}${m.detail ? ` <span class="dim">${esc(m.detail)}</span>` : ""}</div>
        <div class="strong">${esc(m.amount)}</div></div>`)
    .join("");
}

function renderLanes(lanes) {
  $("lane-grid").innerHTML = lanes
    .map((l) => {
      const count = l.no_count || l.count == null ? "" :
        `<span class="lane-count">${l.count}</span>`;
      const body = l.no_count
        ? `<div class="lane-titles">${l.titles.map(esc).join("<br>") || "<span class='lane-sum'>quiet</span>"}</div>`
        : `<div class="lane-sum">${esc(l.summary) || "quiet today"}</div>`;
      return `<div class="lane-tile"><div class="lane-name"><span>${esc(l.name)}</span>${count}</div>${body}</div>`;
    })
    .join("");
  $("nav-lanes").innerHTML = lanes
    .map((l) => `<a href="#lane-${esc(l.name)}"><div>${esc(l.name)}</div>
       <div class="badge">${l.no_count || l.count == null ? "" : l.count || ""}</div></a>`)
    .join("");
}

function renderPromos(p) {
  $("promo-line").innerHTML = `
    <div class="grow">
      <b>Promotions · ${p.count_today} filed today</b>
      <div class="sub">${esc(p.digest_day)} digest${p.unsub.length ? ` · ${p.unsub.map(esc).join(", ")} up for unsubscribe` : ""}</div>
    </div><div>›</div>`;
  $("nav-promos").textContent = `${p.count_today} · ${p.digest_day.slice(0, 3)}`;
}

async function select(el, threadId) {
  document.querySelectorAll(".selected").forEach((n) => n.classList.remove("selected"));
  el.classList.add("selected");
  selected = threadId;
  const r = await fetch(`/api/thread/${threadId}`);
  if (!r.ok) return;
  renderPane(await r.json());
}

function renderPane(t) {
  const pane = $("pane");
  const tierClass = t.tier === "person" ? "" : "service";
  const pulled = [
    t.pulled_out.ask && item("＋", `<b>The ask:</b> ${esc(t.pulled_out.ask)}`, "var(--terra)"),
    t.pulled_out.age_note && item("◷", esc(t.pulled_out.age_note)),
    t.pulled_out.related && item("◉", esc(t.pulled_out.related)),
  ].filter(Boolean).join("");
  pane.innerHTML = `
    <div>
      <div class="badges">
        <span class="badge-tier ${tierClass}">${esc(cap(t.tier))}</span>
        <span class="evidence">${esc(t.evidence)}</span>
      </div>
      <h2 style="margin-top:10px">${esc(t.subject)}</h2>
      <div class="meta" style="margin-top:10px">
        <div class="avatar ${t.tier === "person" ? "" : "hollow"}">${esc(t.initials)}</div>
        <div>
          <div class="who">${esc(t.sender_name)} <span>${esc(t.sender_domain)}</span></div>
          <div class="sub">${esc(t.recipients_summary)}</div>
        </div>
      </div>
    </div>
    ${pulled ? `<div class="pulled"><div class="label">Pulled out for you</div>${pulled}</div>` : ""}
    <div class="msg-body">${esc(t.body)}</div>
    <div class="footer">${esc(t.footer)}</div>`;

  function item(ico, html, color) {
    return `<div class="item"><span class="ico" style="color:${color || "var(--muted)"}">${ico}</span><div>${html}</div></div>`;
  }
}

function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-phase2]");
  if (b) {
    e.stopPropagation();
    e.preventDefault();
    toast("Phase 1 is a read-only mirror. Actions arrive in Phase 2.");
  }
});

load();
setInterval(load, 60000);
