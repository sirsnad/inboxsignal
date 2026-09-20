"""Section admission for the Today screen (SPEC 3.1).

Every section has an admission test; failing it means not appearing. All
functions are read-only over the DB so the payload is always consistent
with the rules.
"""

from datetime import datetime, timedelta, timezone

from . import config, db


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_days_ago(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


def _age_label(days: int | None, fallback: str = "") -> str:
    if days is None:
        return fallback
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day"
    return f"{days} days"


def _initials(name: str) -> str:
    parts = [p for p in (name or "").replace(".", " ").split() if p]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


def _route_for(conn, sender_row, email_type: str | None) -> str:
    """Resolve a message's route from its sender rule (+ subrules for split)."""
    if sender_row is None:
        return "heads_up"
    if sender_row["tier"] == "split":
        sub = conn.execute(
            "SELECT route FROM sender_subrules WHERE sender_address = ? AND email_type = ?",
            (sender_row["address"], email_type),
        ).fetchone()
        if sub:
            return sub["route"]
    if sender_row["tier"] == "service":
        # Service mail routes by email type (SPEC 2 table).
        by_type = {
            "alert": "needs_you", "verification": "needs_you",
            "receipt": "money", "statement": "money",
            "tracking": "on_its_way", "notice": "heads_up",
        }
        return by_type.get(email_type or "", sender_row["route"] or "heads_up")
    return sender_row["route"] or "heads_up"


def _sender(conn, address: str):
    return conn.execute("SELECT * FROM senders WHERE address = ?", (address,)).fetchone()


def people(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT t.*, s.display_name, s.address FROM threads t
           JOIN senders s ON s.address = t.counterpart
           WHERE s.tier = 'person' AND t.state IN ('open', 'following')
             AND t.last_from_me = 0
           ORDER BY t.is_cc_only ASC, t.age_days DESC"""
    ).fetchall()
    out = []
    for t in rows:
        ask = db.loads(t["ask_summary"], {})
        last = conn.execute(
            """SELECT from_name, to_addresses, cc_addresses, snippet FROM messages
               WHERE thread_id = ? AND is_from_me = 0 ORDER BY received_at DESC LIMIT 1""",
            (t["gmail_thread_id"],),
        ).fetchone()
        name = (last and last["from_name"]) or t["display_name"] or t["counterpart"]
        following = bool(t["is_cc_only"])
        out.append({
            "thread_id": t["gmail_thread_id"],
            "name": name,
            "secondary": t["counterpart"].split("@")[-1],
            "initials": _initials(name),
            "summary": ask.get("one_line") or (last["snippet"][:90] if last else t["subject"]),
            "age_days": t["age_days"],
            # Age shows only when > 1 day; terracotta at 3+ (SPEC 3.1)
            "age_label": _age_label(t["age_days"]) if (t["age_days"] or 0) > 1 else "",
            "age_hot": (t["age_days"] or 0) >= 3,
            "following": following,
            "returned": bool(t["returned"]),
            "note": "cc'd · someone else is replying" if following else "",
        })
    return out


def needs_you(conn) -> list[dict]:
    """Test: if the user does nothing, something bad happens. Only alert and
    verification mail from services, or anything a rule routes to needs_you."""
    rows = conn.execute(
        """SELECT m.*, s.tier, s.route, s.address, s.display_name
           FROM messages m
           JOIN senders s ON s.address = m.from_address
           JOIN threads t ON t.gmail_thread_id = m.thread_id
           WHERE m.is_from_me = 0 AND m.email_type IN ('alert', 'verification')
             AND m.received_at > ? AND t.state NOT IN ('done', 'snoozed')
           ORDER BY m.received_at ASC""",
        (_iso_days_ago(config.BACKFILL_DAYS),),
    ).fetchall()
    out = []
    for m in rows:
        if _route_for(conn, m, m["email_type"]) != "needs_you":
            continue
        ex = db.loads(m["extracted_json"], {})
        age = max(0, (_now() - datetime.fromisoformat(m["received_at"])).days)
        service = m["from_name"] or m["display_name"] or m["from_address"].split("@")[-1]
        summary = ex.get("summary") or m["subject"] or ""
        # Rows carry their actions inline (SPEC 3.1): a question-shaped alert
        # gets Yes/No; an FYI-shaped one gets a single acknowledgement.
        yesno = any(w in summary.lower() for w in ("confirm", "charge", "was this you", "payment"))
        out.append({
            "message_id": m["gmail_message_id"],
            "thread_id": m["thread_id"],
            "title": f"{service}: {summary}",
            "detail": m["snippet"][:110],
            "age_days": age,
            "age_label": _age_label(age) if age >= 1 else "",
            "age_hot": age >= 3,
            "answer_mode": "yesno" if yesno else "ack",
        })
    return out


def waiting_on_them(conn) -> list[dict]:
    my = (db.get_state(conn, "my_address") or "").lower()
    rows = conn.execute(
        """SELECT t.*, s.display_name FROM threads t
           LEFT JOIN senders s ON s.address = t.counterpart
           WHERE t.last_from_me = 1 AND t.counterpart IS NOT NULL
             AND t.counterpart != ? AND t.state NOT IN ('done', 'snoozed')
           ORDER BY t.last_message_at ASC""",
        (my,),
    ).fetchall()
    out = []
    for t in rows:
        last_mine = conn.execute(
            """SELECT received_at, snippet, subject FROM messages
               WHERE thread_id = ? AND is_from_me = 1
               ORDER BY received_at DESC LIMIT 1""",
            (t["gmail_thread_id"],),
        ).fetchone()
        if not last_mine:
            continue
        days = max(0, (_now() - datetime.fromisoformat(last_mine["received_at"])).days)
        name_row = conn.execute(
            """SELECT from_name FROM messages WHERE thread_id = ? AND is_from_me = 0
               ORDER BY received_at DESC LIMIT 1""",
            (t["gmail_thread_id"],),
        ).fetchone()
        name = (name_row and name_row["from_name"]) or (t["display_name"] or t["counterpart"])
        out.append({
            "thread_id": t["gmail_thread_id"],
            "name": name,
            "secondary": (t["counterpart"] or "").split("@")[-1],
            "what": f"You sent: {last_mine['subject'] or last_mine['snippet'][:60]}",
            "days": days,
            "days_label": _age_label(days),
        })
    return out


def heads_up(conn) -> dict:
    """Notices worth a glance, no action. Auto-clears after 24h."""
    rows = conn.execute(
        """SELECT m.*, s.display_name, s.tier, s.route, s.address
           FROM messages m JOIN senders s ON s.address = m.from_address
           WHERE m.is_from_me = 0 AND m.email_type = 'notice' AND m.received_at > ?
           ORDER BY m.received_at DESC""",
        (_iso_days_ago(1),),
    ).fetchall()
    items = []
    for m in rows:
        if _route_for(conn, m, "notice") != "heads_up":
            continue
        ex = db.loads(m["extracted_json"], {})
        items.append({
            "message_id": m["gmail_message_id"],
            "summary": ex.get("summary") or m["subject"],
            "sender": m["display_name"] or m["from_name"],
        })
    return {
        "count": len(items),
        "line": " · ".join(i["summary"] for i in items[:4]),
        "items": items,
    }


def on_its_way(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT tr.*, m.from_name, m.subject, s.display_name
           FROM tracking tr
           JOIN messages m ON m.gmail_message_id = tr.message_id
           LEFT JOIN senders s ON s.address = m.from_address
           WHERE tr.delivered_at IS NULL OR tr.delivered_at > ?
           ORDER BY m.received_at DESC""",
        (_iso_days_ago(1),),
    ).fetchall()
    seen, out = set(), []
    for r in rows:
        key = (r["from_name"], r["carrier"])
        if key in seen:
            continue  # newest status per shipment source
        seen.add(key)
        title = r["display_name"] or r["from_name"] or "Package"
        if r["carrier"]:
            title = f"{title} · {r['carrier']}"
        out.append({
            "title": title,
            "status": r["status"] or ("Delivered" if r["delivered_at"] else "On its way"),
            "eta": r["eta"] or "",
        })
    return out


def money(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT mo.*, m.received_at FROM money mo
           JOIN messages m ON m.gmail_message_id = mo.message_id
           WHERE m.received_at > ? ORDER BY m.received_at DESC""",
        (_iso_days_ago(7),),
    ).fetchall()
    out = []
    for r in rows:
        amount = f"${r['amount_cents'] / 100:,.2f}" if r["amount_cents"] else "receipt"
        out.append({
            "merchant": r["merchant"],
            "detail": r["source"] or "",
            "tag": r["tag"] or "",
            "amount": amount,
        })
    return out


def new_senders(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT s.*, MIN(m.received_at) AS first_seen
           FROM senders s JOIN messages m ON m.from_address = s.address
           WHERE s.confirmed_by_user = 0 AND m.is_from_me = 0
           GROUP BY s.address HAVING first_seen > ?
           ORDER BY first_seen DESC LIMIT 6""",
        (_iso_days_ago(7),),
    ).fetchall()
    out = []
    for s in rows:
        ev = db.loads(s["evidence_json"], {})
        out.append({
            "address": s["address"],
            "name": s["display_name"] or s["address"],
            "hint": ev.get("why", ""),
            "guess": s["tier"],
        })
    return out


def lanes(conn) -> list[dict]:
    my = (db.get_state(conn, "my_address") or "").lower()
    self_row = _sender(conn, my) if my else None
    bot_subjects = (db.loads(self_row["evidence_json"], {}) if self_row else {}).get(
        "bot_subjects", {}
    )
    out = []
    for lane in conn.execute("SELECT * FROM lanes ORDER BY id").fetchall():
        name = lane["name"]
        sources = [
            r["sender_address"]
            for r in conn.execute(
                "SELECT sender_address FROM lane_sources WHERE lane_id = ?",
                (lane["id"],),
            )
        ]
        today_rows = []
        if sources:
            marks = ",".join("?" for _ in sources)
            today_rows = conn.execute(
                f"""SELECT subject, snippet FROM messages
                    WHERE from_address IN ({marks}) AND is_from_me = 0
                      AND received_at > ? ORDER BY received_at DESC""",
                (*sources, _iso_days_ago(1)),
            ).fetchall()
        if name == "Notebook" and my:
            # Mail the user sent themself by hand, newest three titles, no count.
            notes = conn.execute(
                """SELECT subject FROM messages WHERE from_address = ?
                   AND is_from_me = 1
                   AND ? IN (SELECT value FROM json_each(to_addresses))
                   ORDER BY received_at DESC LIMIT 6""",
                (my, my),
            ).fetchall()
            titles = [
                n["subject"] for n in notes
                if _norm(n["subject"]) not in bot_subjects
            ][:3]
            out.append({"name": name, "count": None, "summary": "",
                        "titles": titles, "no_count": True})
            continue
        no_count = name in config.NO_COUNT_LANES
        summary = today_rows[0]["subject"] if today_rows else ""
        count = len(today_rows)
        # Prefer the built digest: merged, deduped, counted by kind (SPEC 3.2).
        from . import digests as digests_mod
        snoozed = bool(lane["snoozed_until"] and lane["snoozed_until"] > _now().isoformat())
        items = [] if snoozed else digests_mod.today_digest(conn, lane["id"])
        if items:
            count = len(items)
            summary = digests_mod.summary_line(items)
        if snoozed:
            summary = f"snoozed{' · ' + lane['snooze_reason'] if lane['snooze_reason'] else ''}"
            count = 0
        titles = [r["subject"] for r in today_rows[:3]] if no_count else []
        out.append({
            "name": name,
            "count": None if no_count else count,
            "summary": summary,
            "titles": titles,
            "no_count": no_count,
            "snoozed": snoozed,
        })
    return out


def _norm(subject: str) -> str:
    from .rules import _norm_subject
    return _norm_subject(subject or "")


def promotions(conn) -> dict:
    count = conn.execute(
        """SELECT COUNT(*) AS n FROM messages m
           JOIN senders s ON s.address = m.from_address
           WHERE s.tier = 'promo' AND m.is_from_me = 0 AND m.received_at > ?""",
        (_iso_days_ago(1),),
    ).fetchone()["n"]
    suggestions = conn.execute(
        """SELECT display_name, address, evidence_json FROM senders
           WHERE tier = 'promo'"""
    ).fetchall()
    unsub = []
    for s in suggestions:
        ev = db.loads(s["evidence_json"], {})
        if ev.get("unsub_suggest") or (ev.get("messages", 0) >= 5 and ev.get("opened", 1) == 0):
            unsub.append(s["display_name"] or s["address"])
    return {"count_today": count, "digest_day": "Sunday", "unsub": unsub[:3]}


def counters(conn, sections: dict) -> dict:
    arrived = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE is_from_me = 0 AND received_at > ?",
        (_iso_days_ago(1),),
    ).fetchone()["n"]
    surfaced_today = sum(
        1 for p in sections["people"] if (p["age_days"] or 0) < 1
    ) + sum(1 for n in sections["needs_you"] if n["age_days"] < 1)
    open_count = (
        len([p for p in sections["people"] if not p["following"]])
        + len(sections["needs_you"])
    )
    return {
        "open": open_count,
        "waiting": len(sections["waiting"]),
        "arrived_today": arrived,
        "filed_today": max(0, arrived - surfaced_today),
    }


def recent_done(conn) -> list[dict]:
    """Checkmark lines under Needs you: what was handled in the last day."""
    rows = conn.execute(
        """SELECT description, at FROM actions
           WHERE (kind LIKE 'confirm_%' OR kind = 'done') AND undone_at IS NULL
             AND at > ? ORDER BY id DESC LIMIT 4""",
        (_iso_days_ago(1),),
    ).fetchall()
    out = []
    for r in rows:
        text = r["description"].split(".")[0]
        out.append({"line": text, "at": r["at"]})
    return out


def today(conn) -> dict:
    sections = {
        "people": people(conn),
        "needs_you": needs_you(conn),
        "waiting": waiting_on_them(conn),
        "heads_up": heads_up(conn),
        "on_its_way": on_its_way(conn),
        "money": money(conn),
        "new_senders": new_senders(conn),
        "lanes": lanes(conn),
        "promotions": promotions(conn),
        "recent_done": recent_done(conn),
    }
    local_now = datetime.now()
    return {
        "date": local_now.strftime("%A, %B %-d") if hasattr(local_now, "strftime") else "",
        "account": db.get_state(conn, "my_address") or "",
        "counters": counters(conn, sections),
        **sections,
    }
