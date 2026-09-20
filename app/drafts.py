"""Reply and nudge starters in the owner's voice (SPEC 6)."""

import json
import logging

from . import config, db, llm

log = logging.getLogger("signal.drafts")


def _first_name(name: str, address: str) -> str:
    n = (name or "").strip().split(" ")[0]
    return n or address.split("@")[0]


def make_starter(conn, thread_id: str, mode: str = "reply") -> dict:
    t = conn.execute(
        "SELECT * FROM threads WHERE gmail_thread_id = ?", (thread_id,)
    ).fetchone()
    if not t:
        raise ValueError("unknown thread")
    last_in = conn.execute(
        """SELECT * FROM messages WHERE thread_id = ? AND is_from_me = 0
           ORDER BY received_at DESC LIMIT 1""",
        (thread_id,),
    ).fetchone()
    last_mine = conn.execute(
        """SELECT * FROM messages WHERE thread_id = ? AND is_from_me = 1
           ORDER BY received_at DESC LIMIT 1""",
        (thread_id,),
    ).fetchone()
    who = last_in or last_mine
    first = _first_name(
        (last_in and last_in["from_name"]) or "", t["counterpart"] or ""
    )
    ask = db.loads(t["ask_summary"], {})

    draft = None
    try:
        out = llm.complete_json(
            "draft",
            config.load_prompt("reply_starter"),
            json.dumps({
                "mode": mode,
                "first_name": first,
                "subject": who["subject"] if who else t["subject"],
                "the_ask": ask.get("ask", ""),
                "incoming_message": (last_in["body_text"] or "")[:2000] if last_in else "",
                "what_i_sent_last": (last_mine["body_text"] or "")[:1000] if last_mine else "",
            }),
        )
        draft = out.get("draft")
    except Exception as e:
        log.warning("starter draft failed for %s: %s", thread_id, e)
    if not draft:
        if mode == "nudge":
            draft = (f"Hi {first} - just checking in on this one. Any word?\n\nSandy")
        else:
            draft = f"Hi {first} - \n\nSandy"
    return {"draft": draft, "first_name": first}
