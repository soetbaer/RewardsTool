"""Liest den Zustand aus dem Rewards-Dashboard.

Das Dashboard ist eine Next.js-Seite: Die Daten stecken als React-Server-Payload
(self.__next_f.push) im HTML. Punkte gibt es nur, wenn eine Kachel auf der
Rewards-Seite angeklickt wird (die Ziel-Links tragen rnoreward=1).
"""
import html as htmllib
import json
import re
from dataclasses import dataclass, field

from .util import log

REWARDS = "https://rewards.bing.com"
DASHBOARD_URL = f"{REWARDS}/dashboard"
EARN_URL = f"{REWARDS}/earn"

_decoder = json.JSONDecoder()


@dataclass
class Activity:
    offer_id: str
    title: str
    description: str
    url: str
    points: int
    completed: bool
    section: str  # Anzeigename
    dom_section: str  # id der <section> auf der Seite ("" = ganze Seite)
    page_url: str  # Seite, auf der die Kachel angeklickt werden muss


@dataclass
class State:
    points: int | None = None
    search: tuple[int, int] | None = None
    daily_set: list[Activity] = field(default_factory=list)
    more: list[Activity] = field(default_factory=list)
    explore: list[Activity] = field(default_factory=list)

    def open_activities(self) -> list[Activity]:
        return [a for a in self.daily_set + self.explore + self.more if not a.completed]


# ---------- Payload-Dekodierung ----------

def decode_payload(page_html: str) -> str:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', page_html)
    return "".join(json.loads(c) for c in chunks)


def _values(payload: str, key: str) -> list:
    """Alle JSON-Werte, die im Payload unter "key" stehen."""
    out = []
    for m in re.finditer(re.escape(f'"{key}":'), payload):
        try:
            value, _ = _decoder.raw_decode(payload, m.end())
            out.append(value)
        except ValueError:
            pass
    return out


def _enclosing_object(payload: str, pos: int) -> dict | None:
    depth, j = 0, pos
    while j > 0:
        c = payload[j]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                break
            depth -= 1
        j -= 1
    try:
        return _decoder.raw_decode(payload, j)[0]
    except ValueError:
        return None


def _flag(value) -> bool:
    return value is True or str(value).lower() == "true"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(text).replace("​", "")).strip()


def _section_cards(page_html: str, section_id: str) -> list[tuple[str, str, str]]:
    """(href, Titel, Beschreibung) aller Kachel-Links einer <section>."""
    part = page_html.split(f'<section id="{section_id}"', 1)
    if len(part) < 2:
        return []
    sec = part[1].split("</section>", 1)[0]
    cards = []
    for href, inner in re.findall(r'<a [^>]*href="([^"]*)"[^>]*>(.*?)</a>', sec, flags=re.S):
        texts = [_clean(re.sub(r"<[^>]+>", " ", p)) for p in re.findall(r"<p[^>]*>(.*?)</p>", inner, flags=re.S)]
        texts += ["", ""]
        cards.append((htmllib.unescape(href), texts[0], texts[1]))
    return cards


# ---------- Parser ----------

def parse_dashboard(page_html: str, state: State) -> None:
    payload = decode_payload(page_html)
    text = _clean(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", page_html, flags=re.S)))
    m = re.search(r"Verfügbare Punkte ([\d.,]+)", text) or re.search(r"Available points ([\d.,]+)", text)
    if m:
        state.points = int(re.sub(r"\D", "", m.group(1)))

    for items in _values(payload, "dailySetItems")[:1]:
        for raw in items:
            state.daily_set.append(Activity(
                offer_id=raw.get("offerId", ""),
                title=_clean(raw.get("title") or ""),
                description=_clean(raw.get("description") or ""),
                url=raw.get("destination") or "",
                points=int(raw.get("points") or 0),
                completed=_flag(raw.get("isCompleted")),
                section="Tägliche Aktionen",
                dom_section="dailyset",
                page_url=DASHBOARD_URL,
            ))


def parse_earn(page_html: str, state: State) -> None:
    payload = decode_payload(page_html)

    for counters in _values(payload, "pointsCounters")[:1]:
        pc = counters.get("pc") or {}
        if "max" in pc:
            state.search = (int(pc.get("progress", 0)), int(pc["max"]))

    # "Weiter verdienen" (und weitere Kachel-Bereiche mit Punkten)
    seen = set()
    for cards in _values(payload, "activityCards"):
        for raw in cards if isinstance(cards, list) else []:
            oid = raw.get("offerId") or raw.get("name")
            if (
                not oid or oid in seen
                or _flag(raw.get("isPromotional")) or _flag(raw.get("isLocked"))
                or int(raw.get("points") or 0) <= 0
            ):
                continue
            seen.add(oid)
            state.more.append(Activity(
                offer_id=oid,
                title=_clean(raw.get("title") or ""),
                description=_clean(raw.get("description") or ""),
                url=raw.get("destination") or "",
                points=int(raw.get("points") or 0),
                completed=_flag(raw.get("isCompleted")),
                section="Weiter verdienen",
                dom_section="",
                page_url=EARN_URL,
            ))

    # "Auf Bing erkunden": Payload liefert Status, Titel stehen nur im HTML.
    # Gesperrte Kacheln sind keine Links, daher Reihenfolge der freigeschalteten = Reihenfolge der Links.
    explore, seen = [], set()
    for m in re.finditer(r'"offerId":"[^"]*exploreonbing[^"]*"', payload):
        raw = _enclosing_object(payload, m.start())
        if raw and raw.get("offerId") not in seen:
            seen.add(raw["offerId"])
            explore.append(raw)
    unlocked = [r for r in explore if not _flag(r.get("isLocked")) and not _flag(r.get("isDisabled"))]
    cards = _section_cards(page_html, "exploreonbing")
    if len(cards) != len(unlocked):
        log.warning("Auf Bing erkunden: %s Kacheln, aber %s freigeschaltete Angebote", len(cards), len(unlocked))
    for raw, (href, title, desc) in zip(unlocked, cards):
        state.explore.append(Activity(
            offer_id=raw["offerId"],
            title=title,
            description=desc,
            url=href,
            points=10,
            completed=_flag(raw.get("isCompleted")),
            section="Auf Bing erkunden",
            dom_section="exploreonbing",
            page_url=EARN_URL,
        ))


# ---------- Laden ----------

def open_rewards_page(ctx, url: str):
    """Öffnet eine Rewards-Seite; None, wenn nicht eingeloggt."""
    page = ctx.new_page()
    for attempt in range(2):
        page.goto(url, wait_until="load", timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass  # Seite hält Verbindungen offen – Daten sind trotzdem da
        page.wait_for_timeout(1500)  # React-Hydrierung, damit Klicks greifen
        if "rewards.bing.com" in page.url and "self.__next_f" in page.content():
            break
        page.wait_for_timeout(3000)  # langsamer Seitenaufbau: ein zweiter Versuch
    if "rewards.bing.com" not in page.url or "self.__next_f" not in page.content():
        log.warning("Nicht eingeloggt (gelandet auf %s)", page.url)
        page.close()
        return None
    expand_sections(page)
    return page


# Markiert den Kopf-Knopf jedes eingeklappten Bereichs (nicht die Knöpfe im Inhalt des Bereichs)
_MARK_COLLAPSED_JS = """
() => {
  document.querySelectorAll('[data-rt-expand]').forEach(b => b.removeAttribute('data-rt-expand'));
  const names = [];
  document.querySelectorAll('.react-aria-Disclosure').forEach(d => {
    if (d.getAttribute('data-expanded') === 'true') return;
    const panel = d.querySelector('.react-aria-DisclosurePanel');
    const btn = [...d.querySelectorAll('button[aria-expanded="false"]')]
      .find(b => (!panel || !panel.contains(b)) && b.closest('.react-aria-Disclosure') === d);
    if (btn) {
      btn.setAttribute('data-rt-expand', '1');
      names.push(d.closest('section')?.id || '?');
    }
  });
  return names;
}
"""


def expand_sections(page) -> None:
    """Klappt eingeklappte Bereiche auf – sonst sind die Kacheln darin nicht anklickbar."""
    opened = []
    try:
        # Immer nur einen Bereich öffnen und danach neu suchen: React baut die Seite beim Aufklappen neu auf,
        # dabei gehen Markierungen an den übrigen Knöpfen verloren.
        for _ in range(20):
            names = page.evaluate(_MARK_COLLAPSED_JS)
            if not names:
                break
            page.locator("[data-rt-expand]").first.click(timeout=5000)
            page.wait_for_timeout(500)
            opened.append(names[0])
        if opened:
            log.info("Eingeklappte Bereiche aufgeklappt: %s", ", ".join(opened))
    except Exception as e:
        log.warning("Bereiche konnten nicht aufgeklappt werden: %s", e)


def load_state(ctx, dashboard: bool = True, earn: bool = True) -> State | None:
    state = State()
    for enabled, url, parser in ((dashboard, DASHBOARD_URL, parse_dashboard), (earn, EARN_URL, parse_earn)):
        if not enabled:
            continue
        page = open_rewards_page(ctx, url)
        if not page:
            return None
        try:
            parser(page.content(), state)
        finally:
            page.close()
    return state
