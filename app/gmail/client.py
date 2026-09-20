"""Thin Gmail REST client over httpx.

Reads: profile, threads.list/get, history.list.
Writes (Phase 2, and nothing beyond these): label create/list, thread label
modify, message send. There is deliberately no wrapper for delete, trash,
archive, or modifying anything but labels - the app must never do those
(PROMPT.md).
"""

import base64
import time

import httpx
from google.auth.transport.requests import Request

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailClient:
    def __init__(self, credentials):
        self.credentials = credentials
        self.http = httpx.Client(timeout=30)

    def _headers(self) -> dict:
        if self.credentials.expired and self.credentials.refresh_token:
            self.credentials.refresh(Request())
        return {"Authorization": f"Bearer {self.credentials.token}"}

    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(4):
            resp = self.http.get(f"{BASE}{path}", params=params, headers=self._headers())
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    def profile(self) -> dict:
        return self._get("/profile")

    def list_threads(self, q: str, page_token: str | None = None) -> dict:
        params = {"q": q, "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        return self._get("/threads", params)

    def get_thread(self, thread_id: str) -> dict:
        return self._get(f"/threads/{thread_id}", {"format": "full"})

    def list_history(self, start_history_id: str, page_token: str | None = None) -> dict:
        params = {"startHistoryId": start_history_id, "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        return self._get("/history", params)

    # ---- writes (labels and send only) ----

    def _post(self, path: str, body: dict) -> dict:
        for attempt in range(4):
            resp = self.http.post(f"{BASE}{path}", json=body, headers=self._headers())
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    def list_labels(self) -> list[dict]:
        return self._get("/labels").get("labels", [])

    def create_label(self, name: str) -> dict:
        assert name.startswith("Signal/"), "only Signal/ labels may be created"
        return self._post("/labels", {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        })

    def modify_thread_labels(self, thread_id: str, add: list[str] | None = None,
                             remove: list[str] | None = None) -> dict:
        return self._post(f"/threads/{thread_id}/modify", {
            "addLabelIds": add or [],
            "removeLabelIds": remove or [],
        })

    def send_message(self, raw_mime: bytes, thread_id: str | None = None) -> dict:
        body = {"raw": base64.urlsafe_b64encode(raw_mime).decode()}
        if thread_id:
            body["threadId"] = thread_id
        return self._post("/messages/send", body)
