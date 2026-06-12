"""Centro de noticias EN VIVO: muchas fuentes RSS + calendario economico.

- Hilo propio: refresca titulares de ~15 fuentes (forex, mercados, mundo,
  bancos centrales, cripto, materias primas) cada NEWS_REFRESH_SECONDS y el
  calendario economico (ForexFactory) cada 30 minutos.
- Etiqueta cada titular con las divisas/activos que menciona, para saber que
  noticias influyen en cada simbolo.
- context_for(symbol): paquete de titulares + eventos que se inyecta en los
  prompts de la IA (asi la IA cita que noticia pesa en su decision).
- blocking_event(symbol): filtro duro que evita abrir trades minutos antes y
  despues de un dato de ALTO impacto de las divisas del simbolo.
"""
import hashlib
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import feedparser
import requests

import config
from app.state import state

# (categoria, nombre, url) — fuentes RSS publicas, sin API key
FEEDS = [
    ("forex", "ForexLive", "https://www.forexlive.com/feed/news/"),
    ("forex", "FXStreet", "https://www.fxstreet.com/rss/news"),
    ("forex", "Investing Forex", "https://www.investing.com/rss/news_1.rss"),
    ("mercados", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("mercados", "CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("mercados", "Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("mundo", "BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("mundo", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("mundo", "The Guardian", "https://www.theguardian.com/world/rss"),
    ("bancos_centrales", "Reserva Federal", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("bancos_centrales", "BCE", "https://www.ecb.europa.eu/rss/press.html"),
    ("cripto", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cripto", "Cointelegraph", "https://cointelegraph.com/rss"),
    ("materias_primas", "Investing Commodities", "https://www.investing.com/rss/news_11.rss"),
    ("materias_primas", "OilPrice", "https://oilprice.com/rss/main"),
]

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# Deteccion de divisas/activos mencionados en un titular
TAG_PATTERNS = [
    ("USD", r"\b(USD|DOLLAR|DOLAR|FED|FOMC|POWELL|TREASURY|NFP|PAYROLLS?|WALL STREET|U\.S\.|US ECONOMY)\b"),
    ("EUR", r"\b(EUR|EURO(?:ZONE|ZONA)?|ECB|BCE|LAGARDE|BUNDESBANK|GERMANY|ALEMANIA|FRANCE)\b"),
    ("GBP", r"\b(GBP|POUND|STERLING|BOE|BAILEY|UK|BRITAIN|LONDON STOCK)\b"),
    ("JPY", r"\b(JPY|YEN|BOJ|UEDA|JAPAN|JAPON|NIKKEI)\b"),
    ("AUD", r"\b(AUD|AUSSIE|RBA|AUSTRALIA)\b"),
    ("NZD", r"\b(NZD|KIWI|RBNZ|NEW ZEALAND)\b"),
    ("CAD", r"\b(CAD|LOONIE|BOC|BANK OF CANADA|CANADA)\b"),
    ("CHF", r"\b(CHF|FRANC|SNB|SWITZERLAND|SUIZA)\b"),
    ("CNH", r"\b(CNH|CNY|YUAN|PBOC|CHINA|CHINESE)\b"),
    ("MXN", r"\b(MXN|PESO MEXICANO|BANXICO|MEXICO)\b"),
    ("XAU", r"\b(GOLD|XAU|ORO|BULLION)\b"),
    ("XAG", r"\b(SILVER|XAG|PLATA)\b"),
    ("OIL", r"\b(OIL|CRUDE|WTI|BRENT|OPEC|PETROLEO)\b"),
    ("BTC", r"\b(BITCOIN|BTC)\b"),
    ("ETH", r"\b(ETHEREUM|ETHER|ETH)\b"),
    ("CRYPTO", r"\b(CRYPTO(?:CURRENC\w+)?|CRIPTO\w*|STABLECOIN|BINANCE|COINBASE)\b"),
]
_TAGS_COMPILED = [(tag, re.compile(pat, re.IGNORECASE)) for tag, pat in TAG_PATTERNS]

# Simbolos que no se pueden trocear en divisas: se mapean a mano
# (indices, y alias de metales/energia que usan algunos brokers)
SPECIAL_TAGS = {
    "US30": {"USD"}, "US100": {"USD"}, "US500": {"USD"}, "NAS100": {"USD"},
    "SPX500": {"USD"}, "GER40": {"EUR"}, "DAX40": {"EUR"}, "UK100": {"GBP"},
    "JP225": {"JPY"}, "AUS200": {"AUD"},
    "GOLD": {"XAU", "USD"}, "SILVER": {"XAG", "USD"},
    "USOIL": {"OIL", "USD"}, "WTI": {"OIL", "USD"},
    "UKOIL": {"OIL"}, "BRENT": {"OIL"},
}


def detect_tags(text: str) -> list[str]:
    return [tag for tag, rx in _TAGS_COMPILED if rx.search(text)]


def symbol_tags(symbol: str) -> set[str]:
    """Divisas/activos a los que es sensible un simbolo (EURUSD -> {EUR, USD})."""
    s = re.sub(r"[._\-]", "", symbol.upper())
    for alias, tags in SPECIAL_TAGS.items():
        if s.startswith(alias):
            return set(tags)
    tags = set()
    if len(s) >= 6 and s[:6].isalpha():
        tags.update((s[:3], s[3:6]))
    if s[:3] in ("BTC", "ETH", "XRP", "SOL", "ADA", "DOG", "BNB", "LTC"):
        tags.update({"CRYPTO", s[:3]})
        if "USDT" in s:
            tags.add("USD")  # USDT sigue al dolar: el calendario USD le afecta
    return tags


class NewsCenter:
    def __init__(self):
        self._lock = threading.Lock()
        self.items = deque(maxlen=400)   # titulares (sin orden garantizado)
        self.calendar = []               # eventos economicos de la semana (orden por fecha)
        self._seen = set()
        self._seen_order = deque(maxlen=1500)
        self._fails = {}                 # url/“calendar” -> fallos consecutivos
        self._last_calendar = 0.0
        self._calendar_logged = False
        self._started = False

    # ---------- Ciclo de refresco ----------

    def start(self):
        if not config.NEWS_ENABLED or self._started:
            return
        self._started = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        state.log("info", f"NOTICIAS EN VIVO activadas: {len(self._feeds())} fuentes RSS "
                          f"+ calendario economico (refresco cada {config.NEWS_REFRESH_SECONDS}s).")
        while True:
            try:
                self._refresh_round()
            except Exception as e:
                state.log("error", f"Noticias: error en el ciclo de refresco: {e}")
            time.sleep(max(30, config.NEWS_REFRESH_SECONDS))

    def _feeds(self) -> list[tuple]:
        feeds = list(FEEDS)
        for url in config.NEWS_EXTRA_FEEDS:
            try:
                name = url.split("/")[2].replace("www.", "")[:30]
            except IndexError:
                name = url[:30]
            feeds.append(("extra", name, url))
        return feeds

    def _refresh_round(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(self._fetch_feed, self._feeds()))
        if time.time() - self._last_calendar > 1800:
            self._fetch_calendar()

    def _fetch_feed(self, feed: tuple):
        category, name, url = feed
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
            now = datetime.now(timezone.utc)
            fresh = []
            for e in parsed.entries[:25]:
                title = re.sub(r"\s+", " ", str(getattr(e, "title", "") or "")).strip()
                if not title:
                    continue
                link = str(getattr(e, "link", "") or "")
                key = hashlib.md5((link or title).encode("utf-8", "ignore")).hexdigest()
                published = now
                for attr in ("published_parsed", "updated_parsed"):
                    t = getattr(e, attr, None)
                    if t:
                        published = datetime(*t[:6], tzinfo=timezone.utc)
                        break
                if (now - published).total_seconds() > 2 * 86400:
                    continue  # demasiado viejo para "en vivo"
                summary = re.sub(r"<[^>]+>", " ", str(getattr(e, "summary", "") or ""))
                summary = re.sub(r"\s+", " ", summary).strip()[:280]
                fresh.append({
                    "id": key,
                    "time_utc": published.isoformat(timespec="seconds"),
                    "category": category,
                    "source": name,
                    "title": title[:220],
                    "summary": summary,
                    "link": link,
                    "tags": detect_tags(f"{title} {summary}"),
                })
            with self._lock:
                for item in fresh:
                    if item["id"] in self._seen:
                        continue
                    self._seen.add(item["id"])
                    self._seen_order.append(item["id"])
                    self.items.appendleft(item)
                while len(self._seen) > 1400:
                    self._seen.discard(self._seen_order.popleft())
            self._fails[url] = 0
        except Exception as e:
            n = self._fails.get(url, 0) + 1
            self._fails[url] = n
            if n == 3 or n % 50 == 0:
                state.log("error", f"Noticias: la fuente '{name}' lleva {n} fallos ({e}). "
                                   f"Se sigue intentando; el resto de fuentes funciona igual.")

    def _fetch_calendar(self):
        self._last_calendar = time.time()
        try:
            resp = requests.get(CALENDAR_URL, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            events = []
            for ev in resp.json():
                try:
                    t = datetime.fromisoformat(str(ev.get("date")))
                except (TypeError, ValueError):
                    continue
                events.append({
                    "time_utc": t.astimezone(timezone.utc).isoformat(timespec="seconds"),
                    "currency": str(ev.get("country", "")).upper(),
                    "title": str(ev.get("title", ""))[:120],
                    "impact": str(ev.get("impact", "")).capitalize(),  # High/Medium/Low/Holiday
                    "forecast": str(ev.get("forecast", "") or ""),
                    "previous": str(ev.get("previous", "") or ""),
                })
            events.sort(key=lambda x: x["time_utc"])
            with self._lock:
                self.calendar = events
            self._fails["calendar"] = 0
            if not self._calendar_logged:
                self._calendar_logged = True
                highs = sum(1 for e in events if e["impact"] == "High")
                state.log("info", f"Calendario economico cargado: {len(events)} eventos esta "
                                  f"semana ({highs} de alto impacto).")
        except Exception as e:
            n = self._fails.get("calendar", 0) + 1
            self._fails["calendar"] = n
            if n == 1 or n % 10 == 0:
                state.log("error", f"Noticias: no se pudo bajar el calendario economico: {e}")

    # ---------- Consultas (thread-safe) ----------

    def _sorted_items(self) -> list[dict]:
        with self._lock:
            return sorted(self.items, key=lambda x: x["time_utc"], reverse=True)

    @staticmethod
    def _age_min(iso_utc: str) -> int:
        try:
            t = datetime.fromisoformat(iso_utc)
            return max(0, int((datetime.now(timezone.utc) - t).total_seconds() // 60))
        except (TypeError, ValueError):
            return 0

    def context_for(self, symbol: str, max_headlines: int = 8) -> dict | None:
        """Paquete de noticias relevantes para inyectar en el prompt de la IA."""
        if not config.NEWS_ENABLED:
            return None
        tags = symbol_tags(symbol)
        items = self._sorted_items()
        tagged = [i for i in items if tags & set(i["tags"])][:max_headlines - 3]
        seen_ids = {i["id"] for i in tagged}
        globals_ = [i for i in items if i["id"] not in seen_ids][:3]
        headlines = [
            f"[hace {self._age_min(i['time_utc'])}m] [{i['category']}/{i['source']}] {i['title']}"
            for i in tagged + globals_
        ]

        now = datetime.now(timezone.utc)
        upcoming, recent = [], []
        with self._lock:
            cal = list(self.calendar)
        for ev in cal:
            if ev["currency"] not in tags or ev["impact"] not in ("High", "Medium"):
                continue
            try:
                delta_min = (datetime.fromisoformat(ev["time_utc"]) - now).total_seconds() / 60
            except (TypeError, ValueError):
                continue
            line = (f"{ev['currency']} {ev['title']} (impacto {ev['impact'].upper()}, "
                    f"prevision {ev['forecast'] or '?'}, previo {ev['previous'] or '?'})")
            if 0 <= delta_min <= 12 * 60:
                upcoming.append(f"EN {int(delta_min)} MIN: {line}")
            elif -2 * 60 <= delta_min < 0:
                recent.append(f"hace {int(-delta_min)} min: {line}")
        return {
            "headlines": headlines,
            "upcoming_events": upcoming[:6],
            "recent_events": recent[:4],
        }

    def blocking_event(self, symbol: str) -> dict | None:
        """Evento de ALTO impacto inminente que bloquea entradas en el simbolo."""
        before, after = config.NEWS_BLOCK_BEFORE_MIN, config.NEWS_BLOCK_AFTER_MIN
        if not config.NEWS_ENABLED or (before <= 0 and after <= 0):
            return None
        tags = symbol_tags(symbol)
        now = datetime.now(timezone.utc)
        with self._lock:
            cal = list(self.calendar)
        for ev in cal:
            if ev["impact"] != "High" or ev["currency"] not in tags:
                continue
            try:
                delta_min = (datetime.fromisoformat(ev["time_utc"]) - now).total_seconds() / 60
            except (TypeError, ValueError):
                continue
            if -after <= delta_min <= before:
                return {"title": ev["title"], "currency": ev["currency"],
                        "minutes": int(delta_min), "time_utc": ev["time_utc"]}
        return None

    # ---------- API para la UI ----------

    def feed(self, limit: int = 80, category: str = "", symbol: str = "") -> list[dict]:
        tags = symbol_tags(symbol) if symbol else None
        out = []
        for i in self._sorted_items():
            if category and i["category"] != category:
                continue
            if tags is not None and not (tags & set(i["tags"])):
                continue
            out.append({**i, "age_min": self._age_min(i["time_utc"])})
            if len(out) >= limit:
                break
        return out

    def calendar_upcoming(self, hours: int = 48) -> list[dict]:
        now = datetime.now(timezone.utc)
        out = []
        with self._lock:
            cal = list(self.calendar)
        for ev in cal:
            try:
                delta_min = (datetime.fromisoformat(ev["time_utc"]) - now).total_seconds() / 60
            except (TypeError, ValueError):
                continue
            if -30 <= delta_min <= hours * 60:
                out.append({**ev, "in_min": int(delta_min)})
        return out

    def influence(self, symbols: list[str]) -> dict:
        """Que esta viendo el bot AHORA por simbolo (para la pagina Noticias)."""
        result = {}
        for sym in symbols:
            ctx = self.context_for(sym) or {"headlines": [], "upcoming_events": [], "recent_events": []}
            result[sym] = {
                "headlines": ctx["headlines"][:5],
                "upcoming_events": ctx["upcoming_events"][:4],
                "recent_events": ctx["recent_events"][:2],
                "blocked_by": self.blocking_event(sym),
            }
        return result

    def status(self) -> dict:
        with self._lock:
            n_items = len(self.items)
            n_events = len(self.calendar)
        failing = sum(1 for k, v in self._fails.items() if k != "calendar" and v >= 3)
        return {
            "enabled": config.NEWS_ENABLED,
            "sources": len(self._feeds()),
            "sources_failing": failing,
            "items": n_items,
            "calendar_events": n_events,
        }
