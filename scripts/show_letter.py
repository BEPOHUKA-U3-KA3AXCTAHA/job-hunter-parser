"""Print stored outreach letter body for a given decision maker.

Usage:
  python scripts/show_letter.py            # list all 26 contacts
  python scripts/show_letter.py jack       # body for Jack Conte
  python scripts/show_letter.py "tom sosn" # case-insensitive partial match

Pipe through xclip to copy to clipboard:
  python scripts/show_letter.py jack | xclip -selection clipboard
"""
import sqlite3
import sys

DB = "jhp.db"


def main() -> int:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    if len(sys.argv) < 2:
        for r in c.execute("""
            SELECT dm.full_name, co.name AS comp, j.title
            FROM messages m
            JOIN decision_makers dm ON m.decision_maker_id=dm.id
            JOIN job_postings j ON m.job_posting_id=j.id
            JOIN companies co ON j.company_id=co.id
            ORDER BY co.name, dm.full_name
        """):
            print(f"{r['full_name']:25} {r['comp']:14} {r['title'][:50]}")
        return 0

    needle = sys.argv[1].lower()
    rows = list(c.execute("""
        SELECT dm.full_name, co.name AS comp, j.title, m.body
        FROM messages m
        JOIN decision_makers dm ON m.decision_maker_id=dm.id
        JOIN job_postings j ON m.job_posting_id=j.id
        JOIN companies co ON j.company_id=co.id
        WHERE LOWER(dm.full_name) LIKE ?
        ORDER BY co.name, j.title
    """, (f"%{needle}%",)))

    if not rows:
        print(f"no matches for: {needle}", file=sys.stderr)
        return 1

    for r in rows:
        print(f"=== {r['full_name']} ({r['comp']} - {r['title']}) ===")
        print()
        print(r["body"])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
