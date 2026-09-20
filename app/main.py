"""FastAPI app: serves the UI and the read-only JSON API (Phase 1)."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import admission, config, db

log = logging.getLogger("signal.app")
STATIC = Path(__file__).parent / "static"


async def _poll_loop():
    from .gmail import sync
    while True:
        try:
            await asyncio.to_thread(sync.poll_once)
        except Exception as e:
            log.warning("poll failed: %s", e)
        await asyncio.sleep(config.POLL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init().close()
    task = None
    if not config.DEMO and config.TOKEN_PATH.exists():
        task = asyncio.create_task(_poll_loop())
    elif not config.DEMO:
        log.warning(
            "no token.json; run `python -m app.gmail.sync --backfill` first "
            "(or set SIGNAL_DEMO=1 for fixture data)"
        )
    yield
    if task:
        task.cancel()


app = FastAPI(title="Signal Inbox", lifespan=lifespan)


@app.get("/api/today")
def api_today():
    with db.session() as conn:
        return admission.today(conn)


@app.get("/api/thread/{thread_id}")
def api_thread(thread_id: str):
    with db.session() as conn:
        t = conn.execute(
            "SELECT * FROM threads WHERE gmail_thread_id = ?", (thread_id,)
        ).fetchone()
        if not t:
            raise HTTPException(404)
        last = conn.execute(
            """SELECT * FROM messages WHERE thread_id = ? AND is_from_me = 0
               ORDER BY received_at DESC LIMIT 1""",
            (thread_id,),
        ).fetchone()
        if not last:
            last = conn.execute(
                "SELECT * FROM messages WHERE thread_id = ? ORDER BY received_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        sender = conn.execute(
            "SELECT * FROM senders WHERE address = ?", (last["from_address"],)
        ).fetchone()

        evidence = ""
        tier = sender["tier"] if sender else "service"
        if sender and tier == "person":
            from .rules import sender_stats
            stats = sender_stats(conn, sender["address"])
            ev = db.loads(sender["evidence_json"], {})
            replied = stats["replied_threads"] or ev.get("replied_threads", 0)
            first = (last["from_name"] or sender["display_name"] or "").split(" ")[0]
            if replied:
                evidence = (
                    f"You've replied to {first or 'them'} in "
                    f"{replied} thread{'s' if replied != 1 else ''} this month"
                )
            elif ev.get("why"):
                evidence = ev["why"]

        recipients = db.loads(last["to_addresses"], []) + db.loads(last["cc_addresses"], [])
        n = len(recipients)
        recipients_summary = (
            "to you" if n <= 1 else f"to {n} people"
        )
        try:
            when = datetime.fromisoformat(last["received_at"]).strftime("%a, %b %-d, %-I:%M %p")
        except ValueError:
            when = last["received_at"]

        ask = db.loads(t["ask_summary"], {})
        return {
            "thread_id": thread_id,
            "tier": tier,
            "evidence": evidence,
            "subject": last["subject"] or t["subject"],
            "sender_name": last["from_name"] or (sender and sender["display_name"]) or last["from_address"],
            "sender_domain": last["from_address"].split("@")[-1],
            "initials": admission._initials(last["from_name"] or last["from_address"]),
            "recipients_summary": f"{recipients_summary} · {when}",
            "pulled_out": {
                "ask": ask.get("ask", ""),
                "age_note": ask.get("age_note", ""),
                "related": ask.get("related", ""),
            },
            "body": last["body_text"] or last["snippet"],
            # SPEC 3.4 item 5: footer states the Gmail side effect of the
            # primary action. Phase 1 has no actions yet.
            "footer": "Read-only mirror: nothing here changes Gmail. Actions arrive in Phase 2.",
        }


@app.get("/api/senders")
def api_senders():
    with db.session() as conn:
        rows = conn.execute(
            """SELECT s.*, COUNT(m.gmail_message_id) AS msg_count
               FROM senders s LEFT JOIN messages m
                 ON m.from_address = s.address AND m.is_from_me = 0
               GROUP BY s.address ORDER BY msg_count DESC"""
        ).fetchall()
        out = []
        for s in rows:
            ev = db.loads(s["evidence_json"], {})
            bits = []
            why = ev.get("why", "")
            if why:
                bits.append(why)
            if (ev.get("messages") is not None and s["tier"] in ("feed", "promo")
                    and "opened" not in why):
                bits.append(f"opened {ev.get('opened', 0)} of {ev.get('messages', 0)}")
            subrules = conn.execute(
                "SELECT email_type, route FROM sender_subrules WHERE sender_address = ?",
                (s["address"],),
            ).fetchall()
            if subrules:
                bits.append(", ".join(f"{r['email_type']} → {r['route']}" for r in subrules))
            out.append({
                "address": s["address"],
                "name": s["display_name"] or s["address"],
                "domain": s["domain"],
                "tier": s["tier"],
                "route": s["route"],
                "evidence": " · ".join(bits),
                "confirmed": bool(s["confirmed_by_user"]),
                "unsub_suggest": bool(ev.get("unsub_suggest")),
                "messages": s["msg_count"],
            })
        counts: dict[str, int] = {}
        for o in out:
            counts[o["tier"] or "?"] = counts.get(o["tier"] or "?", 0) + 1
        return {"senders": out, "counts": counts}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/senders")
def senders_page():
    return FileResponse(STATIC / "senders.html")
