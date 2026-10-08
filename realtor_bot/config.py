import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    return int(os.getenv(name) or default)


@dataclass(frozen=True)
class Config:
    gcp_project: str = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    gcp_location: str = os.getenv("GOOGLE_CLOUD_LOCATION") or "us-central1"
    vertex_api_key: str = os.getenv("VERTEX_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"

    max_visits_per_day: int = _int("MAX_PROFILE_VISITS_PER_DAY", 50)
    max_visits_per_ad: int = _int("MAX_VISITS_PER_AD", 15)
    max_likers_per_ad: int = _int("MAX_LIKERS_PER_AD", 60)
    session_minutes: int = _int("SESSION_MINUTES", 25)

    browser_profile_dir: Path = ROOT / "browser_profile"
    db_path: Path = ROOT / "data" / "realtors.db"
    csv_path: Path = ROOT / "data" / "realtor_leads.csv"
