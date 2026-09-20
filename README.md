# Signal Inbox

A personal view on top of Gmail that turns ~30 daily arrivals into a short
briefing. Spec in `SPEC.md`, design mockups in `mockups/`, original build
prompt in `PROMPT.md`.

**Status: Phase 2 (actions).** On top of the Phase 1 read-only mirror:
done/snooze with `Signal/` labels, reply and reply-all through Gmail with a
starter draft in your voice and a 12-second hold before anything sends,
Yes/No handling on Needs-you rows (the confirm link opens in a new tab -
never fetched server-side), one-tap New-senders sorting, rule editing on the
Senders screen, and an actions log ("What just happened in Gmail") where
every entry is undoable for 12 seconds. The app never deletes, trashes, or
archives, and never touches labels outside `Signal/`.

Phase 2 adds the `gmail.modify` (labels only) and `gmail.send` scopes - if
you have a Phase 1 `token.json`, delete it and rerun the backfill to
re-consent. Phase 3 (lanes, digest parsers, unsubscribe, Cmd+K, phone) is
next.

## Run the demo (no Gmail needed)

```bash
pip install -r requirements.txt
SIGNAL_DB_PATH=demo.db python -m scripts.seed_demo
SIGNAL_DB_PATH=demo.db SIGNAL_DEMO=1 uvicorn app.main:app --port 8000
# open http://127.0.0.1:8000
```

## Run against the real inbox

1. **OpenRouter key** - copy `.env.example` to `.env` and set
   `OPENROUTER_API_KEY`. Models per task are configurable there too
   (defaults: Haiku 4.5 for bulk classification, Opus 5 for ask extraction).
2. **Google OAuth (one time)** - in Google Cloud Console: create a project,
   enable the Gmail API, configure the OAuth consent screen (External; add
   yourself as a test user), create an OAuth client of type **Desktop app**,
   and download the JSON to `credentials.json` in the repo root (gitignored).
3. **Backfill** - `python -m app.gmail.sync --backfill` opens the consent
   browser window, pulls 30 days of threads, guesses sender tiers, and
   classifies service mail. Rerunnable; it only adds.
4. **Serve** - `uvicorn app.main:app --port 8000`. The app polls
   `history.list` every 60s for new mail.
5. Review the sender guesses at `/senders` - that review is Phase 1's exit
   criterion before any write path is built.

## Layout

```
app/config.py      env, model map, scopes, lanes
app/db.py          SQLite schema (SPEC section 8)
app/llm.py         OpenRouter client (JSON calls, lenient parse, retry)
app/rules.py       sender tiers, email types, extraction pipeline
app/admission.py   section admission tests (SPEC 3.1)
app/main.py        FastAPI: /api/today, /api/thread/{id}, /api/senders
app/gmail/         OAuth, REST client, message parsing, backfill + poll
app/static/        Today screen, reading pane, Senders page (warm paper)
prompts/           editable prompt files (sender tier, email type, ask)
scripts/seed_demo.py  fixture world from the mockups
```

Design tokens live in `:root` of `app/static/style.css` so a later
DESIGN.md-based swap is a one-file change (SPEC 7).
