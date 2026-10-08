"""SQLite state (dedupe, daily caps, resume) + CSV export of leads."""
import csv
import sqlite3
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS ads (
    ad_key TEXT PRIMARY KEY, advertiser TEXT, targets_realtors INTEGER,
    method TEXT, reason TEXT, seen_at TEXT
);
CREATE TABLE IF NOT EXISTS advertisers (
    username TEXT PRIMARY KEY, targets_realtors INTEGER, checked_at TEXT
);
CREATE TABLE IF NOT EXISTS profiles (
    username TEXT PRIMARY KEY,
    status TEXT,              -- lead | not_realtor | screened_out | unavailable
    full_name TEXT, category TEXT, followers TEXT, bio TEXT, link_in_bio TEXT,
    brokerage TEXT, city TEXT, email TEXT, phone TEXT,
    method TEXT, source_advertiser TEXT, found_at TEXT, details_done INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS visits (day TEXT, username TEXT);
"""

CSV_COLUMNS = [
    "username", "profile_url", "full_name", "category", "followers", "bio", "link_in_bio",
    "brokerage", "city", "email", "phone", "source_advertiser", "found_at",
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ads / advertisers
    def ad_seen(self, ad_key: str) -> bool:
        return self.db.execute("SELECT 1 FROM ads WHERE ad_key=?", (ad_key,)).fetchone() is not None

    def save_ad(self, ad_key, advertiser, targets, method, reason="") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO ads VALUES (?,?,?,?,?,?)",
            (ad_key, advertiser, int(targets), method, reason, _now()),
        )
        if advertiser:
            self.db.execute(
                "INSERT OR REPLACE INTO advertisers VALUES (?,?,?)", (advertiser, int(targets), _now())
            )
        self.db.commit()

    def advertiser_verdict(self, advertiser: str) -> bool | None:
        row = self.db.execute(
            "SELECT targets_realtors FROM advertisers WHERE username=?", (advertiser,)
        ).fetchone()
        return None if row is None else bool(row[0])

    # profiles
    def known_profile(self, username: str) -> bool:
        return self.db.execute("SELECT 1 FROM profiles WHERE username=?", (username,)).fetchone() is not None

    def save_profile(self, username: str, status: str, **fields) -> None:
        cols = ["username", "status", "found_at", *fields.keys()]
        vals = [username, status, _now(), *fields.values()]
        self.db.execute(
            f"INSERT OR REPLACE INTO profiles ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals
        )
        self.db.commit()

    def leads_missing_details(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT username, full_name, bio, link_in_bio FROM profiles WHERE status='lead' AND details_done=0"
        ).fetchall()

    def set_details(self, username: str, brokerage: str, city: str, category: str) -> None:
        self.db.execute(
            "UPDATE profiles SET brokerage=COALESCE(NULLIF(?, ''), brokerage), city=?, category=?, "
            "details_done=1 WHERE username=?",
            (brokerage, city, category, username),
        )
        self.db.commit()

    # daily cap
    def record_visit(self, username: str) -> None:
        self.db.execute("INSERT INTO visits VALUES (?,?)", (date.today().isoformat(), username))
        self.db.commit()

    def visits_today(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM visits WHERE day=?", (date.today().isoformat(),)
        ).fetchone()[0]

    def lead_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM profiles WHERE status='lead'").fetchone()[0]

    def export_csv(self, path: Path) -> int:
        rows = self.db.execute("SELECT * FROM profiles WHERE status='lead' ORDER BY found_at").fetchall()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            w.writeheader()
            for r in rows:
                d = {k: r[k] for k in r.keys() if k in CSV_COLUMNS}
                d["profile_url"] = f"https://www.instagram.com/{r['username']}/"
                w.writerow(d)
        return len(rows)
