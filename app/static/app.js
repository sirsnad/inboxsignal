/* Today screen + actions (Phase 2). Every action shows an undo toast for
   12s and lands in the "What just happened in Gmail" log. Sends are held
   12s before Gmail sees them. */

const $ = (id) => document.getElementById(id);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));

async function post(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `${r.status}`);
  }
  return r.json();
}

/* ---------- pane state ---------- */
// mode: empty | read | compose | sent
const pane = { mode: "empty", thread: null, draft: "", replyAll: false,
               composeMode: "reply", sent: null };

/* ---------- undo toast ---------- */
let toastTimer = null, countTimer = null;

function showToast(msg, actionId, seconds, onUndo) {
  const t = $("toast");
  clearTimeout(toastTimer); clearInterval(countTimer);
  if (!actionId) {
    t.innerHTML = esc(msg);
    t.classList.remove("hidden");
    toastTimer = setTimeout(() => t.classList.add("hidden"), 4000);
    return;
  }
  let left = seconds || 12;
  t.innerHTML = `<span>${esc(msg)}</span>
    <button class="pill" style="border-color:#5e5a52;background:transparent;color:#fff"
      id="toast-undo">Undo · <span id="toast-count">${left}</span>s</button>`;
  t.classList.remove("hidden");
  $("toast-undo").addEventListener("click", async () => {
    try {
      await post(`/api/actions/${actionId}/undo`);
      t.classList.add("hidden");
      clearInterval(countTimer);
      if (onUndo) onUndo();
      showToast("Undone.");
      refresh();
    } catch (e) { showToast(e.message); }
  });
  countTimer = setInterval(() => {
    left -= 1;
    const el = $("toast-count");
    if (el) el.textContent = left;
    if (left <= 0) { clearInterval(countTimer); t.classList.add("hidden"); }
  }, 1000);
}

/* ---------- data load ---------- */
async function refresh() {
  const data = await (await fetch("/api/today")).json();
  render(data);
  if (pane.mode === "empty") renderEmptyPane();
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
  $("nav-senders").textContent = newN ? `${newN} new` : "sorted";
  $("nav-senders").className = "badge " + (newN ? "hot" : "ok");

  const filed = c.arrived_today ? Math.round((c.filed_today / c.arrived_today) * 100) : 0;
  $("foot-line").textContent = `${c.filed_today} of ${c.arrived_today} filed for you`;
  $("foot-meter").style.width = `${filed}%`;
  $("foot-sub").textContent =
    c.open === 0 ? "Everything handled." : `${c.open} still need a look.`;

  renderPeople(d.people);
  renderNeeds(d.needs_you, d.recent_done);
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
  Object.assign(box.style, { display: "flex", flexDirection: "column", gap: "8px" });
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
          <div class="row-name">${esc(p.name)}
            ${p.returned ? `<span class="chip">returned</span>` : ""}</div>
          ${p.following
            ? `<span class="chip">Following</span>`
            : `<div class="row-age ${p.age_hot ? "hot" : ""}">${esc(p.age_label)}</div>`}
        </div>
        <div class="row-body">${esc(p.summary)}</div>
        ${p.note ? `<div class="row-sub">${esc(p.note)}</div>` : ""}
      </div>`;
    el.addEventListener("click", () => openThread(el, p.thread_id));
    box.appendChild(el);
  }
}

function renderNeeds(items, recentDone) {
  const box = $("needs-list");
  box.innerHTML = "";
  if (items.length) {
    const list = document.createElement("div");
    list.className = "card-list";
    for (const n of items) {
      const row = document.createElement("div");
      row.className = "row selectable";
      const buttons = n.answer_mode === "yesno"
        ? `<button class="pill primary" data-a="yes">Yes, me</button>
           <button class="pill" data-a="no">No</button>`
        : `<button class="pill" data-a="ack">That was me</button>`;
      row.innerHTML = `
        <div class="grow">
          <div class="row-name" style="font-size:14px">${esc(n.title)}</div>
          <div class="row-sub">${esc(n.detail)}
            ${n.age_label ? ` · <span class="${n.age_hot ? "row-age hot" : ""}" style="display:inline">${esc(n.age_label)}</span>` : ""}
          </div>
        </div>${buttons}`;
      row.querySelectorAll("[data-a]").forEach((b) =>
        b.addEventListener("click", async (e) => {
          e.stopPropagation();
          try {
            const res = await post("/api/actions/confirm",
              { message_id: n.message_id, answer: b.dataset.a });
            if (res.open_url) window.open(res.open_url, "_blank", "noopener");
            showToast(res.description.split(".")[0] + ".", res.action_id, res.undo_seconds);
            refresh();
          } catch (err) { showToast(err.message); }
        }));
      row.addEventListener("click", () => openThread(row, n.thread_id));
      list.appendChild(row);
    }
    box.appendChild(list);
  } else {
    box.innerHTML = `<div class="banner green">✓&nbsp; Nothing needs you.</div>`;
  }
  for (const r of recentDone || []) {
    const line = document.createElement("div");
    line.className = "done-line";
    line.innerHTML = `<span class="check">✓</span> ${esc(r.line)}`;
    box.appendChild(line);
  }
}

function renderNewSenders(items) {
  const sec = $("sec-new-senders");
  sec.classList.toggle("hidden", !items.length);
  const box = $("new-senders-list");
  box.innerHTML = "";
  for (const s of items) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `
      <div class="grow">
        <div class="row-name" style="font-size:13px">${esc(s.name)}</div>
        <div class="row-sub">${esc(s.hint || `first email this week · guessed ${s.guess || "?"}`)}</div>
      </div>` +
      ["person", "service", "promo"]
        .map((t) => `<button class="pill ${s.guess === t ? "tinted" : ""}" data-t="${t}">
             ${t[0].toUpperCase() + t.slice(1)}</button>`)
        .join("");
    row.querySelectorAll("[data-t]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          const res = await post("/api/actions/rule",
            { address: s.address, tier: b.dataset.t });
          showToast(`${s.name} filed as ${b.dataset.t} from now on.`,
                    res.action_id, res.undo_seconds);
          refresh();
        } catch (err) { showToast(err.message); }
      }));
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
  $("onway-list").innerHTML = items
    .map((i) => `<div class="mini-row"><div>${esc(i.title)}</div><div class="ok">${esc(i.eta || i.status)}</div></div>`)
    .join("");
}

function renderWaiting(items) {
  $("col-waiting").classList.toggle("hidden", !items.length);
  const box = $("waiting-list");
  box.innerHTML = "";
  for (const w of items) {
    const row = document.createElement("div");
    row.className = "mini-row";
    row.innerHTML = `<div>${esc(w.name)} <span class="dim">${esc(w.what)}</span></div>`;
    const right = document.createElement("div");
    if (w.days >= 2) {
      const b = document.createElement("button");
      b.className = "pill";
      b.textContent = "Nudge";
      b.addEventListener("click", () => startCompose(w.thread_id, "nudge"));
      right.appendChild(b);
    } else {
      right.className = "dim";
      right.textContent = w.days_label;
    }
    row.appendChild(right);
    box.appendChild(row);
  }
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

/* ---------- reading pane ---------- */

async function openThread(el, threadId) {
  document.querySelectorAll(".selected").forEach((n) => n.classList.remove("selected"));
  if (el) el.classList.add("selected");
  const r = await fetch(`/api/thread/${threadId}`);
  if (!r.ok) return;
  pane.thread = await r.json();
  pane.mode = "read";
  renderPane();
}

function item(ico, html, color) {
  return `<div class="item"><span class="ico" style="color:${color || "var(--muted)"}">${ico}</span><div>${html}</div></div>`;
}

function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

function renderPane() {
  const box = $("pane");
  if (pane.mode === "empty") { renderEmptyPane(); return; }
  const t = pane.thread;

  if (pane.mode === "compose") { renderCompose(box, t); return; }
  if (pane.mode === "sent") { renderSent(box, t); return; }

  const tierClass = t.tier === "person" ? "" : "service";
  const pulled = [
    t.pulled_out.ask && item("＋", `<b>The ask:</b> ${esc(t.pulled_out.ask)}`, "var(--terra)"),
    t.pulled_out.age_note && item("◷", esc(t.pulled_out.age_note)),
    t.pulled_out.related && item("◉", esc(t.pulled_out.related)),
  ].filter(Boolean).join("");
  const actionsHtml = t.can_reply && t.tier === "person" ? `
    <div style="display:flex;gap:8px;padding-top:4px">
      <button class="pill primary" style="height:38px;border-radius:19px" id="p-reply">
        Reply to ${esc(t.first_name)} only</button>
      <button class="pill" style="height:38px;border-radius:19px" id="p-done">Mine's fine, done</button>
    </div>` : `
    <div style="display:flex;gap:8px;padding-top:4px">
      <button class="pill" style="height:38px;border-radius:19px" id="p-done">Done</button>
    </div>`;
  box.innerHTML = `
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
    ${pulled ? `<div class="pulled"><div class="label">Pulled out for you</div>${pulled}${actionsHtml}</div>`
             : actionsHtml}
    <div class="msg-body">${esc(t.body)}</div>
    <div class="footer" style="display:flex;justify-content:space-between;align-items:center">
      <span>${esc(t.footer)}</span>
      <span id="snooze-zone"><button class="pill" id="p-snooze">Snooze</button></span>
    </div>`;

  const reply = $("p-reply");
  if (reply) reply.addEventListener("click", () => startCompose(t.thread_id, "reply"));
  $("p-done").addEventListener("click", async () => {
    try {
      const res = await post("/api/actions/done", { thread_id: t.thread_id });
      pane.mode = "empty";
      renderPane();
      showToast(`${t.sender_name}'s thread marked done. Nothing sent.`,
                res.action_id, res.undo_seconds);
      refresh();
    } catch (err) { showToast(err.message); }
  });
  $("p-snooze").addEventListener("click", () => {
    $("snooze-zone").innerHTML = ["tomorrow", "monday", "week"]
      .map((p) => `<button class="pill" data-s="${p}" style="margin-left:6px">${cap(p)}</button>`)
      .join("");
    $("snooze-zone").querySelectorAll("[data-s]").forEach((b) =>
      b.addEventListener("click", async () => {
        try {
          const res = await post("/api/actions/snooze",
            { thread_id: t.thread_id, preset: b.dataset.s });
          pane.mode = "empty";
          renderPane();
          showToast(res.description.split(".")[0] + ".", res.action_id, res.undo_seconds);
          refresh();
        } catch (err) { showToast(err.message); }
      }));
  });
}

async function startCompose(threadId, mode) {
  if (!pane.thread || pane.thread.thread_id !== threadId) {
    await openThread(null, threadId);
  }
  pane.composeMode = mode;
  pane.replyAll = false;
  pane.mode = "compose";
  pane.draft = "…drafting in your voice…";
  renderPane();
  try {
    const d = await post(`/api/thread/${threadId}/draft`, { mode });
    pane.draft = d.draft;
    pane.othersCount = d.others_count;
    pane.toName = d.to_name;
  } catch (e) {
    pane.draft = "";
    showToast(e.message);
  }
  renderPane();
}

function renderCompose(box, t) {
  const others = pane.othersCount ?? t.others_count ?? 0;
  const toName = pane.toName || t.sender_name;
  const n = pane.replyAll ? others + 1 : 1;
  box.innerHTML = `
    <div>
      <div class="evidence">${pane.composeMode === "nudge" ? "Nudging" : "Replying"} in thread</div>
      <h2 style="margin-top:6px">Re: ${esc(t.subject)}</h2>
    </div>
    <div style="border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column">
      <div style="display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line-soft);font-size:13px">
        <div style="color:var(--muted);width:32px">To</div>
        <span style="background:var(--green-tint);color:var(--green-dark);padding:4px 10px;border-radius:12px;font-weight:600">${esc(toName)}</span>
        ${pane.replyAll && others ? `<span style="background:var(--terra-tint);color:var(--terra);padding:4px 10px;border-radius:12px;font-weight:600">+ ${others} more</span>` : ""}
        <div class="grow"></div>
        ${others ? `<button class="pill" id="c-toggle">${pane.replyAll ? `Just ${esc(t.first_name)} instead` : `Reply all instead (${others} more)`}</button>` : ""}
      </div>
      <div style="display:flex;gap:10px;padding:8px 14px;border-bottom:1px solid var(--line-soft);font-size:12px;color:var(--muted)">
        ✦ Starter drafted in your voice. Edit anything; it never sends on its own.
      </div>
      <textarea id="c-body" rows="9" aria-label="Reply body"
        style="border:0;padding:14px;font-size:14px;line-height:1.55;resize:none;outline:none;font-family:inherit">${esc(pane.draft)}</textarea>
      <div style="display:flex;align-items:center;gap:8px;padding:10px 14px;border-top:1px solid var(--line-soft);background:var(--ground)">
        <button class="pill primary" style="height:38px;border-radius:19px" id="c-send">
          Send to ${n === 1 ? esc(t.first_name) : `${n} people`}</button>
        <button class="pill" style="height:38px;border-radius:19px" id="c-cancel">Cancel</button>
        <div class="grow"></div>
        <div style="font-size:12px;color:var(--muted)">Quotes the message below, like Gmail</div>
      </div>
    </div>
    <div style="font-size:13px;line-height:1.5;color:var(--muted);border-left:2px solid var(--line);padding-left:12px">
      ${esc((t.body || "").slice(0, 400))}
    </div>`;
  const toggle = $("c-toggle");
  if (toggle) toggle.addEventListener("click", () => {
    pane.draft = $("c-body").value;
    pane.replyAll = !pane.replyAll;
    renderPane();
  });
  $("c-cancel").addEventListener("click", () => { pane.mode = "read"; renderPane(); });
  $("c-send").addEventListener("click", async () => {
    pane.draft = $("c-body").value;
    try {
      const res = await post(`/api/thread/${t.thread_id}/send`,
        { body: pane.draft, reply_all: pane.replyAll });
      pane.sent = res;
      pane.mode = "sent";
      renderPane();
      refresh();
    } catch (err) { showToast(err.message); }
  });
}

function renderSent(box, t) {
  const s = pane.sent;
  let left = s.undo_seconds || 12;
  box.innerHTML = `
    <div class="banner green" style="border-radius:12px;padding:14px 16px;display:flex;gap:12px;align-items:center">
      <div class="grow">
        <div style="font-weight:600">Sending to ${esc(s.sent_to)}.</div>
        <div style="font-size:13px;font-weight:400">Held for <span id="s-count">${left}</span>s, then it's in your Gmail Sent folder and the thread moves to Waiting on them.</div>
      </div>
      <button class="pill" id="s-undo">Undo · <span id="s-count2">${left}</span>s</button>
    </div>
    <div style="display:flex;gap:12px;align-items:flex-start;padding:14px;background:var(--ground);border-radius:12px">
      <div class="avatar" style="background:var(--green-tint);color:var(--green)">SW</div>
      <div class="grow">
        <div style="display:flex;justify-content:space-between;align-items:baseline">
          <div style="font-size:14px;font-weight:600">You <span style="font-weight:400;color:var(--muted)">to ${esc(s.sent_to)}</span></div>
          <div style="font-size:12px;color:var(--muted)">just now</div>
        </div>
        <div class="msg-body" style="margin-top:4px">${esc(pane.draft)}</div>
      </div>
    </div>
    <div class="footer">When they reply, this thread comes back to People with the reply on top.</div>`;
  const timer = setInterval(() => {
    left -= 1;
    const a = $("s-count"), b = $("s-count2");
    if (a) a.textContent = left;
    if (b) b.textContent = left;
    if (left <= 0) {
      clearInterval(timer);
      const u = $("s-undo");
      if (u) u.remove();
      const c = $("s-count");
      if (c) c.closest("div").textContent = "Sent through Gmail as you. The thread is in Waiting on them.";
    }
  }, 1000);
  $("s-undo").addEventListener("click", async () => {
    clearInterval(timer);
    try {
      await post(`/api/actions/${s.action_id}/undo`);
      pane.mode = "compose";
      renderPane();
      showToast("Send canceled. Nothing left your account.");
      refresh();
    } catch (err) { showToast(err.message); }
  });
}

async function renderEmptyPane() {
  const box = $("pane");
  let logHtml = "";
  try {
    const data = await (await fetch("/api/log")).json();
    const rows = data.log.filter((l) => !l.undone_at).slice(0, 6);
    if (rows.length) {
      logHtml = `<div class="pulled"><div class="label">What just happened in Gmail</div>
        ${rows.map((l) => item("✓", esc(l.description), "var(--green)")).join("")}</div>
        <div class="footer">Nothing was deleted. Every item is still in Gmail, labeled and searchable.</div>`;
    }
  } catch (e) { /* log is decorative */ }
  box.innerHTML = logHtml || `
    <div class="pane-empty">
      <div class="big">Nothing selected.</div>
      <div>Pick a People or Needs you item to read it here.</div>
    </div>`;
}

refresh();
setInterval(() => { if (pane.mode === "empty" || pane.mode === "read") refresh(); }, 60000);
