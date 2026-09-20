"""Ingest a JSONL snapshot of Gmail messages into the app database.

Used for previewing the app against real mail pulled through another channel
(e.g. Claude's Gmail connector) before the app's own OAuth backfill runs.
Snapshot lines carry: id, thread_id, sender, to, cc, subject, snippet, body,
date, unread, labels. Anything missing (List-Unsubscribe headers, rfc822
message ids, display names) stays blank and is filled by a real backfill
later - message ids match Gmail's, so `INSERT OR CONFLICT` reconciles.

    SIGNAL_DB_PATH=real.db python -m scripts.ingest_snapshot my@gmail.com dir/*.jsonl
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app import db, rules
from app.gmail.sync import refresh_thread, upsert_message


def _name_from(address: str) -> str:
    local = address.split("@")[0]
    return " ".join(p.capitalize() for p in local.replace(".", " ").replace("_", " ").split())


def _iso(date_str: str) -> str:
    try:
        d = datetime.fromisoformat((date_str or "").replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.isoformat()
    except ValueError:
        return datetime.now(timezone.utc).isoformat()


def main() -> None:
    my_address = sys.argv[1].lower()
    files = sys.argv[2:]
    if not files:
        sys.exit("usage: ingest_snapshot.py <my-address> <file.jsonl> [...]")

    seen_threads: set[str] = set()
    count = 0
    with db.session() as conn:
        db.set_state(conn, "my_address", my_address)
        for path in files:
            for line in Path(path).read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                sender = (r.get("sender") or "").lower().strip("<> ")
                if not sender or not r.get("id"):
                    continue
                received = _iso(r.get("date"))
                is_me = sender == my_address
                unread = bool(r.get("unread"))
                upsert_message(conn, {
                    "gmail_message_id": r["id"],
                    "thread_id": r.get("thread_id") or r["id"],
                    "from_address": sender,
                    "from_name": _name_from(sender),
                    "to_addresses": [a.lower() for a in r.get("to") or []],
                    "cc_addresses": [a.lower() for a in r.get("cc") or []],
                    "subject": r.get("subject") or "",
                    "snippet": (r.get("snippet") or (r.get("body") or ""))[:160],
                    "body_text": (r.get("body") or "")[:20000],
                    "list_unsubscribe": "",
                    "list_unsubscribe_post": "",
                    "rfc822_message_id": "",
                    "received_at": received,
                    "opened_at": None if (unread or is_me) else received,
                    "is_from_me": int(is_me),
                })
                seen_threads.add(r.get("thread_id") or r["id"])
                count += 1
        for tid in seen_threads:
            refresh_thread(conn, tid, my_address)
        conn.commit()
        print(f"ingested {count} messages across {len(seen_threads)} threads")
        rules.run_pipeline(conn)
        print("pipeline done")


if __name__ == "__main__":
    main()
