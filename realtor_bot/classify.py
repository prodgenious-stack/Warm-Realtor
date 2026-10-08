"""Free keyword pre-filter first; Gemini (Vertex AI) only for the ambiguous cases."""
import logging
import re

from google import genai
from google.genai import types
from pydantic import BaseModel

from .config import Config

log = logging.getLogger(__name__)

BROKERAGES = [
    "keller williams", "exp realty", "compass", "re/max", "remax", "coldwell banker",
    "century 21", "berkshire hathaway", "sotheby's international realty", "sothebys realty",
    "douglas elliman", "redfin", "real broker", "fathom realty", "howard hanna",
    "better homes and gardens real estate", "weichert", "corcoran", "the agency",
    "era real estate", "united real estate", "lpt realty", "epique realty", "side inc",
]

_REALTOR = re.compile(
    r"realtor|real\s*estate\s*(agent|advisor|broker|salesperson|professional)|"
    r"\bbroker(age)?\b|\blisting agent\b|\bbuyer'?s agent\b|\bdre\s*#?\s*\d|"
    r"\blic(ense)?\.?\s*#?\s*\d|licensed (real estate )?(agent|salesperson)|"
    r"homes? for sale|"
    + "|".join(re.escape(b) for b in BROKERAGES),
    re.I,
)
# Adjacent professions that are NOT realtors (lenders target realtors, but aren't leads).
_NOT_REALTOR = re.compile(r"loan officer|mortgage|\bnmls\b|lender|title (agent|company)", re.I)

_REAL_ESTATE_ANY = re.compile(
    r"real\s*estate|realtor|listing|broker|home ?buyer|seller|mortgage|property|properties|"
    r"\bhomes?\b|\bagents?\b|open house|closing|escrow|\bmls\b",
    re.I,
)
_AIMED_AT_AGENTS = re.compile(
    r"(realtors?|real estate agents?|\bagents\b|brokers?|brokerages?).{0,80}"
    r"(leads?|listings?|deals|gci|crm|commission|close more|grow your|your business|sphere)|"
    r"(leads?|listings?|gci|crm|commission).{0,80}(realtors?|real estate agents?|\bagents\b)",
    re.I | re.S,
)


def profile_keyword_verdict(text: str) -> bool | None:
    """True/False when keywords decide it, None when Gemini should look."""
    realtor = bool(_REALTOR.search(text))
    other = bool(_NOT_REALTOR.search(text))
    if realtor and not other:
        return True
    if other and not realtor:
        return False
    return None


def ad_keyword_verdict(text: str) -> bool | None:
    if _AIMED_AT_AGENTS.search(text):
        return True
    if not _REAL_ESTATE_ANY.search(text):
        return False
    return None


def liker_looks_realtor(username: str, display_name: str) -> bool:
    text = f"{username} {display_name}"
    return bool(re.search(r"realtor|realty|homes|realestate|real estate|broker|properties", text, re.I))


def find_brokerage(text: str) -> str:
    low = text.lower()
    for b in BROKERAGES:
        if b in low:
            return b.title()
    return ""


# ---------- Gemini ----------

class AdVerdict(BaseModel):
    targets_realtors: bool
    reason: str


class LikerPicks(BaseModel):
    likely_realtors: list[str]


class ProfileVerdict(BaseModel):
    is_realtor: bool
    brokerage: str
    city: str
    category: str
    reason: str


class LeadDetails(BaseModel):
    username: str
    brokerage: str
    city: str
    category: str


class LeadDetailsBatch(BaseModel):
    leads: list[LeadDetails]


class Gemini:
    def __init__(self, cfg: Config):
        if cfg.vertex_api_key:
            self.client = genai.Client(vertexai=True, api_key=cfg.vertex_api_key)
        else:
            if not cfg.gcp_project:
                raise SystemExit("Set GOOGLE_CLOUD_PROJECT (or VERTEX_API_KEY) in .env")
            self.client = genai.Client(vertexai=True, project=cfg.gcp_project, location=cfg.gcp_location)
        self.model = cfg.gemini_model
        self.calls = 0

    def _ask(self, prompt: str, schema: type[BaseModel]):
        self.calls += 1
        try:
            resp = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0,
                ),
            )
            return resp.parsed
        except Exception as e:  # network/quota errors shouldn't kill the session
            log.warning("Gemini call failed: %s", e)
            return None

    def ad_targets_realtors(self, ad_text: str) -> AdVerdict | None:
        return self._ask(
            "You review Instagram ads. Decide whether this ad is aimed at real estate agents/realtors "
            "as the customer (e.g. lead gen, CRMs, coaching, brokerage recruiting, marketing for agents). "
            "Ads aimed at home buyers/sellers are NOT targeting realtors.\n\nAD TEXT:\n" + ad_text[:2500],
            AdVerdict,
        )

    def pick_likely_realtors(self, likers: list[tuple[str, str]]) -> list[str]:
        if not likers:
            return []
        rows = "\n".join(f"{u} | {n}" for u, n in likers)
        res = self._ask(
            "These Instagram accounts liked an ad aimed at real estate agents, so many are realtors. "
            "From username and display name alone, return the usernames that plausibly belong to a "
            "realtor/real estate agent/broker. Skip obvious non-agents (brands, memes, unrelated "
            "businesses).\n\nusername | display name\n" + rows,
            LikerPicks,
        )
        return res.likely_realtors if res else []

    def classify_profile(self, username: str, profile_text: str) -> ProfileVerdict | None:
        return self._ask(
            "Is this Instagram account a licensed real estate agent, realtor, or real estate broker? "
            "Mortgage lenders, title companies, and home buyers are NOT. Also extract brokerage, city/market, "
            "and a short category (e.g. 'Real estate agent', 'Broker/owner', 'Team lead'); use '' if unknown."
            f"\n\nUSERNAME: {username}\nPROFILE:\n{profile_text[:2500]}",
            ProfileVerdict,
        )

    def extract_details(self, leads: list[tuple[str, str]]) -> list[LeadDetails]:
        blocks = "\n\n".join(f"### {u}\n{t[:1200]}" for u, t in leads)
        res = self._ask(
            "For each realtor Instagram profile below, extract brokerage, city/market, and a short "
            "category (e.g. 'Real estate agent', 'Broker/owner', 'Team lead'). Use '' if unknown. "
            "Return one entry per username.\n\n" + blocks,
            LeadDetailsBatch,
        )
        return res.leads if res else []
