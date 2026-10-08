"""Human-like pacing: uneven scrolling, dwell time, mouse movement before clicks."""
import logging
import random
import time

log = logging.getLogger(__name__)


def pause(lo: float, hi: float) -> None:
    time.sleep(random.uniform(lo, hi))


def short() -> None:
    pause(0.4, 1.3)


def read() -> None:
    """Time spent looking at a post."""
    pause(2.5, 7.0)


def between_profiles() -> None:
    pause(10, 28)


def maybe_distracted() -> None:
    """Occasional longer idle, like checking another app."""
    if random.random() < 0.05:
        secs = random.uniform(20, 75)
        log.info("Idle break for %.0fs", secs)
        time.sleep(secs)


def wheel(page, total: int) -> None:
    """Scroll by `total` px in small uneven wheel ticks (negative scrolls up)."""
    direction = 1 if total > 0 else -1
    done = 0
    while done < abs(total):
        step = random.randint(60, 140)
        page.mouse.wheel(0, step * direction)
        done += step
        time.sleep(random.uniform(0.03, 0.12))


def scroll_feed(page) -> None:
    wheel(page, random.randint(350, 900))
    if random.random() < 0.12:
        short()
        wheel(page, -random.randint(100, 300))


def move_to(page, locator) -> tuple[float, float] | None:
    box = locator.bounding_box()
    if not box:
        return None
    x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    page.mouse.move(x, y, steps=random.randint(8, 22))
    return x, y


def click(page, locator) -> None:
    pos = move_to(page, locator)
    short()
    if pos:
        page.mouse.click(*pos)
    else:
        locator.click()
