# Signal Inbox: Product Spec

A view on top of Gmail that turns roughly 30 daily arrivals into a short briefing. Built from 30 days of observed behavior in one Gmail account. Everything here is derived from that data; where the spec says "you," it means the account owner.

## 1. The problem, in numbers

Observed over one representative week (Sep 12 to 19, 2026):

- ~200 threads arrived, about 30 a day.
- 1 was from a human who wanted something.
- Gmail's Primary tab still held 35 non-human items out of 40.
- ~19 a day were promotions. Near-zero open rate.
- 6 a day were real-estate alerts across two senders for three saved searches.
- The sent folder was ~80% notes-to-self and automation output, not correspondence.

Open and reply behavior over 30 days:

- Replies go overwhelmingly to a small group of people (a fantasy baseball league, recruiters, product teams the user reports bugs to). Reply latency to those people is minutes.
- Wedding vendor email is opened 100% of the time and replied to ~0% (the fiancée replies; the user is cc'd).
- Money and security alerts (bank fraud confirms, account access alerts, utility projections) are opened 100%, replied to 0%.
- One newsletter (RotoReels, AL free agents) is opened daily. All other feeds are opened selectively or never.
- The user's own daily automation email (AI Trader Digest) was opened 3 of 14 times.

Design conclusion: this inbox does not need faster triage. It needs the one human email on top, the two things with consequences next, and the other 27 turned into counts and cards.

## 2. Sender tiers (the core object)

Every sender address gets exactly one rule. A rule is a tier plus a route. Rules are the product; the UI is a view of what the rules did.

| Tier | Definition | Default route | Examples from the account |
|---|---|---|---|
| **Person** | A human who might want a reply | People section, notify | Dan Katz (league), Chuck Ferrante (caterer), recruiters |
| **Service** | A company account the user has; one-way | Needs you / Heads up / Money / On its way, by email type | Chase, Google Accounts, SDG&E, Etsy, Anthropic, PayPal |
| **Feed** | Recurring content the user chose | A lane, merged into a daily digest | RotoReels, Realtor.com, Zillow, LinkedIn Jobs, Claude Code weekly |
| **Promo** | Marketing | Promotions, weekly digest, never surfaces daily | Fanatics, Uber, Marine Layer, Bonobos offers |
| **Bot** | Mail the user's own scripts send | A lane tile showing a number, not a message | AI Trader Digest, Rolecall |
| **Notes** | Mail the user sends to themself by hand | Notebook | draft plans, build notes, links, art files |
| **Split** | One sender, several email types with different routes | Per-type routing | DoorDash (receipts → Money, tracking → On its way, deals → Promo); Chase (fraud → Needs you, statements → Money, offers → Promo); Google (new app access → Needs you, "you shared data with" → Heads up); LinkedIn (job alerts → Jobs lane, profile views → muted) |

### 2.1 How a rule is set

1. First email from an unknown sender: guess the tier from the domain, the display name, `List-Unsubscribe` presence, and the first message body (Claude call). File it under the guess and add the sender to a "New senders" card with three one-tap options (Person / Service / Promo). The guessed option is pre-tinted.
2. The user's behavior confirms or overrides:
   - Reply once → Person. Permanent unless changed by hand.
   - Open on most days it arrives → stays Feed.
   - Ignore for 30 days → drop to Promo and add to the unsubscribe suggestions.
   - Sender is the user's own address and the message has a script-like signature (same subject pattern daily, no greeting) → Bot. Otherwise → Notes.
3. Rules are editable on the Senders screen (see `mockups/Senders.html`). Each row shows the tier, the route, and the evidence ("opened 5 of 5," "opened 1 of 28," "you reply within minutes").

### 2.2 Split senders

A Split sender has sub-rules keyed on email type. Email type comes from a Claude classification of subject + first 500 chars into one of: `receipt`, `tracking`, `alert`, `statement`, `verification`, `notice`, `offer`, `digest`, `personal`, `other`. Sub-rules map type → route. The Senders screen shows Split senders with a one-line summary of the map.

## 3. Screens and sections

### 3.1 Today (home). `mockups/Main.html` (phone), `mockups/Desktop.html` and `mockups/Desktop2.html` (desktop)

Sections in order. Each has an admission test. If an item fails the test it does not appear here.

**Counters.** `open, any day` (persistent queue size) · `waiting on them` · `arrived today` · `filed for you`. Open is the number that matters; it is colored terracotta when > 0 and green when 0.

**People.** Test: the sender's tier is Person. Persists until the user replies, marks done, or snoozes. Shows the sender, a one-line summary of the ask (Claude-extracted), and age in days when > 1 day. Age turns terracotta at 3+ days. A thread where the user is only cc'd and someone else is replying shows a **Following** chip instead of actions, and the avatar is faded.

**Needs you.** Test: *if the user does nothing, something bad happens.* Fraud confirmations, payments due, "was this you" security alerts, verifications with expiry. Nothing else. Each row carries its actions inline (Yes / No / That was me / Pay). Rows persist until acted on. Age shown. When empty, show a green line: "Nothing needs you." with the next known dated item if any.

**Waiting on them.** Test: the user sent the last message in the thread and nobody has replied. Shows the person, what the user sent, and days waiting. A **Nudge** button drafts a two-line follow-up. Items leave when a reply arrives (the thread returns to People with the reply on top).

**Heads up.** Test: worth a glance, no action. Utility projections, rate changes, terms updates, gifts, "your data was shared." One collapsed line: "4 heads-ups, nothing to do" plus a comma-joined summary. Expands to a list. Auto-clears after 24 hours. Anything with a dollar amount also appears in Money.

**On its way.** Test: email type is `tracking` and the item has not been delivered. Cards show carrier, status, and an ETA when the email states one. Cleared 24h after delivery.

**Money.** Test: email type is `receipt` or `statement`. A ledger of merchant, amount, source (PayPal, Venmo, card), and any lane tag (e.g. "wedding" when the merchant is a known wedding vendor). Rolls into a monthly ledger view.

**New senders.** Test: a sender with no confirmed rule. See 2.1. Disappears when empty.

**Lanes.** One tile per lane with a count and a one-line summary of the newest digest. Lanes never show unread badges, only counts of items in today's digest. Lanes observed in this account: Baseball, Jobs, Homes, Markets, Notebook, Reads, Watch & go. Reads and Notebook show **no count at all**, only the three most recent titles.

**Promotions.** One dashed line: count filed today, next digest day, and up to three unsubscribe suggestions. Never expands on Today; opens the Promotions digest.

### 3.2 Lane. `mockups/Lane.html`

A lane merges every Feed sender on one topic into one daily digest. The Homes lane merges Realtor.com and Zillow (6 emails/day) into one card set: new listings (price, address, beds/baths/sqft, source, "you opened this one" when applicable), price drops, open houses, rentals. Duplicates across sources are removed by address. Lane settings: digest cadence (daily at a time / weekly), sources, snooze (with a named reason, e.g. "until wedding").

Digest extraction is per source: write a small parser per Feed sender where the HTML is stable (Realtor.com, Zillow, LinkedIn Jobs) and fall back to a Claude extraction prompt for the rest.

### 3.3 Senders. `mockups/Senders.html`

Search, tier filter chips with counts, a "new senders this week" card, then one row per sender: avatar mark, name and domain, tier and route and evidence line, tier badge or an Unsub button for Promo senders with 0 opens in 30 days. Tapping a row opens an editor for tier, route, sub-rules (Split), and digest cadence (Feed).

### 3.4 Reading pane (desktop only). `mockups/Desktop.html`, `mockups/Desktop2.html`

Opens for People and Needs you items. Layout, top to bottom:

1. Tier badge and one line of relationship evidence ("6 replies to Dan this month, usually within the hour").
2. Subject, sender, recipients summary ("to the whole league, 24 people"), date.
3. **Pulled out for you**: the ask, any deadline or age, and related context from the user's own recent sent mail. Then the actions.
4. The original message, untouched.
5. Footer stating the Gmail side effect of the primary action.

Lanes do not open in the reading pane; they open as digests in the center column.

## 4. Actions and their Gmail side effects

| Action | UI effect | Gmail effect |
|---|---|---|
| Reply (default: sender only) | Compose in the reading pane with a starter draft; thread moves to Waiting on them on send | `users.messages.send` in the same thread, from the user's address, with quoted history. Sent folder has it. |
| Reply all | Same, with the extra recipients shown as a warm chip and a count on the Send button | Same, all recipients |
| Done | Item leaves its section; logged | Add label `Signal/Done`. Nothing else. |
| Snooze (until a time) | Item leaves; returns marked "returned" | Add label `Signal/Snoozed`; remove on return |
| Yes / No on a bank or Google confirm | Row leaves; logged with what the service will do next | Open the confirm URL from the email in a server-side fetch **only if the user explicitly enabled that for the sender**; otherwise open it in a new tab for the user. Add `Signal/Done`. |
| Nudge | Compose with a two-line follow-up starter | As Reply |
| Unsubscribe | Sender drops to Promo/muted; logged | Use the `List-Unsubscribe` header (mailto or one-click POST per RFC 8058). Never click unsubscribe links in the body. |
| Set sender rule | Row leaves New senders; future mail routes silently | Add `Signal/<Tier>` label to that sender's existing threads |

Every action:
- Writes a row to an `actions` table with a human-readable description that the UI shows under "What just happened in Gmail."
- Is undoable for 12 seconds via a toast. Undo for a sent message uses a send delay (hold the send for 12s client-side; do not rely on Gmail's undo).
- Never deletes, trashes, or archives, and never touches labels outside the `Signal/` prefix.

## 5. Keyboard and command palette (desktop, v2)

- `Cmd/Ctrl+K` opens the palette. Groups: **act** (only actions currently applicable), **snooze**, **go** (lanes, Senders, Waiting), **gmail** (free-text search fallback). Typing filters; Enter runs the top hit; Esc closes.
- Single keys when focus is not in an input: `Y` yes, `N` no, `G` acknowledge Google, `R` reply, `D` done, `O` open the linked tool (e.g. the roster site), `S` snooze. `Cmd+Enter` sends from the composer. `Esc` cancels compose.
- Key hints render as small `kbd` chips on the buttons themselves, so there is nothing to memorize.

## 6. Reply starter

When the user hits Reply, prefill the body with a two-to-four-line draft written from the extracted ask and the user's observed Gmail voice. Observed voice (from sent mail):

- Greeting is "Hi <first name> -" with a hyphen, not a comma.
- One or two sentences, plain, no preamble.
- Thanks are short: "Thanks!" or "Thanks for the catch!"
- Sign-off is just "Sandy" after a blank line.
- No bullets, no formatting.

The starter is editable, never auto-sent, and the UI says so in one line above the textarea.

## 7. Visual system

Current look ("warm paper"), used in all mockups:

- Ground `#f3efe7`, card `#ffffff`, rail `#ede8dd`, line `#e0d9cb` / `#ede8dd`, ink `#1c1b18`, muted `#5e5a52`.
- Accent green `#2d5f4f` (People, done, positive), tint `#e6eee9`.
- Accent terracotta `#a8482a` (Needs you, age, anything that costs you), tint `#f6e9df`. **Terracotta is reserved for that meaning; never use it decoratively.**
- Display: Fraunces (Google Fonts), weight 500/600. Body/UI: Instrument Sans. Key hints: JetBrains Mono.
- Marks: solid green disc with initials = Person. Same disc at 55% opacity = Person you're only following. Hollow 1.5px outline = Service. No mark = Feed.
- Buttons are pills. Primary = solid green. Secondary = white with a hairline border. Destructive/decline = secondary, never red.
- No unread badges anywhere. Counts only, and none on Reads or Notebook.

The user may swap this for a DESIGN.md-based system later (WIRED was the candidate). Keep colors, fonts, and radii in CSS variables so the swap is a one-file change.

## 8. Data model (SQLite is fine)

```
senders(address PK, display_name, domain, tier, route, evidence_json, created_at, confirmed_by_user)
sender_subrules(sender_address FK, email_type, route)
threads(gmail_thread_id PK, last_message_at, section, state ENUM(open, done, snoozed, sent, following), snooze_until, ask_summary, age_days, is_cc_only)
messages(gmail_message_id PK, thread_id FK, from_address, email_type, extracted_json, received_at, opened_at)
lanes(id PK, name, cadence, digest_time, snoozed_until, snooze_reason)
lane_sources(lane_id FK, sender_address FK)
digests(id PK, lane_id FK, date, items_json)
money(id PK, message_id FK, merchant, amount_cents, source, tag)
tracking(id PK, message_id FK, carrier, status, eta, delivered_at)
actions(id PK, at, kind, target, description, gmail_effect, undone_at)
```

Gmail sync: OAuth with scopes `gmail.readonly`, `gmail.modify` (labels only; document this in the consent screen text), `gmail.send`. Initial backfill of 30 days via `threads.list` + `threads.get`. Incremental sync via `users.history.list` polled every 60s in Phase 1; switch to `users.watch` + Pub/Sub in Phase 3 if latency matters. Store `historyId`.

Open tracking: Gmail's `UNREAD` label is the proxy for "opened." It is imperfect (previews mark things read) but it is what the tier learning uses.

## 9. Build phases

**Phase 1: read-only mirror.** OAuth, 30-day backfill, sender guessing, email-type classification, section admission, the desktop Today screen and reading pane rendering real data. No write path at all. Success: the Today screen for the real account shows the right one or two People items on top and the right two Needs you items, with everything else filed. The user reviews the sender guesses for accuracy before Phase 2.

**Phase 2: actions.** Labels under `Signal/`, done/snooze state, reply and reply-all through Gmail with the starter draft and 12s hold, the actions log, undo, New senders sorting, the Senders screen. Success: the user runs the app for a full day without opening Gmail.

**Phase 3: lanes, palette, phone.** Digest parsers for Realtor.com, Zillow, LinkedIn Jobs, RotoReels; Claude fallback extractor; lane screen; Promotions weekly digest and RFC 8058 unsubscribe; Cmd+K and single keys; the phone layout from `mockups/Main.html`. Success: the six real-estate emails a day appear as one card set, and the user unsubscribes from at least three promo senders from inside the app.

**Later, not now:** lock-screen or widget for On its way; block-based composer; multi-account.

## 10. Non-goals

- Not a Gmail replacement. No folders, no drag-and-drop filing, no bulk select.
- Not a task manager. "Needs you" is admission-tested, not user-curated.
- No unread counts, no badges, no "inbox zero" framing.
- No AI-written replies sent without a human edit-or-confirm step.
