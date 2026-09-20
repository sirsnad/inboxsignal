"""Thin Gmail REST client over httpx.

Read-only in Phase 1: profile, threads.list/get, history.list. Deliberately
no wrapper for anything that writes.
"""

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
