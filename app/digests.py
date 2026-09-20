"""Lane digests (SPEC 3.2). One digest per lane per day, merged across that
lane's Feed senders, deduped by item.

Extraction is per source: small parsers where the mail is predictable
(Realtor.com, Zillow, LinkedIn Jobs, RotoReels) and a Claude fallback for
the rest. Parsers work over the stored plain text of the email.
"""

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from . import config, db, llm

log = logging.getLogger("signal.digests")

PRICE_RE = re.compile(r"\$[\d][\d,]{2,}(?:,\d{3})*")
BEDS_RE = re.compile(r"(\d+)\s*(?:bd|bds|bed|beds)\b", re.I)
BATHS_RE = re.compile(r"(\d+(?:\.\d)?)\s*(?:ba|baths?)\b", re.I)
SQFT_RE = re.compile(r"([\d,]{3,})\s*(?:sq\s?ft|sqft)", re.I)
STREET_RE = re.compile(
    r"\d+\s+(?:Via\s+)?[A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){0,4}"
    r"(?:\s(?:St|Ave|Blvd|Dr|Rd|Ln|Ct|Way|Pl|Ter|Cir|Street|Avenue|Drive|Road|Lane)\b)?"
    r"(?:,\s*[A-Z][^\n$]{2,40})?"
)


def _kind_from_subject(subject: str) -> str:
    s = (subject or "").lower()
    if "price drop" in s or "reduced" in s or "price cut" in s:
        return "price_drop"
    if "open house" in s:
        return "open_house"
    if "rent" in s:
        return "rental"
    return "new"


def parse_homes(subject: str, body: str, source: str) -> list[dict]:
    """Realtor.com / Zillow saved-search alerts: price + address + specs."""
    items = []
    default_kind = _kind_from_subject(subject)
    lines = [l.strip() for l in (body or "").splitlines()]
    text = "\n".join(lines)
    for m in PRICE_RE.finditer(text):
        # Listings read "address \n price \n specs": take the nearest
        # address above the price, else the first below it.
        before = text[max(0, m.start() - 120): m.start()]
        after = text[m.end(): m.end() + 200]
        before_hits = list(STREET_RE.finditer(before))
        addr = before_hits[-1] if before_hits else STREET_RE.search(after)
        window = before + after
        beds, baths, sqft = BEDS_RE.search(after), BATHS_RE.search(after), SQFT_RE.search(after)
        if not (addr or beds):
            continue
        drop = None
        wl = window.lower()
        if "price drop" in wl or "reduced" in wl or "was $" in wl:
            drop_m = re.search(r"-\$[\d,]+", after)
            drop = drop_m.group(0) if drop_m else None
        detail_bits = []
        if beds:
            detail_bits.append(f"{beds.group(1)} bd")
        if baths:
            detail_bits.append(f"{baths.group(1)} ba")
        if sqft:
            detail_bits.append(f"{sqft.group(1)} sqft")
        detail_bits.append(source)
        items.append({
            "kind": "price_drop" if drop else default_kind,
            "title": addr.group(0).strip() if addr else (subject or "Listing"),
            "price": m.group(0),
            "drop": drop,
            "detail": " · ".join(detail_bits),
        })
        if len(items) >= 12:
            break
    return items


def parse_linkedin_jobs(subject: str, body: str, source: str) -> list[dict]:
    items = []
    lines = [l.strip() for l in (body or "").splitlines() if l.strip()]
    for i, line in enumerate(lines[:-1]):
        nxt = lines[i + 1]
        if ("·" in nxt or " - " in nxt) and 3 < len(line) < 90 and not PRICE_RE.search(line) \
                and not line.lower().startswith(("view", "see ", "your job alert")):
            items.append({"kind": "job", "title": line,
                          "detail": f"{nxt[:80]} · {source}"})
        if len(items) >= 10:
            break
    return items


def parse_rotoreels(subject: str, body: str, source: str) -> list[dict]:
    items = []
    for line in (body or "").splitlines():
        line = line.strip(" -•*")
        if 25 <= len(line) <= 140 and not line.lower().startswith(("unsubscribe", "view in browser", "http")):
            items.append({"kind": "story", "title": line, "detail": source})
        if len(items) >= 6:
            break
    if not items and subject:
        items = [{"kind": "story", "title": subject, "detail": source}]
    return items


PARSERS = {
    "realtor.com": parse_homes,
    "zillow.com": parse_homes,
    "linkedin.com": parse_linkedin_jobs,
    "rotoreels": parse_rotoreels,
}


def _parser_for(address: str):
    for key, fn in PARSERS.items():
        if key in address:
            return fn
    return None


def _llm_items(lane: str, sender: str, subject: str, body: str) -> list[dict]:
    try:
        out = llm.complete_json(
            "digest",
            config.load_prompt("digest_extract"),
            json.dumps({"lane": lane, "sender": sender, "subject": subject,
                        "body": (body or "")[:4000]}),
        )
        return [i for i in out.get("items", []) if i.get("title")][:12]
    except Exception as e:
        log.warning("digest fallback failed for %s: %s", sender, e)
        return []


def _norm_title(title: str) -> str:
    return re.sub(r"\W+", "", (title or "").lower())


def build_today(conn) -> int:
    """Build/refresh today's digest for every lane with sources. Duplicates
    across sources are removed by normalized title (address for Homes)."""
    today = date.today().isoformat()
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    built = 0
    lanes = conn.execute("SELECT * FROM lanes").fetchall()
    for lane in lanes:
        if lane["snoozed_until"] and lane["snoozed_until"] > datetime.now(timezone.utc).isoformat():
            continue
        sources = [r["sender_address"] for r in conn.execute(
            "SELECT sender_address FROM lane_sources WHERE lane_id = ?", (lane["id"],))]
        if not sources:
            continue
        marks = ",".join("?" for _ in sources)
        msgs = conn.execute(
            f"""SELECT * FROM messages WHERE from_address IN ({marks})
                AND is_from_me = 0 AND received_at > ? ORDER BY received_at""",
            (*sources, since),
        ).fetchall()
        if not msgs:
            continue
        items, seen = [], set()
        for m in msgs:
            source = (m["from_name"] or m["from_address"].split("@")[-1]).strip()
            fn = _parser_for(m["from_address"])
            parsed = fn(m["subject"], m["body_text"], source) if fn else []
            if not parsed:
                parsed = _llm_items(lane["name"], source, m["subject"], m["body_text"])
            for item in parsed:
                key = _norm_title(item.get("title", ""))
                if not key or key in seen:
                    continue
                seen.add(key)
                item["source"] = source
                item["opened"] = bool(m["opened_at"])
                items.append(item)
        if not items:
            continue
        conn.execute("DELETE FROM digests WHERE lane_id = ? AND date = ?",
                     (lane["id"], today))
        conn.execute(
            "INSERT INTO digests (lane_id, date, items_json) VALUES (?, ?, ?)",
            (lane["id"], today, json.dumps(items)),
        )
        built += 1
    return built


KIND_LABEL = {"new": "new", "price_drop": "price drops", "open_house": "open houses",
              "rental": "rentals", "job": "jobs", "story": "stories"}


def summary_line(items: list[dict]) -> str:
    counts: dict[str, int] = {}
    for i in items:
        counts[i.get("kind", "new")] = counts.get(i.get("kind", "new"), 0) + 1
    bits = [f"{n} {KIND_LABEL.get(k, k)}" for k, n in counts.items()]
    return " · ".join(bits[:3])


def today_digest(conn, lane_id: int) -> list[dict]:
    row = conn.execute(
        "SELECT items_json FROM digests WHERE lane_id = ? ORDER BY date DESC LIMIT 1",
        (lane_id,),
    ).fetchone()
    return db.loads(row["items_json"], []) if row else []
