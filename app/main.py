"""FastAPI app: serves the UI and the read-only JSON API (Phase 1)."""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import actions, admission, config, db, drafts

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


async def _actions_loop():
    """Executes held sends after their 12s undo window and wakes snoozes."""
    tick = 0
    while True:
        try:
            def work(check_snoozes: bool):
                with db.session() as conn:
                    actions.execute_due_sends(conn)
                    actions.execute_due_ops(conn)
                    if check_snoozes:
                        actions.wake_snoozed(conn)
            await asyncio.to_thread(work, tick % 15 == 0)
        except Exception as e:
            log.warning("actions loop failed: %s", e)
        tick += 1
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init().close()
    tasks = [asyncio.create_task(_actions_loop())]
    if not config.DEMO and config.TOKEN_PATH.exists():
        tasks.append(asyncio.create_task(_poll_loop()))
    elif not config.DEMO:
        log.warning(
            "no token.json; run `python -m app.gmail.sync --backfill` first "
            "(or set SIGNAL_DEMO=1 for fixture data)"
        )
    yield
    for t in tasks:
        t.cancel()


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
        can_reply = not last["is_from_me"]
        first_name = (last["from_name"] or "").split(" ")[0] or "them"
        others = max(0, n - 1)
        # SPEC 3.4 item 5: the footer states the Gmail side effect of the
        # primary action.
        if can_reply and others:
            footer = (f"Reply goes out through Gmail as you. Reply all hits "
                      f"{n} inboxes; the default here is {first_name}.")
        elif can_reply:
            footer = "Reply goes out through Gmail as you, in this thread."
        else:
            footer = "Done adds the label Signal/Done in Gmail. Nothing else."
        return {
            "thread_id": thread_id,
            "tier": tier,
            "state": t["state"],
            "evidence": evidence,
            "subject": last["subject"] or t["subject"],
            "sender_name": last["from_name"] or (sender and sender["display_name"]) or last["from_address"],
            "sender_domain": last["from_address"].split("@")[-1],
            "initials": admission._initials(last["from_name"] or last["from_address"]),
            "recipients_summary": f"{recipients_summary} · {when}",
            "first_name": first_name,
            "others_count": others,
            "can_reply": can_reply,
            "message_id": last["gmail_message_id"],
            "pulled_out": {
                "ask": ask.get("ask", ""),
                "age_note": ask.get("age_note", ""),
                "related": ask.get("related", ""),
            },
            "body": last["body_text"] or last["snippet"],
            "footer": footer,
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


# ---------- Phase 2: actions ----------

class ThreadRef(BaseModel):
    thread_id: str


class SnoozeBody(BaseModel):
    thread_id: str
    preset: str = "monday"  # tomorrow | monday | week


class ConfirmBody(BaseModel):
    message_id: str
    answer: str  # yes | no | ack


class RuleBody(BaseModel):
    address: str
    tier: str
    route: str | None = None
    subrules: dict[str, str] | None = None


class DraftBody(BaseModel):
    mode: str = "reply"  # reply | nudge


class SendBody(BaseModel):
    body: str
    reply_all: bool = False


@app.post("/api/actions/done")
def act_done(body: ThreadRef):
    with db.session() as conn:
        return actions.mark_done(conn, body.thread_id)


def _snooze_until(preset: str) -> tuple[str, str]:
    now = datetime.now().astimezone()
    eight = dtime(8, 0)
    if preset == "tomorrow":
        target = datetime.combine(now.date() + timedelta(days=1), eight).astimezone()
        label = "tomorrow at 8am"
    elif preset == "week":
        target = datetime.combine(now.date() + timedelta(days=7), eight).astimezone()
        label = "in a week, at 8am"
    else:
        days = (7 - now.weekday()) % 7 or 7  # next Monday
        target = datetime.combine(now.date() + timedelta(days=days), eight).astimezone()
        label = "Monday at 8am"
    return target.isoformat(), f"to People {label}"


@app.post("/api/actions/snooze")
def act_snooze(body: SnoozeBody):
    until, label = _snooze_until(body.preset)
    with db.session() as conn:
        return actions.snooze(conn, body.thread_id, until, label)


@app.post("/api/actions/confirm")
def act_confirm(body: ConfirmBody):
    with db.session() as conn:
        try:
            return actions.confirm(conn, body.message_id, body.answer)
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.post("/api/actions/rule")
def act_rule(body: RuleBody):
    with db.session() as conn:
        try:
            return actions.set_rule(conn, body.address, body.tier,
                                    body.route, body.subrules)
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.post("/api/actions/{action_id}/undo")
def act_undo(action_id: int):
    with db.session() as conn:
        try:
            return actions.undo(conn, action_id)
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.post("/api/thread/{thread_id}/draft")
def thread_draft(thread_id: str, body: DraftBody):
    with db.session() as conn:
        try:
            starter = drafts.make_starter(conn, thread_id, body.mode)
            info = actions.reply_recipients(conn, thread_id, reply_all=True)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {
            **starter,
            "to_name": info["message"]["from_name"] or info["to"][0],
            "others_count": len(info["others"]),
        }


@app.post("/api/thread/{thread_id}/send")
def thread_send(thread_id: str, body: SendBody):
    if not body.body.strip():
        raise HTTPException(400, "empty reply")
    with db.session() as conn:
        try:
            return actions.queue_send(conn, thread_id, body.body, body.reply_all)
        except ValueError as e:
            raise HTTPException(400, str(e))


# ---------- Phase 3: lanes, promotions, unsubscribe ----------

class LaneSettings(BaseModel):
    cadence: str | None = None          # daily | weekly
    digest_time: str | None = None      # "07:00"
    snooze_days: int | None = None
    snooze_reason: str | None = None
    unsnooze: bool = False


class UnsubBody(BaseModel):
    address: str


@app.get("/api/lane/{name}")
def api_lane(name: str):
    from . import digests as dg
    with db.session() as conn:
        lane = conn.execute("SELECT * FROM lanes WHERE name = ?", (name,)).fetchone()
        if not lane:
            raise HTTPException(404)
        sources = conn.execute(
            """SELECT s.address, s.display_name FROM lane_sources ls
               JOIN senders s ON s.address = ls.sender_address WHERE ls.lane_id = ?""",
            (lane["id"],),
        ).fetchall()
        items = dg.today_digest(conn, lane["id"])
        merged_from = conn.execute(
            """SELECT COUNT(*) AS n FROM messages WHERE from_address IN
               (SELECT sender_address FROM lane_sources WHERE lane_id = ?)
               AND is_from_me = 0 AND received_at > datetime('now', '-1 day')""",
            (lane["id"],),
        ).fetchone()["n"]
        lead = items[:6]
        rest: dict[str, int] = {}
        for i in items[6:]:
            k = i.get("kind", "other")
            rest[k] = rest.get(k, 0) + 1
        return {
            "name": lane["name"],
            "cadence": lane["cadence"],
            "digest_time": lane["digest_time"],
            "snoozed_until": lane["snoozed_until"],
            "snooze_reason": lane["snooze_reason"],
            "sources": [s["display_name"] or s["address"] for s in sources],
            "merged_from": merged_from,
            "items": items,
            "also": [
                {"label": dg.KIND_LABEL.get(k, k), "count": n} for k, n in rest.items()
            ],
        }


@app.post("/api/lane/{name}/settings")
def api_lane_settings(name: str, body: LaneSettings):
    with db.session() as conn:
        lane = conn.execute("SELECT * FROM lanes WHERE name = ?", (name,)).fetchone()
        if not lane:
            raise HTTPException(404)
        if body.cadence in ("daily", "weekly"):
            conn.execute("UPDATE lanes SET cadence = ? WHERE id = ?",
                         (body.cadence, lane["id"]))
        if body.digest_time:
            conn.execute("UPDATE lanes SET digest_time = ? WHERE id = ?",
                         (body.digest_time, lane["id"]))
        if body.unsnooze:
            conn.execute("UPDATE lanes SET snoozed_until = NULL, snooze_reason = NULL "
                         "WHERE id = ?", (lane["id"],))
        elif body.snooze_days:
            until = (datetime.now().astimezone() + timedelta(days=body.snooze_days)).isoformat()
            conn.execute("UPDATE lanes SET snoozed_until = ?, snooze_reason = ? WHERE id = ?",
                         (until, body.snooze_reason or "", lane["id"]))
        return {"ok": True}


@app.get("/api/promotions")
def api_promotions():
    with db.session() as conn:
        rows = conn.execute(
            """SELECT s.address, s.display_name, s.route, s.evidence_json,
                      COUNT(m.gmail_message_id) AS week_count,
                      MAX(m.received_at) AS last_at,
                      MAX(m.subject) AS last_subject,
                      MAX(CASE WHEN m.list_unsubscribe != '' THEN 1 ELSE 0 END) AS has_unsub
               FROM senders s
               LEFT JOIN messages m ON m.from_address = s.address
                   AND m.is_from_me = 0 AND m.received_at > datetime('now', '-7 day')
               WHERE s.tier = 'promo'
               GROUP BY s.address ORDER BY week_count DESC"""
        ).fetchall()
        out = []
        for r in rows:
            ev = db.loads(r["evidence_json"], {})
            out.append({
                "address": r["address"],
                "name": r["display_name"] or r["address"],
                "week_count": r["week_count"],
                "last_subject": r["last_subject"] or "",
                "muted": r["route"] == "muted",
                "suggest": bool(ev.get("unsub_suggest")),
                "evidence": ev.get("why", ""),
                "has_unsub": bool(r["has_unsub"]),
            })
        return {"senders": out, "digest_day": "Sunday"}


@app.post("/api/actions/unsubscribe")
def act_unsubscribe(body: UnsubBody):
    with db.session() as conn:
        try:
            return actions.unsubscribe(conn, body.address)
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.get("/api/log")
def api_log():
    with db.session() as conn:
        return {"log": actions.recent_log(conn)}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/senders")
def senders_page():
    return FileResponse(STATIC / "senders.html")


@app.get("/lane/{name}")
def lane_page(name: str):
    return FileResponse(STATIC / "lane.html")


@app.get("/promotions")
def promotions_page():
    return FileResponse(STATIC / "promotions.html")
