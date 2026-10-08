"""Scroll the feed, find ads aimed at realtors, collect realtor likers into a CSV."""
import logging
import time

from playwright.sync_api import sync_playwright

from . import classify, human, insta
from .config import Config
from .store import Store

log = logging.getLogger("realtor_bot")


class Session:
    def __init__(self, cfg: Config, store: Store, gemini: classify.Gemini, feed, context):
        self.cfg, self.store, self.gemini = cfg, store, gemini
        self.feed, self.context = feed, context
        self.profile_tab = None

    def visits_left(self) -> int:
        return self.cfg.max_visits_per_day - self.store.visits_today()

    def ad_targets_realtors(self, ad: insta.Ad) -> tuple[bool, str, str]:
        known = self.store.advertiser_verdict(ad.advertiser) if ad.advertiser else None
        if known is not None:
            return known, "advertiser_memory", ""
        kw = classify.ad_keyword_verdict(ad.text)
        if kw is not None:
            return kw, "keywords", ""
        verdict = self.gemini.ad_targets_realtors(ad.text)
        if verdict is None:
            return False, "gemini_failed", ""
        return verdict.targets_realtors, "gemini", verdict.reason

    def handle_ad(self, article, ad: insta.Ad) -> None:
        targets, method, reason = self.ad_targets_realtors(ad)
        self.store.save_ad(ad.key, ad.advertiser, targets, method, reason)
        log.info("Ad @%s -> %s (%s)", ad.advertiser or "?", "REALTOR AD" if targets else "skip", method)
        if not targets:
            return

        human.read()
        feed_url = self.feed.url
        if not insta.open_likers(self.feed, article):
            log.info("  Likes hidden or not clickable, skipping")
            return
        likers = insta.collect_likers(self.feed, self.cfg.max_likers_per_ad)
        insta.close_likers(self.feed, feed_url)
        log.info("  Collected %d likers", len(likers))

        picks = self.screen_likers(likers)
        for username in picks[: min(self.cfg.max_visits_per_ad, self.visits_left())]:
            self.visit_profile(username, ad.advertiser)
            human.between_profiles()

    def screen_likers(self, likers: list[tuple[str, str]]) -> list[str]:
        new = [(u, n) for u, n in likers if not self.store.known_profile(u)]
        strong = [u for u, n in new if classify.liker_looks_realtor(u, n)]
        rest = [(u, n) for u, n in new if u not in strong]
        maybe = set(self.gemini.pick_likely_realtors(rest)) if rest else set()
        picks = strong + [u for u, _ in rest if u in maybe]
        for u, n in rest:
            if u not in maybe:
                self.store.save_profile(u, "screened_out", full_name=n, method="liker_screen")
        log.info("  Screening: %d new, %d keyword picks, %d Gemini picks", len(new), len(strong), len(picks) - len(strong))
        return picks

    def visit_profile(self, username: str, source_advertiser: str) -> None:
        if self.profile_tab is None:
            self.profile_tab = self.context.new_page()
        self.store.record_visit(username)
        prof = insta.scrape_profile(self.profile_tab, username)
        if prof is None:
            self.store.save_profile(username, "unavailable", source_advertiser=source_advertiser)
            return

        text = f"{prof.full_name}\n{prof.header_text}"
        fields = dict(
            full_name=prof.full_name, followers=prof.followers, bio=prof.header_text,
            link_in_bio=prof.link_in_bio, email=prof.email, phone=prof.phone,
            source_advertiser=source_advertiser,
        )
        kw = classify.profile_keyword_verdict(text)
        if kw is True:
            self.store.save_profile(username, "lead", method="keywords", brokerage=classify.find_brokerage(text), **fields)
        elif kw is False:
            self.store.save_profile(username, "not_realtor", method="keywords", **fields)
        else:
            v = self.gemini.classify_profile(username, text)
            if v and v.is_realtor:
                self.store.save_profile(
                    username, "lead", method="gemini", brokerage=v.brokerage, city=v.city,
                    category=v.category, details_done=1, **fields,
                )
            else:
                self.store.save_profile(username, "not_realtor", method="gemini", **fields)

        is_lead = self.store.db.execute(
            "SELECT status FROM profiles WHERE username=?", (username,)
        ).fetchone()[0] == "lead"
        log.info("    @%s -> %s", username, "LEAD" if is_lead else "not a realtor")
        if is_lead:
            self.store.export_csv(self.cfg.csv_path)

    def fill_details(self) -> None:
        """One batched Gemini call per 25 keyword-matched leads for brokerage/city/category."""
        rows = self.store.leads_missing_details()
        for i in range(0, len(rows), 25):
            chunk = rows[i : i + 25]
            got = self.gemini.extract_details(
                [(r["username"], f"{r['full_name']}\n{r['bio']}\n{r['link_in_bio']}") for r in chunk]
            )
            for d in got:
                self.store.set_details(d.username, d.brokerage, d.city, d.category)

    def run(self) -> None:
        insta.ensure_logged_in(self.feed)
        vp = self.feed.evaluate("[window.innerWidth, window.innerHeight]")
        self.feed.mouse.move(vp[0] / 2, vp[1] / 2, steps=15)

        seen: set[str] = set()
        end = time.time() + self.cfg.session_minutes * 60
        while time.time() < end:
            if self.visits_left() <= 0:
                log.info("Daily profile-visit cap reached (%d)", self.cfg.max_visits_per_day)
                break
            insta.check_blocked(self.feed)
            for article in insta.visible_articles(self.feed):
                ad = insta.read_ad(article)
                if not ad or ad.key in seen:
                    continue
                seen.add(ad.key)
                if self.store.ad_seen(ad.key):
                    continue
                self.handle_ad(article, ad)
                break  # the DOM may have shifted; re-scan after handling
            human.scroll_feed(self.feed)
            human.pause(1.5, 4.5)
            human.maybe_distracted()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = Config()
    store = Store(cfg.db_path)
    gemini = classify.Gemini(cfg)

    with sync_playwright() as pw:
        launch = dict(user_data_dir=str(cfg.browser_profile_dir), headless=False, viewport=None)
        try:
            context = pw.chromium.launch_persistent_context(channel="chrome", **launch)
        except Exception:
            log.info("Google Chrome not found, using Playwright's Chromium")
            context = pw.chromium.launch_persistent_context(**launch)

        feed = context.pages[0] if context.pages else context.new_page()
        session = Session(cfg, store, gemini, feed, context)
        try:
            session.run()
        except insta.Blocked as e:
            log.warning("STOPPED: Instagram challenge/block detected (%s). Resolve it manually and rest the account.", e)
        except KeyboardInterrupt:
            log.info("Stopped by user")
        finally:
            session.fill_details()
            n = store.export_csv(cfg.csv_path)
            log.info("Done. %d leads total in %s | %d visits today | %d Gemini calls this session",
                     n, cfg.csv_path, store.visits_today(), gemini.calls)
            context.close()


if __name__ == "__main__":
    main()
