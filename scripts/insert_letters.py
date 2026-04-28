"""Insert hand-written outreach letters into the `applies` table.

Idempotent: re-runs are safe — UNIQUE(job_posting_id, dm_id, attempt_no=1)
prevents duplicates, so re-running just skips already-inserted rows.

Usage:
    .venv/bin/python3 scripts/insert_letters.py

Run from repo root.
"""
import json
import sqlite3
from datetime import datetime
from uuid import uuid4

DB = "jhp.db"
CV_URL = "https://drive.google.com/file/d/1YaFIGZd-S0P5aAkpWlSoa7wx0FL8hbPz/view"

# Per (company, title_like): body template using {greeting} and {job_url}.
# Honest, slightly self-deprecating tone, no em-dashes.
TEMPLATES = [
    ("Patreon", "%Identity%",
"""{greeting},

Saw the SE Identity & Access role on RemoteOK and honestly it's the kind of work I'd love to do. My undergrad is in Computer Security, so this is one of the rare cases where the degree is actually relevant. On the work side I'm Tech Lead at Intelligent Solutions (4 years Python + Rust backend), shipped Keycloak + JWT + OAuth flows on a market monitoring product.

I won't pretend I've done IAM at Patreon's scale - but I'd grind to ramp up, and I'd really like to be useful on a team that takes the trust model seriously. Happy to take any kind of test or pair on something.

Job: {job_url}
CV:  """ + CV_URL + """

Thanks for reading,
Sergey"""),

    ("Mercor", "Rust Developer%",
"""{greeting},

Saw the Rust Developer remote role on LinkedIn. Honest about me: 4 years Python on the backend, ~2 years Rust on a futures trading platform (Actix-web + Tokio + PyO3) where I cut the algo reaction from 2 sec to about 100 ms. That's most of what I bring.

I don't know what Mercor does with Rust day to day, but I'd really like to be on a team where I can grow into more serious systems work. Fully remote setup is honestly what I've been looking for. If my level isn't where you need it, would still appreciate a pointer on what to work on.

Job: {job_url}
CV:  """ + CV_URL + """

Thanks,
Sergey"""),

    ("Kraken", "%Rust%Core Engineering%",
"""{greeting},

Saw the Rust SE role on Kraken's Core Engineering team via LinkedIn. Kraken is one of the very few exchanges where the engineering reputation actually precedes the brand - that's why I'm writing.

About me, no spin: 4 years Python + Rust backend, currently Tech Lead at Intelligent Solutions. The Rust was a futures trading platform (Actix + Tokio + PyO3) and a small currency arbitrage service. Computer Security degree on the side. I'm not coming in claiming staff-grade, just want to be useful and learn from a strong team.

Job: {job_url}
CV:  """ + CV_URL + """

Thanks,
Sergey"""),

    ("Kraken", "%Rust Platm%",
"""{greeting},

Saw the Senior Staff Rust Platform role at Kraken on LinkedIn. Honest from the start: "Staff" is usually a higher bar than I clear today. Writing anyway because Kraken is one of the few teams I'd actively work hard to grow into.

Background: Python + Rust backend, 4 years, Tech Lead at Intelligent Solutions. Designed hexagonal architecture from scratch on a market monitoring product, was architect on a small Rust + MQTT IoT prototype (pump equipment monitoring) with Kafka/RabbitMQ event flows. If you ever consider stretchier candidates, I'd love a chance.

Job: {job_url}
CV:  """ + CV_URL + """

Thanks,
Sergey"""),

    ("Bitmex", "%Trading Technology%",
"""{greeting},

Saw the Senior Trading Tech LowLatency role on web3.career. This is genuinely close to what I've been doing - my current gig at Intelligent Solutions is a Rust algorithmic trading platform where most of my day is shaving milliseconds (Actix + Tokio for the hot path, PyO3 to talk to Python pricing logic, sub-100 ms end to end).

I don't have crypto exchange experience as such, but the engineering problems read very familiar from the FX side. Just want to be useful on a team that does this seriously. Happy to take any coding test.

Job: {job_url}
CV:  """ + CV_URL + """

Thanks for the time,
Sergey"""),

    ("Okx", "%Quant%Rust%",
"""{greeting},

Saw the Quant Developer Rust Liquidity Platform role at OKX on web3.career. Most of my recent work has been Rust on trading infrastructure (Actix + Tokio + PyO3 to Python pricing), which is rare overlap with what I've actually built.

I won't pretend I've worked at OKX scale, but I've shipped low-latency systems end to end and would honestly love to be useful on a team building one. Earlier this year I also did a freelance Crypto Portfolio Management Service backend, so the asset class isn't completely foreign.

Job: {job_url}
CV:  """ + CV_URL + """

Thanks,
Sergey"""),

    ("Okx", "%Senior%Rust%CrossPlatm%",
"""{greeting},

Saw the Senior SE Rust CrossPlatform role at OKX on web3.career. Backend Rust is my main language for the past couple of years - mostly Actix + Tokio + PyO3 on a trading platform, plus a Rust currency arbitrage microservice for transfer-chain detection.

If "cross-platform" here means heavy systems plumbing rather than UI, I'd really love to be in the conversation. If it leans the other way, just ignore this one. Honestly just looking for a team where I can grow.

Job: {job_url}
CV:  """ + CV_URL + """

Sergey"""),

    ("Bitpanda", "%Python%Asset Pricing%",
"""{greeting},

Saw the Senior SE Python role on the Asset Pricing Hedging team at Bitpanda via web3.career. Pricing and hedging is genuinely new to me - I won't pretend otherwise. What I can bring: Python backend (4 years, currently Tech Lead at Intelligent Solutions), real-time financial systems experience (a Rust + Python trading platform with sub-100 ms reaction), and a recent freelance Crypto Portfolio Management Service.

If your team would consider someone strong on the engineering side who has to learn the asset class, I'd really like to be in that conversation. Apologies if this is the wrong inbox - happy to be redirected.

Job: {job_url}
CV:  """ + CV_URL + """

Thanks,
Sergey"""),

    ("OpenYield", "%C++/Rust Developer%",
"""{greeting},

Saw the Senior C++/Rust Developer role at OpenYield on web3.career. Honest disclosure first because I'd rather not waste your time: my Rust is solid (~2 years on a futures trading platform with Actix + Tokio + PyO3, plus a currency arbitrage microservice). My C++ is mostly read-and-understand at this point, not ship-it.

If C++ is a hard requirement on day one, I'd respect that and step back. If there's any flex for someone who'd grind to ramp on C++ while carrying Rust - I'd love a chance. Fixed-income at OpenYield is the kind of thing I'd care about working on long-term.

Job: {job_url}
CV:  """ + CV_URL + """

Thanks,
Sergey"""),

    ("Shakepay", "%Platm%Engineer%",
"""{greeting},

Saw the Senior Platform Engineer role at Shakepay on web3.career. About me, no embellishment: Python + Rust backend, 4 years, currently Tech Lead at Intelligent Solutions. Platform-side experience is mostly event-driven flows (Kafka/RabbitMQ/MQTT) and being architect on a small Rust + MQTT IoT prototype for pump equipment monitoring.

No direct crypto exchange experience, so I won't pretend I'd hit the ground at full speed. But the platform problems carry over and I'd really like to be on a team where I can grow into them.

Job: {job_url}
CV:  """ + CV_URL + """

Thanks,
Sergey"""),

    ("Tastylive", "%Infrastructure Engineer%",
"""{greeting},

Saw the Senior Infrastructure Engineer role at Tastylive on LinkedIn. Honest framing: I'm not a hardcore SRE, I won't pretend I am. I'm just the kind of dev who actually likes the boring infra problems that hold everything else together.

Background is backend Python + Rust (4 years, Tech Lead at Intelligent Solutions) with a fair amount of platform-side work: Kafka/RabbitMQ event flows, a small Rust + MQTT IoT prototype, a low-latency Rust trading platform. If there's a fit at my stage, I'd love to be useful here. If not, also appreciate any feedback on the gap.

Job: {job_url}
CV:  """ + CV_URL + """

Thanks,
Sergey"""),
]

ROLE_PRI = {
    "founder": 5, "ceo": 5, "cto": 5,
    "head_of_engineering": 4, "vp_engineering": 4, "engineering_manager": 4,
    "tech_lead": 3, "recruiter": 2, "hr": 1, "other": 0,
}
DMS_PER_JOB = 3  # cover top 3 DMs per company (CEO + CTO + Founder typically)


def main() -> None:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    inserted = 0
    skipped_dupe = 0
    skipped_no_job = 0

    for comp_name, title_like, template in TEMPLATES:
        job = c.execute(
            """SELECT j.id, j.title, j.source_url FROM job_postings j
               JOIN companies co ON j.company_id=co.id
               WHERE co.name=? AND j.title LIKE ? LIMIT 1""",
            (comp_name, title_like),
        ).fetchone()
        if not job:
            print(f"SKIP {comp_name} ({title_like}): job not found")
            skipped_no_job += 1
            continue

        company = c.execute(
            "SELECT id FROM companies WHERE name=?", (comp_name,),
        ).fetchone()
        dms = c.execute(
            """SELECT id, full_name, role, contacts FROM decision_makers
               WHERE company_id=?""",
            (company["id"],),
        ).fetchall()
        if not dms:
            print(f"SKIP {comp_name}: no DMs")
            continue

        def key(d):
            cont = json.loads(d["contacts"] or "{}")
            return (-ROLE_PRI.get(d["role"], 0), -int("linkedin" in cont), d["full_name"])

        ranked = sorted(dms, key=key)[:DMS_PER_JOB]

        for dm in ranked:
            first = dm["full_name"].split()[0] if dm["full_name"] else "there"
            url = job["source_url"] or "(no link)"
            body = template.format(greeting=f"Hi {first}", job_url=url).strip()
            # safety: strip em-dashes if any leaked through templates
            body = body.replace("—", "-")

            try:
                c.execute(
                    """INSERT INTO applies
                       (id, job_posting_id, decision_maker_id, attempt_no,
                        flank, method, channel, relevance_score, status,
                        subject, body, cover_letter, form_responses, apply_url,
                        notes, generated_at, sent_at, response_at)
                       VALUES (?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?)""",
                    (
                        uuid4().hex,
                        job["id"],
                        dm["id"],
                        1,
                        "dm_outreach",          # flank
                        "hand_written",          # method
                        "linkedin",              # channel
                        80,                       # relevance_score
                        "generated",             # status
                        None,                     # subject
                        body,                     # body
                        None,                     # cover_letter
                        None,                     # form_responses (JSON, NULL ok)
                        url,                      # apply_url (target posting URL)
                        "hand-written by Sergey + Claude, motivation-first tone",
                        datetime.utcnow().isoformat(),
                        None,                     # sent_at - set when actually sent
                        None,                     # response_at
                    ),
                )
                inserted += 1
                print(f"OK   {comp_name:14} -> {dm['full_name']:25} ({len(body)}c)")
            except sqlite3.IntegrityError:
                skipped_dupe += 1
                print(f"DUPE {comp_name:14} -> {dm['full_name']:25} (already exists)")

    c.commit()
    print(f"\nInserted {inserted} applies, dupes skipped {skipped_dupe}, no-job skipped {skipped_no_job}")
    print(f"Total applies in DB: {c.execute('SELECT COUNT(*) FROM applies').fetchone()[0]}")


if __name__ == "__main__":
    main()
