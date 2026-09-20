"""Seed a demo database with the world from the mockups, so the UI can be
demoed without Gmail credentials.

    SIGNAL_DB_PATH=demo.db python -m scripts.seed_demo
    SIGNAL_DB_PATH=demo.db SIGNAL_DEMO=1 uvicorn app.main:app
"""

import json
from datetime import datetime, timedelta, timezone

from app import config, db

NOW = datetime.now(timezone.utc)
ME = "sandywelsch@gmail.com"


def ago(days=0, hours=0):
    return (NOW - timedelta(days=days, hours=hours)).isoformat()


def sender(conn, address, name, tier, route, evidence=None, confirmed=1, subrules=None):
    conn.execute(
        """INSERT OR REPLACE INTO senders
           (address, display_name, domain, tier, route, evidence_json, created_at, confirmed_by_user)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (address, name, address.split("@")[-1], tier, route,
         json.dumps(evidence or {}), ago(30), confirmed),
    )
    for etype, r in (subrules or {}).items():
        conn.execute(
            "INSERT OR REPLACE INTO sender_subrules VALUES (?, ?, ?)",
            (address, etype, r),
        )
    if route and route.startswith("lane:"):
        lane = conn.execute("SELECT id FROM lanes WHERE name = ?", (route[5:],)).fetchone()
        if lane:
            conn.execute(
                "INSERT OR IGNORE INTO lane_sources VALUES (?, ?)",
                (lane["id"], address),
            )


_mid = 0


def message(conn, thread_id, from_addr, from_name, subject, body, received,
            to=None, cc=None, email_type=None, extracted=None, opened=True,
            from_me=False, snippet=None, unsub="", unsub_post=""):
    global _mid
    _mid += 1
    mid = f"demo-m{_mid:04d}"
    conn.execute(
        """INSERT OR REPLACE INTO messages
           (gmail_message_id, thread_id, from_address, from_name, to_addresses,
            cc_addresses, subject, snippet, body_text, list_unsubscribe,
            list_unsubscribe_post, email_type, extracted_json, received_at,
            opened_at, is_from_me)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mid, thread_id, from_addr, from_name, json.dumps(to or [ME]),
         json.dumps(cc or []), subject, snippet or body[:110], body, unsub,
         unsub_post, email_type, json.dumps(extracted) if extracted else None,
         received, received if (opened and not from_me) else None, int(from_me)),
    )
    return mid


def thread(conn, tid, subject, last_at, counterpart, state="open", last_from_me=0,
           cc_only=0, age=None, ask=None):
    conn.execute(
        """INSERT OR REPLACE INTO threads
           (gmail_thread_id, subject, last_message_at, section, state, snooze_until,
            ask_summary, age_days, is_cc_only, last_from_me, counterpart)
           VALUES (?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?, ?)""",
        (tid, subject, last_at, state, json.dumps(ask) if ask else None,
         age, cc_only, last_from_me, counterpart),
    )


def main():
    config.DB_PATH.unlink(missing_ok=True)
    with db.session() as conn:
        db.set_state(conn, "my_address", ME)

        # ---- people ----
        sender(conn, "dan.katz@tydings.com", "Dan Katz", "person", "people",
               {"messages": 9, "opened": 9, "replied_threads": 6,
                "why": "you reply within minutes"})
        thread(conn, "t-dan", "Free agent pickups from 9/3 forward", ago(8),
               "dan.katz@tydings.com", age=8, ask={
                   "one_line": "Check pickups since 9/3 showing $1 salaries instead of $25.",
                   "ask": "check your roster for any pickup since 9/3 listed at a $1 salary and correct it to $25",
                   "age_note": "8 days old. No deadline stated, but the expansion draft is coming and salaries feed the cap",
                   "related": "you asked Dan to activate Ryan and reserve McClanahan on Sep 7, so you have at least one recent pickup",
               })
        message(conn, "t-dan", "dan.katz@tydings.com", "Dan Katz",
                "Free agent pickups from 9/3 forward",
                "I just noticed that the guys I've picked up since 9/3 have $1 "
                "salaries instead of $25. I've corrected mine, but could everyone "
                "check their rosters and, if they have any recent pickups showing "
                "a $1 salary, fix them?",
                ago(8), to=[ME] + [f"owner{i}@spitball.example" for i in range(23)])

        sender(conn, "chuck@abbeycatering.com", "Chuck Ferrante", "person", "people",
               {"messages": 4, "opened": 4, "replied_threads": 0,
                "why": "wedding vendor; Alex replies, you're cc'd"})
        thread(conn, "t-chuck", "Table menus and rentals", ago(3),
               "chuck@abbeycatering.com", state="following", cc_only=1, age=3, ask={
                   "one_line": "No table menus provided; you'd bring your own. No lamp rentals either.",
                   "ask": "", "age_note": "", "related": ""})
        message(conn, "t-chuck", "chuck@abbeycatering.com", "Chuck Ferrante",
                "Table menus and rentals",
                "We don't provide table menus, so you'd bring your own. We also "
                "don't have lamp rentals, sorry!",
                ago(3), to=["alex@example.com"], cc=[ME])

        # ---- needs you ----
        sender(conn, "no.reply.alerts@chase.com", "Chase", "split", "split",
               {"messages": 6, "opened": 6, "why": "fraud to Needs you, statements to Money, offers to Promotions"},
               subrules={"alert": "needs_you", "statement": "money", "offer": "promotions"})
        thread(conn, "t-chase", "Did you make this charge?", ago(3),
               "no.reply.alerts@chase.com", age=3)
        message(conn, "t-chase", "no.reply.alerts@chase.com", "Chase",
                "Did you make this charge?",
                "We noticed a charge on your Sapphire card ending 9163. If this "
                "was you, confirm; otherwise we'll freeze the card.",
                ago(3), email_type="alert", opened=True,
                snippet="Card ending 9163 · same minute as your Etsy order",
                extracted={"email_type": "alert",
                           "summary": "confirm a Sapphire charge"})

        sender(conn, "no-reply@accounts.google.com", "Google Accounts", "split", "split",
               {"messages": 3, "opened": 3, "why": "new app access to Needs you, data sharing to Heads up"},
               subrules={"alert": "needs_you", "notice": "heads_up"})
        thread(conn, "t-muse", "Muse was granted access to your Google Account",
               ago(2), "no-reply@accounts.google.com", age=2)
        message(conn, "t-muse", "no-reply@accounts.google.com", "Google",
                'Muse was granted access to your Google Account',
                'You signed in to "Muse" with Google. If this was you, no action '
                "is needed. If not, review your account activity.",
                ago(2), email_type="alert", opened=True,
                snippet="Sign-in with Google · Thu 9:37 pm",
                extracted={"email_type": "alert",
                           "summary": '"Muse" was given account access'})

        # ---- waiting on them ----
        sender(conn, "ryan@truesearch.example", "Ryan Prather", "person", "people",
               {"replied_threads": 1, "why": "recruiter; you replied"})
        thread(conn, "t-ryan", "Strategic partnerships role", ago(5),
               "ryan@truesearch.example", last_from_me=1)
        message(conn, "t-ryan", "ryan@truesearch.example", "Ryan Prather",
                "Strategic partnerships role",
                "Would love to see your resume for the TrueSearch partnerships role.",
                ago(6))
        message(conn, "t-ryan", ME, "Sandy Welsch", "Re: Strategic partnerships role",
                "Hi Ryan - resume attached. Thanks!\n\nSandy", ago(5),
                to=["ryan@truesearch.example"], from_me=True)

        sender(conn, "rob@wisprflow.example", "Rob", "person", "people",
               {"replied_threads": 2, "why": "you report bugs to the product team"})
        thread(conn, "t-rob", "Calendar sync bug", ago(3),
               "rob@wisprflow.example", last_from_me=1)
        message(conn, "t-rob", "rob@wisprflow.example", "Rob",
                "Re: Calendar sync bug", "Thanks, looking into it.", ago(4))
        message(conn, "t-rob", ME, "Sandy Welsch", "Calendar sync bug",
                "Hi Rob - the calendar sync bug is still happening on 2.3.1. "
                "Repro steps below. Thanks!\n\nSandy", ago(3),
                to=["rob@wisprflow.example"], from_me=True)

        # ---- heads up (notices, last 24h) ----
        for addr, name, subj, summary in [
            ("noreply@sdge.com", "SDG&E", "Your projected bill",
             "SDG&E projects $355 to $378"),
            ("no-reply@robinhood.com", "Robinhood", "Margin rate change",
             "Robinhood raised margin rates"),
            ("service@bonobos.com", "Bonobos", "Updates to our terms",
             "Bonobos terms update"),
            ("noreply@steampowered.com", "Steam", "Shibboleth sent you a gift",
             "Shibboleth gifted you WARDOGS on Steam"),
        ]:
            sender(conn, addr, name, "service", "heads_up",
                   {"messages": 5, "opened": 5, "why": "account you have; one-way"})
            tid = f"t-{name.lower().replace(' ', '')}"
            thread(conn, tid, subj, ago(hours=6), addr)
            message(conn, tid, addr, name, subj, summary, ago(hours=6),
                    email_type="notice",
                    extracted={"email_type": "notice", "summary": summary})

        # ---- on its way ----
        sender(conn, "transaction@etsy.com", "Etsy", "service", "on_its_way",
               {"messages": 4, "opened": 4})
        thread(conn, "t-etsy", "Your order has shipped", ago(1), "transaction@etsy.com")
        m = message(conn, "t-etsy", "transaction@etsy.com", "Etsy poster",
                    "Your order has shipped", "Your poster shipped via UPS. Arrives Tue.",
                    ago(1), email_type="tracking",
                    extracted={"email_type": "tracking", "carrier": "UPS",
                               "status": "Shipped", "eta": "Arrives Tue"})
        conn.execute("INSERT INTO tracking (message_id, carrier, status, eta) VALUES (?, ?, ?, ?)",
                     (m, "UPS", "Shipped", "Arrives Tue"))

        sender(conn, "orders@sainly.example", "Sainly", "service", "on_its_way",
               {"messages": 2, "opened": 2})
        thread(conn, "t-sainly", "Order #3052 update", ago(2), "orders@sainly.example")
        m = message(conn, "t-sainly", "orders@sainly.example", "Sainly #3052",
                    "Order #3052 update", "Your order has shipped.", ago(2),
                    email_type="tracking",
                    extracted={"email_type": "tracking", "status": "Shipped"})
        conn.execute("INSERT INTO tracking (message_id, status) VALUES (?, ?)",
                     (m, "Shipped"))

        # ---- money ----
        sender(conn, "orders@suithub.example", "Suit Hub", "service", "money",
               {"messages": 3, "opened": 3})
        thread(conn, "t-suithub", "Receipt", ago(2), "orders@suithub.example")
        m = message(conn, "t-suithub", "orders@suithub.example", "Suit Hub",
                    "Receipt for your order",
                    "Cummerbund, pocket square. Total $84.98.", ago(2),
                    email_type="receipt",
                    extracted={"email_type": "receipt", "merchant": "Suit Hub",
                               "amount": "84.98"})
        conn.execute("INSERT INTO money (message_id, merchant, amount_cents, source, tag) VALUES (?, ?, ?, ?, ?)",
                     (m, "Suit Hub · cummerbund, pocket square", 8498, None, "wedding"))

        for addr, name, merchant, cents, source, days in [
            ("service@paypal.com", "PayPal", "Blizzard · Skyborne pack", 6359, "PayPal", 3),
            ("tickets@thelot.example", "The Lot", "The Lot · Spider-Man tickets", 4400, "Sun", 4),
            ("receipts@anthropic.com", "Anthropic", "Anthropic · monthly", None, "subscription", 5),
        ]:
            sender(conn, addr, name, "service", "money", {"messages": 2, "opened": 2})
            tid = f"t-{name.lower().replace(' ', '')}"
            thread(conn, tid, "Receipt", ago(days), addr)
            m = message(conn, tid, addr, name, "Receipt", "Receipt.", ago(days),
                        email_type="receipt", extracted={"email_type": "receipt"})
            conn.execute("INSERT INTO money (message_id, merchant, amount_cents, source) VALUES (?, ?, ?, ?)",
                         (m, merchant, cents, source))

        # ---- feeds / lanes ----
        sender(conn, "digest@rotoreels.example", "RotoReels", "feed", "lane:Baseball",
               {"messages": 5, "opened": 5, "why": "opened 5 of 5"})
        thread(conn, "t-roto", "AL free agents", ago(hours=5), "digest@rotoreels.example")
        message(conn, "t-roto", "digest@rotoreels.example", "RotoReels",
                "RotoReels: 3 confirmed AL free agents, Clarke Schmidt reported",
                "Three AL free agents confirmed ahead of the deadline\n"
                "Clarke Schmidt reportedly drawing interest from four clubs\n"
                "Expansion draft salary rules: what changes for keeper leagues",
                ago(hours=5), email_type="digest")

        sender(conn, "jobs-noreply@linkedin.com", "LinkedIn Jobs", "feed", "lane:Jobs",
               {"messages": 22, "opened": 6,
                "why": "you open partner and alliances titles only"})
        thread(conn, "t-li", "Jobs for you", ago(hours=7), "jobs-noreply@linkedin.com")
        message(conn, "t-li", "jobs-noreply@linkedin.com", "LinkedIn Jobs",
                "1 strong fit: Figma, Strategic Partner Manager (80)",
                "Strategic Partner Manager\nFigma · San Francisco, CA (Hybrid)\n\n"
                "Head of Alliances\nVercel · Remote",
                ago(hours=7), email_type="digest")

        sender(conn, "alerts@realtor.com", "Realtor.com", "feed", "lane:Homes",
               {"messages": 28, "opened": 1, "why": "opened 1 of 28 · 4 a day merged into 1"})
        sender(conn, "daily@zillow.com", "Zillow", "feed", "lane:Homes",
               {"messages": 14, "opened": 2, "why": "saved searches: La Jolla, Bay Ho, Baltimore"})
        homes_mail = [
            ("alerts@realtor.com", "Realtor.com", "7 new homes in La Jolla",
             "New listing\n3288 Via Alicante, La Jolla\n$959,000\n2 bds · 2 ba · 1,137 sqft\n\n"
             "New listing\n5527 Caminito Herminia, La Jolla\n$1,199,000\n3 bds · 2 ba · 1,542 sqft",
             False),
            ("alerts@realtor.com", "Realtor.com", "20 price drops in La Jolla",
             "Price drop\n404 Bonair St, La Jolla\n$1,625,000 · was $1,685,000 · -$60,000\n"
             "3 bds · 2 ba · 1,252 sqft\n\n"
             "Price drop\n7344 Fay Ave, La Jolla\n$2,150,000 · reduced\n4 bds · 3 ba · 2,410 sqft",
             False),
            ("alerts@realtor.com", "Realtor.com", "Open houses this weekend in La Jolla",
             "Open house Sat 1-4\n1230 Silverado St, La Jolla\n$1,395,000\n2 bds · 2 ba · 1,201 sqft",
             False),
            ("alerts@realtor.com", "Realtor.com", "New rentals in La Jolla",
             "For rent\n909 Coast Blvd, La Jolla\n$4,200\n1 bds · 1 ba · 748 sqft",
             False),
            ("daily@zillow.com", "Zillow", "New in Bay Ho: canyon view lot",
             "New listing\n4560 Mount Hubbard Ave, San Diego\n$1,050,000\n"
             "3 bds · 2 ba · 1,344 sqft\nCanyon view lot, quiet street",
             True),
            ("daily@zillow.com", "Zillow", "1822 Belt St, Baltimore and 3 more",
             "New listing\n1822 Belt St, Baltimore\n$550,000\n4 bds · 4 ba · 1,878 sqft\n\n"
             "New listing\n3288 Via Alicante, La Jolla\n$959,000\n2 bds · 2 ba · 1,137 sqft",
             False),
        ]
        for i, (addr, name, subj, body, opened) in enumerate(homes_mail):
            tid = f"t-homes{i}"
            thread(conn, tid, subj, ago(hours=4), addr)
            message(conn, tid, addr, name, subj, body, ago(hours=4),
                    email_type="digest", opened=opened,
                    unsub=f"<https://{addr.split('@')[-1]}/unsub>",
                    unsub_post="List-Unsubscribe=One-Click")

        sender(conn, "digest@claude.com", "Claude Code weekly", "feed", "lane:Reads",
               {"messages": 4, "opened": 4})
        sender(conn, "letters@throbak.example", "ThroBak", "feed", "lane:Reads",
               {"messages": 4, "opened": 2})
        for i, (addr, subj) in enumerate([
            ("digest@claude.com", "Claude Code weekly: what shipped"),
            ("letters@throbak.example", "ThroBak #071"),
            ("letters@throbak.example", "F&M picked a mascot"),
        ]):
            tid = f"t-reads{i}"
            thread(conn, tid, subj, ago(hours=3), addr)
            message(conn, tid, addr, addr.split("@")[-1], subj, "Newsletter.",
                    ago(hours=3), email_type="digest", opened=False)

        # ---- self: notes + bots ----
        sender(conn, ME, "You", "notes", "notebook",
               {"kind": "self",
                "bot_subjects": {"ai trader digest #": "lane:Markets",
                                 "rolecall #": "lane:Jobs"}})
        for i, (subj, days) in enumerate([
            ("unBoxed 2026 coverage schedule", 2),
            ("Raidos art files, full res", 4),
            ("Monolith emblem", 5),
        ]):
            tid = f"t-note{i}"
            thread(conn, tid, subj, ago(days), None, last_from_me=1)
            message(conn, tid, ME, "Sandy Welsch", subj, "Note to self.",
                    ago(days), to=[ME], from_me=True)
        thread(conn, "t-trader", "AI Trader Digest 9/19", ago(hours=9), None, last_from_me=1)
        message(conn, "t-trader", ME, "Sandy Welsch", "AI Trader Digest 9/19",
                "Flat, 0 positions since Wednesday's clear-out. $944.77.",
                ago(hours=9), to=[ME], from_me=True)

        # ---- promotions (19 today) ----
        promo_evidence = {"messages": 11, "opened": 0,
                          "why": "opened 0 of 11 in 30 days", "unsub_suggest": True}
        sender(conn, "deals@fanatics.com", "Fanatics", "promo", "promotions", promo_evidence, confirmed=1)
        sender(conn, "offers@uber.com", "Uber", "promo", "promotions",
               {"messages": 8, "opened": 0, "why": "opened 0 of 8", "unsub_suggest": True}, confirmed=1)
        sender(conn, "hello@marinelayer.com", "Marine Layer", "promo", "promotions",
               {"messages": 6, "opened": 0, "why": "opened 0 of 6", "unsub_suggest": True}, confirmed=1)
        sender(conn, "style@bonobos.com", "Bonobos Offers", "promo", "promotions",
               {"messages": 9, "opened": 1}, confirmed=1)
        promo_unsub = {
            "deals@fanatics.com": ("<https://fanatics.example/oneclick>", "List-Unsubscribe=One-Click"),
            "offers@uber.com": ("<mailto:unsubscribe@uber.example>", ""),
            "hello@marinelayer.com": ("<https://marinelayer.example/prefs>", ""),
            "style@bonobos.com": ("<https://bonobos.example/oneclick>", "List-Unsubscribe=One-Click"),
        }
        promos = list(promo_unsub)
        for i in range(19):
            addr = promos[i % len(promos)]
            u, up = promo_unsub[addr]
            tid = f"t-promo{i}"
            thread(conn, tid, f"Sale {i}", ago(hours=2 + i % 8), addr)
            message(conn, tid, addr, addr.split("@")[0].title(), f"Big sale {i}",
                    "Marketing.", ago(hours=2 + i % 8), email_type="offer",
                    opened=False, unsub=u, unsub_post=up)

        # ---- new senders (unconfirmed guesses, this week) ----
        for addr, name, tier, why in [
            ("noreply@huggingface.co", "Hugging Face", "service",
             "First email: confirm your address · guessed Service"),
            ("accounts@unity3d.com", "Unity ID", "service",
             "First email: verification code · guessed Service"),
            ("team@adcreative.ai", "AdCreative.ai", "promo",
             "First email: welcome + 40% off · guessed Promo"),
        ]:
            sender(conn, addr, name, tier,
                   "heads_up" if tier == "service" else "promotions",
                   {"messages": 1, "opened": 0, "why": why}, confirmed=0)
            tid = f"t-new-{name.split()[0].lower()}"
            thread(conn, tid, "Welcome", ago(2), addr)
            message(conn, tid, addr, name, "Welcome", "Welcome!", ago(2),
                    email_type="notice" if tier == "service" else "offer",
                    extracted={"email_type": "notice", "summary": ""}, opened=False)

    # Build today's lane digests through the real parsers (no LLM needed
    # for the fixture senders).
    from app import digests
    with db.session() as conn:
        built = digests.build_today(conn)
    print(f"Seeded demo db at {config.DB_PATH} ({built} lane digests built)")


if __name__ == "__main__":
    main()
