"""The write layer (Phase 2). Every action here:

- writes an `actions` row whose description the UI shows under
  "What just happened in Gmail"
- is undoable for 12 seconds (send uses a 12s hold before the API call;
  everything else records reverse steps in undo_json)
- never deletes, trashes, or archives, and never touches labels outside
  the Signal/ prefix (enforced in the Gmail client too)

Without Gmail credentials (demo mode) the state changes and log still
happen; the log line says no Gmail call was made.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr

from . import config, db

log = logging.getLogger("signal.actions")


# ---------- gmail plumbing ----------

def _client():
    if config.DEMO or not config.TOKEN_PATH.exists():
        return None
    from .gmail import auth
    from .gmail.client import GmailClient
    return GmailClient(auth.get_credentials())


def _ensure_label(conn, client, name: str) -> str | None:
    assert name.startswith(config.LABEL_PREFIX)
    if client is None:
        return None
    key = f"label:{name}"
    cached = db.get_state(conn, key)
    if cached:
        return cached
    for lb in client.list_labels():
        if lb["name"] == name:
            db.set_state(conn, key, lb["id"])
            return lb["id"]
    created = client.create_label(name)
    db.set_state(conn, key, created["id"])
    return created["id"]


def _label_thread(conn, thread_id: str, add: list[str] = (), remove: list[str] = ()) -> str:
    """Apply Signal/ labels; returns a note for the log line."""
    client = _client()
    if client is None:
        return " (demo: no Gmail call made)"
    add_ids = [_ensure_label(conn, client, n) for n in add]
    remove_ids = [i for n in remove if (i := db.get_state(conn, f"label:{n}"))]
    client.modify_thread_labels(thread_id, add=add_ids, remove=remove_ids)
    return ""


# ---------- action log ----------

def _log(conn, kind: str, target: str, description: str, gmail_effect: str,
         undo_steps: list[dict] | None) -> dict:
    cur = conn.execute(
        """INSERT INTO actions (at, kind, target, description, gmail_effect, undo_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (datetime.now(timezone.utc).isoformat(), kind, target, description,
         gmail_effect, json.dumps(undo_steps) if undo_steps else None),
    )
    return {"action_id": cur.lastrowid, "description": description,
            "undo_seconds": config.UNDO_SECONDS if undo_steps else 0}


def recent_log(conn, limit: int = 8) -> list[dict]:
    rows = conn.execute(
        """SELECT id, at, kind, description, gmail_effect, undone_at FROM actions
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _thread_snapshot(conn, thread_id: str) -> dict:
    t = conn.execute(
        "SELECT state, snooze_until, returned, last_from_me FROM threads "
        "WHERE gmail_thread_id = ?",
        (thread_id,),
    ).fetchone()
    return dict(t) if t else {}


def _thread_name(conn, thread_id: str) -> str:
    row = conn.execute(
        """SELECT COALESCE(NULLIF(m.from_name, ''), s.display_name, t.counterpart) AS n
           FROM threads t
           LEFT JOIN messages m ON m.thread_id = t.gmail_thread_id AND m.is_from_me = 0
           LEFT JOIN senders s ON s.address = t.counterpart
           WHERE t.gmail_thread_id = ? ORDER BY m.received_at DESC LIMIT 1""",
        (thread_id,),
    ).fetchone()
    return (row and row["n"]) or "the sender"


# ---------- actions ----------

def mark_done(conn, thread_id: str) -> dict:
    prev = _thread_snapshot(conn, thread_id)
    name = _thread_name(conn, thread_id)
    conn.execute("UPDATE threads SET state = 'done' WHERE gmail_thread_id = ?", (thread_id,))
    note = _label_thread(conn, thread_id, add=["Signal/Done"])
    return _log(
        conn, "done", thread_id,
        f"{name}'s thread labeled Signal/Done. Still in Gmail, no reply sent.{note}",
        "Added label Signal/Done. Nothing else.",
        [{"op": "restore_thread", "thread_id": thread_id, "prev": prev},
         {"op": "unlabel", "thread_id": thread_id, "labels": ["Signal/Done"]}],
    )


def snooze(conn, thread_id: str, until_iso: str, until_label: str) -> dict:
    prev = _thread_snapshot(conn, thread_id)
    name = _thread_name(conn, thread_id)
    conn.execute(
        "UPDATE threads SET state = 'snoozed', snooze_until = ?, returned = 0 "
        "WHERE gmail_thread_id = ?",
        (until_iso, thread_id),
    )
    note = _label_thread(conn, thread_id, add=["Signal/Snoozed"])
    return _log(
        conn, "snooze", thread_id,
        f"{name}'s thread snoozed. It returns {until_label}, marked 'returned'.{note}",
        "Added label Signal/Snoozed; it comes off when the thread returns.",
        [{"op": "restore_thread", "thread_id": thread_id, "prev": prev},
         {"op": "unlabel", "thread_id": thread_id, "labels": ["Signal/Snoozed"]}],
    )


def wake_snoozed(conn) -> int:
    """Poller hook: bring back snoozed threads whose time has come."""
    due = conn.execute(
        "SELECT gmail_thread_id FROM threads WHERE state = 'snoozed' AND snooze_until <= ?",
        (datetime.now(timezone.utc).isoformat(),),
    ).fetchall()
    for t in due:
        conn.execute(
            "UPDATE threads SET state = 'open', snooze_until = NULL, returned = 1 "
            "WHERE gmail_thread_id = ?",
            (t["gmail_thread_id"],),
        )
        _label_thread(conn, t["gmail_thread_id"], remove=["Signal/Snoozed"])
    return len(due)


URL_RE = re.compile(r"https?://[^\s<>\")\]]+")


def confirm(conn, message_id: str, answer: str) -> dict:
    """Yes / No / That-was-me on a Needs you row. The confirm URL opens in a
    new tab for the user (server-side fetch only with explicit per-sender
    opt-in, which nobody has yet - SPEC 4)."""
    m = conn.execute(
        "SELECT * FROM messages WHERE gmail_message_id = ?", (message_id,)
    ).fetchone()
    if not m:
        raise ValueError("unknown message")
    ex = db.loads(m["extracted_json"], {})
    url = ex.get("action_url")
    if not url:
        found = URL_RE.search(m["body_text"] or "")
        url = found.group(0) if found else None
    service = m["from_name"] or m["from_address"].split("@")[-1]
    prev = _thread_snapshot(conn, m["thread_id"])
    conn.execute("UPDATE threads SET state = 'done' WHERE gmail_thread_id = ?",
                 (m["thread_id"],))
    note = _label_thread(conn, m["thread_id"], add=["Signal/Done"])
    wording = {
        "yes": f"Answered Yes for {service}",
        "no": f"Answered No for {service} - expect their follow-up in Needs you",
        "ack": f"Acknowledged {service}'s alert",
    }.get(answer, f"Answered {answer} for {service}")
    link_note = (
        " Their confirm link opens in a new tab; the answer happens there."
        if url else " No confirm link found in the email."
    )
    result = _log(
        conn, f"confirm_{answer}", message_id,
        f"{wording}. Alert labeled Signal/Done.{link_note}{note}",
        "Added label Signal/Done. The confirm URL was opened in your browser, "
        "not fetched by the app.",
        [{"op": "restore_thread", "thread_id": m["thread_id"], "prev": prev},
         {"op": "unlabel", "thread_id": m["thread_id"], "labels": ["Signal/Done"]}],
    )
    result["open_url"] = url
    return result


TIER_ROUTES = {"person": "people", "service": "heads_up", "promo": "promotions",
               "feed": "lane:Reads", "notes": "notebook", "bot": "lane:Markets"}


def set_rule(conn, address: str, tier: str, route: str | None = None,
             subrules: dict | None = None) -> dict:
    if tier not in config.TIERS:
        raise ValueError(f"unknown tier {tier}")
    s = conn.execute("SELECT * FROM senders WHERE address = ?", (address,)).fetchone()
    if not s:
        raise ValueError("unknown sender")
    prev_sub = conn.execute(
        "SELECT email_type, route FROM sender_subrules WHERE sender_address = ?",
        (address,),
    ).fetchall()
    prev = {"tier": s["tier"], "route": s["route"],
            "confirmed": s["confirmed_by_user"],
            "subrules": {r["email_type"]: r["route"] for r in prev_sub}}
    route = route or TIER_ROUTES.get(tier, "heads_up")
    conn.execute(
        "UPDATE senders SET tier = ?, route = ?, confirmed_by_user = 1 WHERE address = ?",
        (tier, route, address),
    )
    conn.execute("DELETE FROM sender_subrules WHERE sender_address = ?", (address,))
    for etype, r in (subrules or {}).items():
        conn.execute("INSERT INTO sender_subrules VALUES (?, ?, ?)", (address, etype, r))
    if route.startswith("lane:"):
        lane = conn.execute("SELECT id FROM lanes WHERE name = ?", (route[5:],)).fetchone()
        if lane:
            conn.execute("INSERT OR IGNORE INTO lane_sources VALUES (?, ?)",
                         (lane["id"], address))

    # Label the sender's existing threads Signal/<Tier> (SPEC 4).
    label = f"Signal/{tier.capitalize()}"
    threads = [r["gmail_thread_id"] for r in conn.execute(
        "SELECT gmail_thread_id FROM threads WHERE counterpart = ?", (address,))]
    note = ""
    for tid in threads:
        note = _label_thread(conn, tid, add=[label])
    name = s["display_name"] or address
    return _log(
        conn, "rule", address,
        f"Rule saved: {name} is a {tier.capitalize()}. Future mail routes "
        f"without asking.{note}",
        f"Added label {label} to {len(threads)} existing thread"
        f"{'s' if len(threads) != 1 else ''} from {address}.",
        [{"op": "restore_sender", "address": address, "prev": prev},
         {"op": "unlabel_many", "thread_ids": threads, "labels": [label]}],
    )


# ---------- reply / send with 12s hold ----------

def reply_recipients(conn, thread_id: str, reply_all: bool) -> dict:
    my = (db.get_state(conn, "my_address") or "").lower()
    last = conn.execute(
        """SELECT * FROM messages WHERE thread_id = ? AND is_from_me = 0
           ORDER BY received_at DESC LIMIT 1""",
        (thread_id,),
    ).fetchone()
    if not last:
        raise ValueError("no incoming message to reply to")
    to = [last["from_address"]]
    others = [
        a for a in db.loads(last["to_addresses"], []) + db.loads(last["cc_addresses"], [])
        if a and a != my and a != last["from_address"]
    ]
    others = sorted(set(others))
    return {
        "message": last,
        "to": to,
        "others": others,
        "recipients": to + others if reply_all else to,
    }


def queue_send(conn, thread_id: str, body: str, reply_all: bool) -> dict:
    info = reply_recipients(conn, thread_id, reply_all)
    now = datetime.now(timezone.utc)
    send_at = now + timedelta(seconds=config.UNDO_SECONDS)
    cur = conn.execute(
        """INSERT INTO pending_sends (thread_id, body, reply_all, to_json, created_at, send_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (thread_id, body, int(reply_all), json.dumps(info["recipients"]),
         now.isoformat(), send_at.isoformat()),
    )
    send_id = cur.lastrowid
    # The thread moves to Waiting on them optimistically; undo restores it.
    prev = _thread_snapshot(conn, thread_id)
    conn.execute(
        "UPDATE threads SET last_from_me = 1 WHERE gmail_thread_id = ?", (thread_id,)
    )
    name = _thread_name(conn, thread_id)
    n = len(info["recipients"])
    who = name if n == 1 else f"{name} and {n - 1} other{'s' if n > 2 else ''}"
    result = _log(
        conn, "send", thread_id,
        f"Reply to {who} held for {config.UNDO_SECONDS}s, then sent through "
        f"Gmail as you, in the same thread. Sent folder will have it.",
        "users.messages.send in the same thread, from your address, with "
        "quoted history.",
        [{"op": "cancel_send", "send_id": send_id},
         {"op": "restore_thread", "thread_id": thread_id, "prev": prev}],
    )
    result.update({"send_id": send_id, "sent_to": who,
                   "undo_seconds": config.UNDO_SECONDS})
    return result


def cancel_send(conn, send_id: int) -> bool:
    cur = conn.execute(
        """UPDATE pending_sends SET canceled_at = ?
           WHERE id = ? AND sent_at IS NULL AND canceled_at IS NULL""",
        (datetime.now(timezone.utc).isoformat(), send_id),
    )
    return cur.rowcount > 0


def _build_mime(conn, ps, recipients: list[str]) -> bytes:
    my = db.get_state(conn, "my_address") or ""
    last = conn.execute(
        """SELECT * FROM messages WHERE thread_id = ? AND is_from_me = 0
           ORDER BY received_at DESC LIMIT 1""",
        (ps["thread_id"],),
    ).fetchone()
    msg = EmailMessage()
    msg["From"] = my
    msg["To"] = ", ".join(recipients)
    subject = (last and last["subject"]) or ""
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if last and last["rfc822_message_id"]:
        msg["In-Reply-To"] = last["rfc822_message_id"]
        msg["References"] = last["rfc822_message_id"]
    quoted = ""
    if last:
        when = datetime.fromisoformat(last["received_at"]).strftime("%a, %b %d, %Y at %I:%M %p")
        quoted_body = "\n".join(f"> {l}" for l in (last["body_text"] or "").splitlines())
        writer = formataddr((last["from_name"], last["from_address"]))
        quoted = f"\n\nOn {when} {writer} wrote:\n{quoted_body}"
    msg.set_content(ps["body"] + quoted)
    return msg.as_bytes()


def execute_due_sends(conn) -> int:
    """Runs every couple of seconds. Sends anything whose hold expired."""
    due = conn.execute(
        "SELECT * FROM pending_sends WHERE sent_at IS NULL AND canceled_at IS NULL "
        "AND send_at <= ?",
        (datetime.now(timezone.utc).isoformat(),),
    ).fetchall()
    sent = 0
    for ps in due:
        recipients = db.loads(ps["to_json"], [])
        client = _client()
        now = datetime.now(timezone.utc).isoformat()
        try:
            if client is not None:
                resp = client.send_message(_build_mime(conn, ps, recipients),
                                           thread_id=ps["thread_id"])
                new_id = resp.get("id", f"sent-{ps['id']}")
            else:
                new_id = f"demo-sent-{ps['id']}"
            my = db.get_state(conn, "my_address") or ""
            conn.execute(
                """INSERT OR IGNORE INTO messages
                   (gmail_message_id, thread_id, from_address, from_name, to_addresses,
                    cc_addresses, subject, snippet, body_text, received_at, is_from_me)
                   VALUES (?, ?, ?, 'You', ?, '[]', ?, ?, ?, ?, 1)""",
                (new_id, ps["thread_id"], my, ps["to_json"], "Re:",
                 ps["body"][:110], ps["body"], now),
            )
            conn.execute("UPDATE pending_sends SET sent_at = ? WHERE id = ?",
                         (now, ps["id"]))
            suffix = " (demo: no Gmail call made)" if client is None else ""
            conn.execute(
                "UPDATE actions SET description = ? WHERE kind = 'send' AND target = ? "
                "AND id = (SELECT MAX(id) FROM actions WHERE kind = 'send' AND target = ?)",
                (f"Replied in the same thread, from {my}. Sent folder has it.{suffix}",
                 ps["thread_id"], ps["thread_id"]),
            )
            sent += 1
        except Exception as e:
            log.error("send %s failed: %s", ps["id"], e)
            conn.execute("UPDATE pending_sends SET error = ? WHERE id = ?",
                         (str(e), ps["id"]))
    return sent


# ---------- unsubscribe (Phase 3, SPEC 4) ----------

UNSUB_URL_RE = re.compile(r"<(https?://[^>]+)>")
UNSUB_MAILTO_RE = re.compile(r"<mailto:([^>?]+)(\?[^>]*)?>")


def parse_list_unsubscribe(header: str, post_header: str) -> dict:
    """RFC 2369 header targets. One-click POST only when RFC 8058's
    List-Unsubscribe-Post: List-Unsubscribe=One-Click is present."""
    url = UNSUB_URL_RE.search(header or "")
    mailto = UNSUB_MAILTO_RE.search(header or "")
    one_click = bool(url) and "one-click" in (post_header or "").lower()
    return {
        "url": url.group(1) if url else None,
        "mailto": mailto.group(1) if mailto else None,
        "mailto_subject": (mailto.group(2) or "").replace("?subject=", "") if mailto else "",
        "one_click": one_click,
    }


def unsubscribe(conn, address: str) -> dict:
    """Uses the List-Unsubscribe header only - never links in the body."""
    s = conn.execute("SELECT * FROM senders WHERE address = ?", (address,)).fetchone()
    if not s:
        raise ValueError("unknown sender")
    m = conn.execute(
        """SELECT list_unsubscribe, list_unsubscribe_post FROM messages
           WHERE from_address = ? AND list_unsubscribe != ''
           ORDER BY received_at DESC LIMIT 1""",
        (address,),
    ).fetchone()
    target = parse_list_unsubscribe(
        m["list_unsubscribe"] if m else "", m["list_unsubscribe_post"] if m else ""
    )
    if target["one_click"]:
        method, how = "one_click", "one-click POST (RFC 8058)"
    elif target["mailto"]:
        method, how = "mailto", f"an unsubscribe email to {target['mailto']}"
    elif target["url"]:
        # A bare URL without the RFC 8058 header is a page for a human.
        method, how = "open", "their unsubscribe page, opened in a new tab"
    else:
        raise ValueError(
            "This sender has no List-Unsubscribe header, and the app never "
            "clicks unsubscribe links inside email bodies."
        )

    prev = {"tier": s["tier"], "route": s["route"], "confirmed": s["confirmed_by_user"],
            "subrules": {}}
    conn.execute(
        "UPDATE senders SET tier = 'promo', route = 'muted', confirmed_by_user = 1 "
        "WHERE address = ?",
        (address,),
    )
    name = s["display_name"] or address
    undo_steps = [{"op": "restore_sender", "address": address, "prev": prev}]
    op_id = None
    result_url = None
    if method in ("one_click", "mailto"):
        now = datetime.now(timezone.utc)
        cur = conn.execute(
            "INSERT INTO pending_ops (kind, payload_json, created_at, run_at) "
            "VALUES ('unsubscribe', ?, ?, ?)",
            (json.dumps({"address": address, **target, "method": method}),
             now.isoformat(),
             (now + timedelta(seconds=config.UNDO_SECONDS)).isoformat()),
        )
        op_id = cur.lastrowid
        undo_steps.insert(0, {"op": "cancel_op", "op_id": op_id})
        effect = (f"Held {config.UNDO_SECONDS}s, then {how}. "
                  "Future mail from them files to Promotions, muted.")
    else:
        result_url = target["url"]
        effect = f"{how.capitalize()}. Future mail files to Promotions, muted."
    res = _log(
        conn, "unsubscribe", address,
        f"Unsubscribing from {name} via {how}. They drop to Promotions, muted.",
        effect, undo_steps,
    )
    res["open_url"] = result_url
    return res


def execute_due_ops(conn) -> int:
    due = conn.execute(
        "SELECT * FROM pending_ops WHERE done_at IS NULL AND canceled_at IS NULL "
        "AND run_at <= ?",
        (datetime.now(timezone.utc).isoformat(),),
    ).fetchall()
    ran = 0
    for op in due:
        payload = db.loads(op["payload_json"], {})
        now = datetime.now(timezone.utc).isoformat()
        try:
            demo = config.DEMO or not config.TOKEN_PATH.exists()
            if op["kind"] == "unsubscribe":
                if payload.get("method") == "one_click" and not config.DEMO:
                    import httpx
                    r = httpx.post(payload["url"],
                                   content="List-Unsubscribe=One-Click",
                                   headers={"Content-Type": "application/x-www-form-urlencoded"},
                                   timeout=15, follow_redirects=True)
                    r.raise_for_status()
                elif payload.get("method") == "mailto" and not demo:
                    client = _client()
                    my = db.get_state(conn, "my_address") or ""
                    msg = EmailMessage()
                    msg["From"] = my
                    msg["To"] = payload["mailto"]
                    msg["Subject"] = payload.get("mailto_subject") or "unsubscribe"
                    msg.set_content("unsubscribe")
                    client.send_message(msg.as_bytes())
            conn.execute("UPDATE pending_ops SET done_at = ? WHERE id = ?",
                         (now, op["id"]))
            ran += 1
        except Exception as e:
            log.error("op %s failed: %s", op["id"], e)
            conn.execute("UPDATE pending_ops SET error = ?, done_at = ? WHERE id = ?",
                         (str(e), now, op["id"]))
    return ran


def cancel_op(conn, op_id: int) -> bool:
    cur = conn.execute(
        "UPDATE pending_ops SET canceled_at = ? WHERE id = ? AND done_at IS NULL "
        "AND canceled_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), op_id),
    )
    return cur.rowcount > 0


# ---------- undo ----------

def undo(conn, action_id: int) -> dict:
    row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if not row:
        raise ValueError("unknown action")
    if row["undone_at"]:
        return {"ok": True, "note": "already undone"}
    steps = db.loads(row["undo_json"], [])
    if not steps:
        raise ValueError("this action can't be undone")
    for step in steps:
        op = step["op"]
        if op == "restore_thread":
            p = step["prev"]
            if p:
                conn.execute(
                    "UPDATE threads SET state = ?, snooze_until = ?, returned = ?, "
                    "last_from_me = COALESCE(?, last_from_me) "
                    "WHERE gmail_thread_id = ?",
                    (p.get("state", "open"), p.get("snooze_until"),
                     p.get("returned", 0), p.get("last_from_me"),
                     step["thread_id"]),
                )
        elif op in ("unlabel", "unlabel_many"):
            tids = step.get("thread_ids") or [step.get("thread_id")]
            for tid in tids:
                if tid:
                    _label_thread(conn, tid, remove=step["labels"])
        elif op == "restore_sender":
            p = step["prev"]
            conn.execute(
                "UPDATE senders SET tier = ?, route = ?, confirmed_by_user = ? "
                "WHERE address = ?",
                (p["tier"], p["route"], p["confirmed"], step["address"]),
            )
            conn.execute("DELETE FROM sender_subrules WHERE sender_address = ?",
                         (step["address"],))
            for etype, r in (p.get("subrules") or {}).items():
                conn.execute("INSERT INTO sender_subrules VALUES (?, ?, ?)",
                             (step["address"], etype, r))
        elif op == "cancel_send":
            if not cancel_send(conn, step["send_id"]):
                raise ValueError("too late - the reply already went out")
        elif op == "cancel_op":
            if not cancel_op(conn, step["op_id"]):
                raise ValueError("too late - it already went through")
    conn.execute("UPDATE actions SET undone_at = ? WHERE id = ?",
                 (datetime.now(timezone.utc).isoformat(), action_id))
    return {"ok": True}
