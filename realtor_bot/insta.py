"""Instagram web page interactions. Selectors are best-effort: Instagram changes its DOM often."""
import hashlib
import logging
import random
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, TimeoutError as PWTimeout

from . import human

log = logging.getLogger(__name__)

HOME = "https://www.instagram.com/"
USER_HREF = re.compile(r"^/([A-Za-z0-9._]{1,30})/$")
NON_USER_PATHS = {"explore", "reels", "direct", "accounts", "stories", "p", "reel", "about", "legal"}

BLOCK_URL_PARTS = ("/challenge", "/checkpoint", "/accounts/suspended", "/accounts/disabled")
BLOCK_TEXT = re.compile(
    r"try again later|action blocked|we restrict certain activity|suspicious activity|"
    r"confirm it'?s you|help us confirm|your account has been (suspended|disabled)",
    re.I,
)
LIKES_TEXT = re.compile(r"^\s*[\d.,]+\s*[KkMm]?\s+likes?\s*$", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
URLISH = re.compile(r"\b(?:https?://)?(?:[\w-]+\.)+(?:com|net|org|co|io|me|ee|bio|link|homes|realty|realtor)(?:/\S*)?", re.I)


class Blocked(Exception):
    """Instagram showed a challenge / block / logout. Stop immediately."""


@dataclass
class Ad:
    key: str
    advertiser: str
    text: str


@dataclass
class Profile:
    username: str
    full_name: str
    followers: str
    header_text: str
    link_in_bio: str
    email: str
    phone: str


def _username_from_href(href: str | None) -> str | None:
    m = USER_HREF.match(href or "")
    if m and m.group(1).lower() not in NON_USER_PATHS:
        return m.group(1)
    return None


def check_blocked(page: Page) -> None:
    if any(p in page.url for p in BLOCK_URL_PARTS):
        raise Blocked(page.url)
    if "/accounts/login" in page.url:
        raise Blocked("logged out")
    for d in page.locator('div[role="dialog"]').all():
        try:
            if BLOCK_TEXT.search(d.inner_text(timeout=1000)):
                raise Blocked("block/challenge dialog shown")
        except PWTimeout:
            pass


def ensure_logged_in(page: Page) -> None:
    page.goto(HOME, wait_until="domcontentloaded")
    human.pause(3, 5)
    if "/accounts/login" in page.url or page.locator('input[name="username"]').count():
        input("\n>>> Log in to Instagram in the browser window, then press Enter here... ")
        page.goto(HOME, wait_until="domcontentloaded")
        human.pause(3, 5)
    check_blocked(page)


def visible_articles(page: Page):
    """Feed posts currently on screen."""
    height = page.evaluate("window.innerHeight")
    for art in page.locator("article").all():
        box = art.bounding_box()
        if box and box["y"] < height and box["y"] + box["height"] > 0:
            yield art


def read_ad(article) -> Ad | None:
    """Return an Ad if this feed post is sponsored, else None."""
    try:
        text = article.inner_text(timeout=2000)
    except PWTimeout:
        return None
    head = "\n".join(text.splitlines()[:6])
    if not re.search(r"\bSponsored\b", head):
        return None

    advertiser = ""
    for a in article.locator('a[href^="/"]').all()[:8]:
        advertiser = _username_from_href(a.get_attribute("href")) or ""
        if advertiser:
            break
    key_src = advertiser + "|" + text[:400]
    key = hashlib.sha1(key_src.encode()).hexdigest()[:16]
    return Ad(key=key, advertiser=advertiser, text=text)


def open_likers(page: Page, article) -> bool:
    """Click the ad's like count to open the likers list. False if likes are hidden."""
    candidates = [
        article.locator('a[href$="/liked_by/"]'),
        article.get_by_text(LIKES_TEXT),
        article.get_by_text(re.compile(r"^\s*others\s*$", re.I)),
    ]
    for loc in candidates:
        if loc.count() and loc.first.is_visible():
            human.click(page, loc.first)
            try:
                page.locator('div[role="dialog"] a[href^="/"], main a[href^="/"]').first.wait_for(timeout=8000)
                human.short()
                return True
            except PWTimeout:
                return False
    return False


_ROWS_JS = """
(root) => {
  const out = [], seen = new Set();
  for (const a of root.querySelectorAll('a[href^="/"]')) {
    const m = (a.getAttribute('href') || '').match(/^\\/([A-Za-z0-9._]{1,30})\\/$/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    let row = a, text = '';
    for (let i = 0; i < 6 && row.parentElement; i++) {
      row = row.parentElement;
      text = row.innerText || '';
      if (text.split('\\n').filter(s => s.trim()).length >= 2) break;
    }
    out.push({username: m[1], text});
  }
  return out;
}
"""


def collect_likers(page: Page, limit: int) -> list[tuple[str, str]]:
    """Scroll the likers list and return (username, display_name) pairs."""
    dialog = page.locator('div[role="dialog"]')
    root = dialog.last if dialog.count() else page.locator("main").first
    found: dict[str, str] = {}
    stale = 0
    while len(found) < limit and stale < 4:
        before = len(found)
        for row in root.evaluate(_ROWS_JS):
            u = row["username"]
            if u in found or u.lower() in NON_USER_PATHS:
                continue
            lines = [s.strip() for s in row["text"].splitlines() if s.strip()]
            name = next(
                (s for s in lines if s != u and s.lower() not in {"follow", "following", "requested", "remove", "·"}),
                "",
            )
            found[u] = name
        stale = stale + 1 if len(found) == before else 0
        human.move_to(page, root)
        human.wheel(page, 400)
        human.pause(1.2, 2.8)
    return list(found.items())[:limit]


def close_likers(page: Page, feed_url: str) -> None:
    if page.locator('div[role="dialog"]').count():
        page.keyboard.press("Escape")
        human.short()
    if page.url.rstrip("/") != feed_url.rstrip("/"):
        page.go_back(wait_until="domcontentloaded")
        human.pause(1.5, 3)


def scrape_profile(page: Page, username: str) -> Profile | None:
    page.goto(f"{HOME}{username}/", wait_until="domcontentloaded")
    human.pause(2.5, 5)
    check_blocked(page)
    if page.get_by_text("Sorry, this page isn't available").count():
        return None

    title = page.title()
    og = page.locator('meta[property="og:description"]')
    og_text = og.first.get_attribute("content") if og.count() else ""
    header = page.locator("main header")
    try:
        header_text = header.first.inner_text(timeout=5000) if header.count() else ""
    except PWTimeout:
        header_text = ""

    nm = re.match(r"^(.*?)\s*\(@", title)
    full_name = nm.group(1) if nm else ""
    fm = re.search(r"([\d.,]+\s*[KkMm]?)\s+Followers", og_text or "")

    link = ""
    for a in page.locator('main header a[href*="l.instagram.com"]').all():
        q = parse_qs(urlparse(a.get_attribute("href") or "").query)
        if q.get("u"):
            link = q["u"][0]
            break
    if not link:
        m = URLISH.search(header_text)
        link = m.group(0) if m else ""

    human.read()
    if random.random() < 0.4:
        human.wheel(page, 300)
        human.short()

    email = EMAIL.search(header_text)
    phone = PHONE.search(header_text)
    return Profile(
        username=username,
        full_name=full_name,
        followers=fm.group(1).strip() if fm else "",
        header_text=header_text,
        link_in_bio=link,
        email=email.group(0) if email else "",
        phone=phone.group(0) if phone else "",
    )
