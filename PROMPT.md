# Prompt for Claude Code

Paste everything below the line into Claude Code from inside the `signal-inbox-handoff` folder.

---

You are building **Signal Inbox**, a personal email client that sits on top of my Gmail account. Read `SPEC.md` fully before writing any code, then read the five files in `mockups/` for the visual and interaction design. Do not start coding until you have summarized the spec back to me in ten bullets and I have confirmed.

Key facts about me and the constraints:

- I am one user. This is not multi-tenant. No auth beyond Google OAuth for my own account.
- Gmail stays the source of truth. The app may **read** mail, **add or remove its own labels** (all under a `Signal/` prefix), and **send replies through the Gmail API as me**. It must never delete, trash, or archive anything, and never modify a label it didn't create. If I stop using the app, my Gmail should look untouched except for some extra labels.
- I'm comfortable in Python and have shipped small FastAPI and Next.js projects. Propose a stack in your summary; my default is a Python backend (FastAPI + SQLite) and a plain HTML/JS frontend that matches the mockups, but argue for something else if it's clearly better.
- Classification of senders and extraction of "the ask" from an email should use the Anthropic API (Claude). Keep prompts in their own files so I can edit them.
- The mockups were exported from a design tool and contain `{{ }}` template placeholders and `<sc-if>` / `<sc-for>` tags. Treat them as the source of truth for layout, copy, spacing, colors, and interaction states, not as code to run.
- Every action the app takes must be logged in a way I can see in the UI ("What just happened in Gmail" in the mockups), and every action must be undoable for at least 12 seconds.

Build in the phases described in `SPEC.md` (section 9). Stop at the end of each phase and show me a working demo before starting the next. Phase 1 is read-only and must be runnable against my real inbox before we touch any write path.

Ask me questions when the spec is ambiguous. Do not invent product behavior that isn't in the spec or the mockups; flag it as a question instead.
