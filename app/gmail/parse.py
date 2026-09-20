"""Turn Gmail API message payloads into flat dicts for the DB."""

import base64
import re
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime


def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _addresses(value: str) -> list[dict]:
    return [
        {"name": name, "address": addr.lower()}
        for name, addr in getaddresses([value])
        if addr
    ]


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    text = text.replace("&gt;", ">").replace("&#39;", "'").replace("&quot;", '"')
    return re.sub(r"[ \t]+", " ", text)


def _body_text(payload: dict) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    plain, html = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            html.append(_decode(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    if plain:
        return "\n".join(plain)
    if html:
        return _strip_html("\n".join(html))
    return ""


def parse_message(msg: dict, my_address: str) -> dict:
    payload = msg.get("payload", {})
    from_list = _addresses(_header(payload, "From"))
    sender = from_list[0] if from_list else {"name": "", "address": ""}
    label_ids = msg.get("labelIds", []) or []

    try:
        received = parsedate_to_datetime(_header(payload, "Date"))
    except (ValueError, TypeError):
        received = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, timezone.utc)
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)

    body = _body_text(payload)
    is_from_me = sender["address"] == my_address.lower()
    # Gmail's UNREAD label is the proxy for "opened" (SPEC 8). Sent mail
    # never carries UNREAD, so "opened" is meaningless there.
    opened = None
    if not is_from_me and "UNREAD" not in label_ids:
        opened = received.isoformat()

    return {
        "gmail_message_id": msg["id"],
        "thread_id": msg["threadId"],
        "from_address": sender["address"],
        "from_name": sender["name"],
        "to_addresses": [a["address"] for a in _addresses(_header(payload, "To"))],
        "cc_addresses": [a["address"] for a in _addresses(_header(payload, "Cc"))],
        "subject": _header(payload, "Subject"),
        "snippet": msg.get("snippet", ""),
        "body_text": body[:20000],
        "list_unsubscribe": _header(payload, "List-Unsubscribe"),
        "received_at": received.isoformat(),
        "opened_at": opened,
        "is_from_me": int(is_from_me),
        "label_ids": label_ids,
    }
