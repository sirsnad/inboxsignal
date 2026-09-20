"""Sender rules and message classification (SPEC 2, 2.1, 2.2).

run_pipeline is called after every sync pass. Order:
  1. ensure a sender row per distinct address
  2. behavioral heuristics (they beat the model: a reply makes a Person)
  3. LLM tier guess for the rest (unconfirmed guesses feed "New senders")
  4. email-type classification + light extraction for service/split mail
  5. ask extraction for open People threads
"""

import json
import logging
import re
from datetime import datetime, timezone

from . import config, db, llm

log = logging.getLogger("signal.rules")

TIER_DEFAULT_ROUTE = {
    "person": "people",
    "service": "heads_up",
    "feed": "lane:Reads",
    "promo": "promotions",
    "bot": "lane:Markets",
    "notes": "notebook",
    "split": "split",
}

GREETING_RE = re.compile(r"^\s*(hi|hey|hello|dear)\b", re.IGNORECASE)


def _norm_subject(subject: str) -> str:
    """Collapse dates/numbers so a daily automated subject maps to one pattern."""
    s = re.sub(r"(?i)^(re|fwd?):\s*", "", subject or "")
    s = re.sub(r"[\d/:.,-]+", "#", s)
    return s.strip().lower()


def _bot_lane(subject: str) -> str:
    s = (subject or "").lower()
    if any(w in s for w in ("trade", "trader", "market", "portfolio", "stock")):
        return "lane:Markets"
    if any(w in s for w in ("job", "role", "recruit", "opening")):
        return "lane:Jobs"
    return "lane:Markets"


def sender_stats(conn, address: str) -> dict:
    """Evidence over the stored window: volume, opens, replies."""
    row = conn.execute(
        """SELECT COUNT(*) AS n, SUM(opened_at IS NOT NULL) AS opened
           FROM messages WHERE from_address = ? AND is_from_me = 0""",
        (address,),
    ).fetchone()
    replied = conn.execute(
        """SELECT COUNT(DISTINCT t.gmail_thread_id) AS n
           FROM threads t JOIN messages m ON m.thread_id = t.gmail_thread_id
           WHERE t.counterpart = ? AND m.is_from_me = 1""",
        (address,),
    ).fetchone()
    return {
        "messages": row["n"] or 0,
        "opened": row["opened"] or 0,
        "replied_threads": replied["n"] or 0,
    }


def _first_message(conn, address: str):
    return conn.execute(
        """SELECT * FROM messages WHERE from_address = ? AND is_from_me = 0
           ORDER BY received_at LIMIT 1""",
        (address,),
    ).fetchone()


def _save_sender(conn, address, name, tier, route, evidence, confirmed=0, subrules=None):
    domain = address.split("@")[-1] if "@" in address else ""
    conn.execute(
        """INSERT INTO senders (address, display_name, domain, tier, route,
               evidence_json, created_at, confirmed_by_user)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(address) DO UPDATE SET
               display_name = COALESCE(NULLIF(excluded.display_name, ''), senders.display_name),
               tier = excluded.tier, route = excluded.route,
               evidence_json = excluded.evidence_json,
               confirmed_by_user = MAX(senders.confirmed_by_user, excluded.confirmed_by_user)""",
        (address, name, domain, tier, route, json.dumps(evidence),
         datetime.now(timezone.utc).isoformat(), confirmed),
    )
    for etype, sub_route in (subrules or {}).items():
        conn.execute(
            """INSERT INTO sender_subrules (sender_address, email_type, route)
               VALUES (?, ?, ?)
               ON CONFLICT(sender_address, email_type) DO UPDATE SET route = excluded.route""",
            (address, etype, sub_route),
        )
    if route and route.startswith("lane:"):
        lane = conn.execute(
            "SELECT id FROM lanes WHERE name = ?", (route[5:],)
        ).fetchone()
        if lane:
            conn.execute(
                "INSERT OR IGNORE INTO lane_sources (lane_id, sender_address) VALUES (?, ?)",
                (lane["id"], address),
            )


def classify_senders(conn) -> None:
    my_address = (db.get_state(conn, "my_address") or "").lower()
    unknown = conn.execute(
        """SELECT DISTINCT m.from_address AS address,
                  MAX(m.from_name) AS name
           FROM messages m LEFT JOIN senders s ON s.address = m.from_address
           WHERE s.address IS NULL OR s.tier IS NULL
           GROUP BY m.from_address"""
    ).fetchall()

    for row in unknown:
        address, name = row["address"], row["name"] or ""
        if not address:
            continue

        # -- self mail: Bot when the subject pattern repeats without a
        # greeting, Notes otherwise (SPEC 2.1 step 2).
        if address == my_address:
            patterns = conn.execute(
                """SELECT subject, COUNT(*) AS n FROM messages
                   WHERE from_address = ? GROUP BY subject""",
                (address,),
            ).fetchall()
            bot_subjects = {}
            grouped: dict[str, int] = {}
            for p in patterns:
                grouped[_norm_subject(p["subject"])] = (
                    grouped.get(_norm_subject(p["subject"]), 0) + p["n"]
                )
            for p in patterns:
                norm = _norm_subject(p["subject"])
                body_row = conn.execute(
                    "SELECT body_text FROM messages WHERE from_address = ? AND subject = ? LIMIT 1",
                    (address, p["subject"]),
                ).fetchone()
                no_greeting = not GREETING_RE.match(body_row["body_text"] or "")
                if grouped.get(norm, 0) >= 3 and no_greeting:
                    bot_subjects[norm] = _bot_lane(p["subject"])
            evidence = {"kind": "self", "bot_subjects": bot_subjects}
            _save_sender(conn, address, name or "You", "notes", "notebook",
                         evidence, confirmed=1)
            continue

        stats = sender_stats(conn, address)

        # -- a reply makes a Person, permanently (SPEC 2.1 step 2).
        if stats["replied_threads"] > 0:
            evidence = {**stats, "why": "you replied"}
            _save_sender(conn, address, name, "person", "people", evidence, confirmed=1)
            continue

        first = _first_message(conn, address)
        guess = _llm_guess(name, address, first) if first else None
        if guess is None:
            # No API key or model failure: leave a low-confidence service
            # guess; it lands in New senders for the user to sort.
            guess = {"tier": "service", "route": "heads_up", "subrules": {},
                     "confidence": 0.0, "reason": "not classified yet"}

        tier, route = guess["tier"], guess.get("route") or TIER_DEFAULT_ROUTE.get(guess["tier"], "heads_up")

        # -- ignored 30 days drops to Promo + unsubscribe suggestion
        if tier in ("feed", "promo") and stats["messages"] >= 5 and stats["opened"] == 0:
            tier, route = "promo", "promotions"
            guess["reason"] = f"opened 0 of {stats['messages']} in 30 days"
            guess["unsub_suggest"] = True

        evidence = {**stats, "why": guess.get("reason", ""),
                    "confidence": guess.get("confidence", 0),
                    "unsub_suggest": guess.get("unsub_suggest", False)}
        _save_sender(conn, address, name, tier, route, evidence,
                     confirmed=0, subrules=guess.get("subrules") or {})


def _llm_guess(name: str, address: str, first) -> dict | None:
    try:
        payload = {
            "address": address,
            "display_name": name,
            "domain": address.split("@")[-1],
            "has_list_unsubscribe": bool(first["list_unsubscribe"]),
            "first_subject": first["subject"],
            "first_body": (first["body_text"] or "")[:500],
        }
        out = llm.complete_json(
            "classify", config.load_prompt("sender_tier"), json.dumps(payload)
        )
        if out.get("tier") in config.TIERS:
            return out
        log.warning("bad tier %r for %s", out.get("tier"), address)
    except Exception as e:  # model/network/key failure must not break sync
        log.warning("sender guess failed for %s: %s", address, e)
    return None


def classify_message_types(conn) -> None:
    rows = conn.execute(
        """SELECT m.gmail_message_id, m.subject, m.body_text, m.from_name
           FROM messages m JOIN senders s ON s.address = m.from_address
           WHERE m.email_type IS NULL AND m.is_from_me = 0
             AND s.tier IN ('service', 'split')"""
    ).fetchall()
    for m in rows:
        result = None
        try:
            out = llm.complete_json(
                "classify",
                config.load_prompt("email_type"),
                json.dumps({"subject": m["subject"],
                            "body": (m["body_text"] or "")[:500]}),
            )
            if out.get("email_type") in config.EMAIL_TYPES:
                result = out
        except Exception as e:
            log.warning("type classification failed for %s: %s", m["gmail_message_id"], e)
        etype = (result or {}).get("email_type", "other")
        conn.execute(
            "UPDATE messages SET email_type = ?, extracted_json = ? WHERE gmail_message_id = ?",
            (etype, json.dumps(result or {}), m["gmail_message_id"]),
        )
        _materialize(conn, m["gmail_message_id"], m["from_name"], etype, result or {})


def _materialize(conn, message_id: str, from_name: str, etype: str, ex: dict) -> None:
    """Money and tracking rows from the classification's extracted fields."""
    if etype in ("receipt", "statement"):
        amount = ex.get("amount")
        cents = None
        if amount:
            try:
                cents = int(round(float(str(amount).replace(",", "").replace("$", "")) * 100))
            except ValueError:
                cents = None
        conn.execute(
            """INSERT INTO money (message_id, merchant, amount_cents, source, tag)
               SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS
               (SELECT 1 FROM money WHERE message_id = ?)""",
            (message_id, ex.get("merchant") or from_name, cents,
             ex.get("source"), ex.get("tag"), message_id),
        )
    elif etype == "tracking":
        delivered = datetime.now(timezone.utc).isoformat() if ex.get("delivered") else None
        conn.execute(
            """INSERT INTO tracking (message_id, carrier, status, eta, delivered_at)
               SELECT ?, ?, ?, ?, ? WHERE NOT EXISTS
               (SELECT 1 FROM tracking WHERE message_id = ?)""",
            (message_id, ex.get("carrier"), ex.get("status"), ex.get("eta"),
             delivered, message_id),
        )


def extract_asks(conn) -> None:
    """Fill ask_summary for open People threads (SPEC 3.1 People, 3.4)."""
    rows = conn.execute(
        """SELECT t.gmail_thread_id, t.counterpart FROM threads t
           JOIN senders s ON s.address = t.counterpart
           WHERE s.tier = 'person' AND t.state IN ('open', 'following')
             AND t.ask_summary IS NULL AND t.last_from_me = 0"""
    ).fetchall()
    for t in rows:
        last = conn.execute(
            """SELECT * FROM messages WHERE thread_id = ? AND is_from_me = 0
               ORDER BY received_at DESC LIMIT 1""",
            (t["gmail_thread_id"],),
        ).fetchone()
        if not last:
            continue
        sent_context = conn.execute(
            """SELECT subject, body_text, received_at FROM messages
               WHERE is_from_me = 1 AND (thread_id = ? OR ? IN
                   (SELECT value FROM json_each(to_addresses)))
               ORDER BY received_at DESC LIMIT 5""",
            (t["gmail_thread_id"], t["counterpart"]),
        ).fetchall()
        summary = None
        try:
            summary = llm.complete_json(
                "extract",
                config.load_prompt("extract_ask"),
                json.dumps({
                    "from": {"name": last["from_name"], "address": last["from_address"]},
                    "subject": last["subject"],
                    "date": last["received_at"],
                    "to": db.loads(last["to_addresses"], []),
                    "cc": db.loads(last["cc_addresses"], []),
                    "body": (last["body_text"] or "")[:4000],
                    "my_recent_sent": [
                        {"subject": s["subject"], "date": s["received_at"],
                         "body": (s["body_text"] or "")[:500]}
                        for s in sent_context
                    ],
                }),
            )
        except Exception as e:
            log.warning("ask extraction failed for %s: %s", t["gmail_thread_id"], e)
        if summary is None:
            summary = {"one_line": (last["snippet"] or "")[:90], "ask": "",
                       "age_note": "", "related": ""}
        conn.execute(
            "UPDATE threads SET ask_summary = ? WHERE gmail_thread_id = ?",
            (json.dumps(summary), t["gmail_thread_id"]),
        )


def run_pipeline(conn) -> None:
    from . import digests
    classify_senders(conn)
    conn.commit()
    classify_message_types(conn)
    conn.commit()
    extract_asks(conn)
    conn.commit()
    digests.build_today(conn)
    conn.commit()
