"""Backfill and incremental sync. Read-only against Gmail.

Usage:
    python -m app.gmail.sync --backfill     # 30-day backfill + classification
    python -m app.gmail.sync --poll         # one incremental pass

The FastAPI app runs the poll loop itself every 60s (SPEC 8).
"""

import argparse
import json
import logging

import httpx

from .. import config, db, rules
from . import auth, parse
from .client import GmailClient

log = logging.getLogger("signal.sync")


def upsert_message(conn, m: dict) -> None:
    conn.execute(
        """INSERT INTO messages (gmail_message_id, thread_id, from_address, from_name,
               to_addresses, cc_addresses, subject, snippet, body_text, list_unsubscribe,
               rfc822_message_id, received_at, opened_at, is_from_me)
           VALUES (:gmail_message_id, :thread_id, :from_address, :from_name,
               :to_addresses, :cc_addresses, :subject, :snippet, :body_text, :list_unsubscribe,
               :rfc822_message_id, :received_at, :opened_at, :is_from_me)
           ON CONFLICT(gmail_message_id) DO UPDATE SET
               opened_at = COALESCE(messages.opened_at, excluded.opened_at),
               snippet = excluded.snippet""",
        {
            **m,
            "to_addresses": json.dumps(m["to_addresses"]),
            "cc_addresses": json.dumps(m["cc_addresses"]),
        },
    )


def refresh_thread(conn, thread_id: str, my_address: str) -> None:
    """Recompute a thread's derived fields from its stored messages."""
    msgs = conn.execute(
        "SELECT * FROM messages WHERE thread_id = ? ORDER BY received_at",
        (thread_id,),
    ).fetchall()
    if not msgs:
        return
    me = my_address.lower()
    last = msgs[-1]
    incoming = [m for m in msgs if not m["is_from_me"]]

    # cc-only: the user appears in Cc but never in To on this thread's
    # incoming mail, and hasn't sent in the thread (SPEC 3.1 Following).
    in_to = any(me in db.loads(m["to_addresses"], []) for m in incoming)
    in_cc = any(me in db.loads(m["cc_addresses"], []) for m in incoming)
    i_sent = any(m["is_from_me"] for m in msgs)
    is_cc_only = int(in_cc and not in_to and not i_sent)

    counterpart = next(
        (m["from_address"] for m in reversed(msgs) if not m["is_from_me"]), None
    )

    # Age: days since the earliest incoming message not yet answered by me.
    last_mine = max(
        (m["received_at"] for m in msgs if m["is_from_me"]), default=None
    )
    unanswered = [
        m for m in incoming if last_mine is None or m["received_at"] > last_mine
    ]
    from datetime import datetime, timezone

    age_days = None
    if unanswered:
        first = datetime.fromisoformat(unanswered[0]["received_at"])
        age_days = max(0, (datetime.now(timezone.utc) - first).days)

    conn.execute(
        """INSERT INTO threads (gmail_thread_id, subject, last_message_at, last_from_me,
               is_cc_only, counterpart, age_days, state)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(gmail_thread_id) DO UPDATE SET
               subject = excluded.subject,
               last_message_at = excluded.last_message_at,
               last_from_me = excluded.last_from_me,
               is_cc_only = excluded.is_cc_only,
               counterpart = excluded.counterpart,
               age_days = excluded.age_days,
               state = CASE WHEN threads.state = 'following' AND excluded.is_cc_only = 0
                            THEN 'open' ELSE threads.state END""",
        (
            thread_id,
            msgs[0]["subject"],
            last["received_at"],
            last["is_from_me"],
            is_cc_only,
            counterpart,
            age_days,
            "following" if is_cc_only else "open",
        ),
    )


def ingest_thread(conn, client: GmailClient, thread_id: str, my_address: str) -> None:
    data = client.get_thread(thread_id)
    for raw in data.get("messages", []):
        upsert_message(conn, parse.parse_message(raw, my_address))
    refresh_thread(conn, thread_id, my_address)


def backfill() -> None:
    creds = auth.get_credentials()
    client = GmailClient(creds)
    profile = client.profile()
    my_address = profile["emailAddress"]

    with db.session() as conn:
        db.set_state(conn, "my_address", my_address)
        q = f"newer_than:{config.BACKFILL_DAYS}d"
        page = None
        count = 0
        while True:
            resp = client.list_threads(q, page)
            for t in resp.get("threads", []):
                ingest_thread(conn, client, t["id"], my_address)
                count += 1
                if count % 25 == 0:
                    conn.commit()
                    log.info("backfill: %d threads", count)
            page = resp.get("nextPageToken")
            if not page:
                break
        db.set_state(conn, "history_id", str(profile["historyId"]))
        log.info("backfill complete: %d threads", count)
        rules.run_pipeline(conn)


def poll_once() -> bool:
    """One incremental pass. Returns False when a full re-backfill is needed
    (expired historyId)."""
    creds = auth.get_credentials()
    client = GmailClient(creds)
    with db.session() as conn:
        my_address = db.get_state(conn, "my_address")
        start = db.get_state(conn, "history_id")
        if not (my_address and start):
            log.warning("no backfill state; run --backfill first")
            return False
        changed: set[str] = set()
        page = None
        newest = start
        try:
            while True:
                resp = client.list_history(start, page)
                newest = str(resp.get("historyId", newest))
                for h in resp.get("history", []):
                    for added in h.get("messagesAdded", []):
                        changed.add(added["message"]["threadId"])
                page = resp.get("nextPageToken")
                if not page:
                    break
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                log.warning("historyId expired; full backfill required")
                return False
            raise
        for thread_id in changed:
            ingest_thread(conn, client, thread_id, my_address)
        db.set_state(conn, "history_id", newest)
        if changed:
            log.info("poll: %d threads updated", len(changed))
            rules.run_pipeline(conn)
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--poll", action="store_true")
    args = ap.parse_args()
    if args.backfill:
        backfill()
    elif args.poll:
        poll_once()
    else:
        ap.print_help()
