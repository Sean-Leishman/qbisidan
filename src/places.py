"""The places map: saved venues, grouped, geocoded, and linked out to the apps you use.

    saved items -> classify (one classifier, any source) -> geocode -> walk/ride from home
      -> one HTML map with a layer per group, plus an unplaced list and a travel list

Three rules shape all of it:
- A venue that cannot be found is listed as unplaced, with its link -- never a guessed pin.
  "The Royal Oak, London" has a dozen answers; a confident wrong pin is worse than none.
- Travel is a different shape: a Lisbon rooftop is a destination, not a pin on a London map.
- Navigation is linked out, not rebuilt: Google Maps, Apple Maps and Citymapper already do
  directions and transit better than anything here would.
"""
import hashlib
import html
import json
import logging
import math
import os
import re
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote, urlencode

import requests

logger = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
VALHALLA = "https://valhalla1.openstreetmap.de/sources_to_targets"
USER_AGENT = "obsidian-helper places (personal use)"
LOCAL = ("pub", "nightlife", "food", "activity")
# A named venue must come back as a venue. A road or suburb that shares the name ("Fabric"
# is also a street) is a miss, not an approximate hit.
VENUE_CLASSES = {"amenity", "leisure", "tourism", "shop", "club", "sport", "craft", "historic"}
MATRIX_BATCH = 50
COLOURS = {"pub": "#b45309", "nightlife": "#7c3aed", "food": "#dc2626", "activity": "#047857"}
LABELS = {"pub": "Pubs", "nightlife": "Night", "food": "Food", "activity": "Activities"}


@dataclass
class Place:
    category: str
    venue: str | None = None
    area: str | None = None
    text: str = ""
    url: str | None = None
    source: str = ""
    why: str | None = None       # why it's worth going: from the caption, or your own note
    point: tuple[float, float] | None = None  # (lon, lat)
    found_as: str | None = None
    ambiguous: int = 0           # how many distinct venues matched the name
    walk_s: float | None = None
    bike_s: float | None = None
    note: str | None = None      # why it is unplaced


# ---------------------------------------------------------------- where "from me" is

def shared_locations_path() -> Path:
    return Path(os.environ.get("PERSONAL_LOCATIONS", "~/.config/personal/locations.toml")).expanduser()


def resolve_point(value=None, path=None):
    """A point to measure from: [lon, lat] as given; a name looked up in the shared
    locations file (~/.config/personal/locations.toml, versioned in dotfiles); or, given
    nothing, that file's `origin`. None if none of those exist -- times are then omitted,
    never measured from a guessed place."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (float(value[0]), float(value[1]))
    path = Path(path) if path else shared_locations_path()
    try:
        shared = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        if value:
            raise ValueError(f"location {value!r} named, but {path} is missing or unreadable")
        return None
    name = value or shared.get("origin")
    if not name:
        return None
    point = (shared.get("locations") or {}).get(name)
    if not (isinstance(point, list) and len(point) == 2):
        raise ValueError(f"no location called {name!r} in {path}")
    return (float(point[0]), float(point[1]))


# ---------------------------------------------------------------- cached, polite HTTP

class Http:
    """One request at a time, spaced, cached to disk. Nominatim allows one a second."""

    def __init__(self, cache_dir, spacing_s=1.1, session=None, sleep=time.sleep):
        self.cache_dir = Path(cache_dir)
        self.spacing_s = spacing_s
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._sleep = sleep
        self._next = 0.0

    def _cached(self, key, fetch):
        path = self.cache_dir / f"{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"
        if path.exists():
            return json.loads(path.read_text())
        wait = self._next - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._next = time.monotonic() + self.spacing_s
        data = fetch()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data

    def get(self, url, params):
        def fetch():
            r = self.session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        return self._cached(url + "?" + urlencode(sorted(params.items())), fetch)

    def post(self, url, body):
        def fetch():
            r = self.session.post(url, json=body, timeout=60)
            r.raise_for_status()
            return r.json()
        return self._cached(url + json.dumps(body, sort_keys=True), fetch)


# ---------------------------------------------------------------- sources

TO_EAT = re.compile(r"^\s*-\s*\[ \]\s*(.+?)\s*$")
NOTE_SPLIT = re.compile(r"\s+[\u2014\u2013-]\s+")  # " — ", " – " or " - " starts your note


def from_to_be_eaten(path) -> list[Place]:
    """Your own list, read-only. Unticked lines only: a ticked one has been eaten.
    "Noodles Inn, Chinatown" -> venue "Noodles Inn", area "Chinatown".
    "Master Wei, Chinatown — biang biang noodles" adds your own reason for going."""
    path = Path(path)
    if not path.exists():
        return []
    places = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = TO_EAT.match(line)
        if not match:
            continue
        entry, *note = NOTE_SPLIT.split(match.group(1), maxsplit=1)
        venue, _, area = entry.partition(",")
        places.append(Place("food", venue.strip() or None, area.strip() or None,
                            text=match.group(1), source="To Be Eaten",
                            why=note[0].strip() if note and note[0].strip() else None))
    return places


INBOX = Path("data/places_inbox.json")


def add_to_inbox(item, path=INBOX):
    """Keep a capture (e.g. a screenshot's text) for every future map build. Written before it
    is classified, so a classifier failure can never lose it."""
    path = Path(path)
    items = json.loads(path.read_text()) if path.exists() else []
    items.append(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=1))


def load_inbox(path=INBOX) -> list[dict]:
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else []


def classify(items, classifier, cache_dir) -> list[Place]:
    """items: [{"text": ..., "url": ..., "source": ...}]. One classifier for every source.

    Results are cached by text, so a re-run over a fuller Instagram export pays only for
    the new saves.
    """
    cache = Path(cache_dir) / "classified.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    key = lambda text: hashlib.sha256(text.encode()).hexdigest()[:24]
    todo = [it for it in items if "why" not in known.get(key(it["text"]), {})]
    if todo:
        for it, placement in zip(todo, classifier.classify([it["text"] for it in todo])):
            if placement.category != "unsorted":  # a failure is retried next run, not remembered
                known[key(it["text"])] = asdict(placement)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known))
    out = []
    for it in items:
        p = known.get(key(it["text"]), {"category": "unsorted"})
        out.append(Place(p["category"], p.get("venue"), p.get("area"), text=it["text"],
                         url=it.get("url"), source=it.get("source", ""), why=p.get("why")))
    return out


# ---------------------------------------------------------------- geography

def km(a, b):
    (lon1, lat1), (lon2, lat2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(h)))


def viewbox(home, radius_km):
    lon, lat = home
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(0.01, math.cos(math.radians(lat))))
    return f"{lon - dlon},{lat + dlat},{lon + dlon},{lat - dlat}"


STOPWORDS = {"the", "a", "an", "and", "of", "london"}


def _tokens(name):
    return {t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if t not in STOPWORDS}


def same_name(wanted, found) -> bool:
    """Every significant word of one name appears in the other.

    "Master Wei" ~ "Master Wei Xi'An" and "Bao Soho" ~ "Bao" pass. "Noodles Inn" vs
    "Meeting Noodles" fails -- Nominatim matched the word "noodles" to a different restaurant
    in King's Cross, and that confident wrong pin is exactly what this map must not show.
    """
    a, b = _tokens(wanted), _tokens(found)
    return bool(a) and bool(b) and (a <= b or b <= a)


def _point(result):
    return (float(result["lon"]), float(result["lat"]))


# What an area hint may resolve to. Found live: "East London" came back as the University of
# East London (an amenity out towards Barking) and "E2" as a building called "E2 (Left)" in
# Harringay -- each a POI that merely contains the words, and each dragged a pub 137 minutes
# away. A hint that is not an area is no hint; the search falls back to nearest-to-home.
AREA_TYPES = {"suburb", "neighbourhood", "quarter", "city_district", "borough", "postcode",
              "town", "village", "hamlet", "locality", "railway"}
AREA_MAX_KM = 8.0  # bigger than this is a region, too vague to choose between branches


def _area_point(area, http, *, city, country, home, region_km):
    """Where a stated area is, so it can choose between branches. None if unknown or vague."""
    if not area:
        return None
    params = {"q": f"{area}, {city}", "format": "jsonv2", "limit": 5, "countrycodes": country}
    if home:
        params.update(viewbox=viewbox(home, region_km), bounded=1)
    try:
        results = http.get(NOMINATIM, params)
    except Exception:
        return None
    for r in results or []:
        if r.get("addresstype") not in AREA_TYPES:
            continue
        try:
            south, north, west, east = map(float, r["boundingbox"])
        except (KeyError, ValueError):
            continue
        if km((west, south), (east, north)) <= AREA_MAX_KM:
            return _point(r)
    return None


# A full UK postcode in the saved text is the most precise hint there is, and it is read
# straight from the text -- no model involved. Found live: a screenshot of Padella's page
# said "6 Southwark St, London SE1 1TQ", the classifier summarised the area as "London", and
# the map pinned the *Shoreditch* Padella because it was nearer Moorgate.
UK_POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.I)


def _postcode_point(text, http, country):
    match = UK_POSTCODE.search(text or "")
    if not match or country.lower() != "gb":
        return None
    postcode = f"{match.group(1)} {match.group(2)}".upper()
    try:
        results = http.get(NOMINATIM, {"postalcode": postcode, "countrycodes": country,
                                       "format": "jsonv2", "limit": 1})
    except Exception:
        return None
    return _point(results[0]) if results else None


def geocode(place: Place, http: Http, *, city, country, home=None, region_km=30.0):
    """Find a named local venue, or say plainly why not.

    The area is a *location hint*, not search text: "Master Wei, Chinatown" has a Hammersmith
    branch Nominatim lists first, and the one you meant is the branch nearest Chinatown.
    """
    if not place.venue:
        place.note = "no venue named"
        return
    params = {"q": f"{place.venue}, {city}", "format": "jsonv2", "limit": 10, "countrycodes": country}
    if home:
        params.update(viewbox=viewbox(home, region_km), bounded=1)
    try:
        results = http.get(NOMINATIM, params)
    except Exception as error:
        place.note = f"geocoder unavailable ({type(error).__name__})"
        return
    venues = [r for r in results or []
              if (r.get("category") or r.get("class")) in VENUE_CLASSES
              and same_name(place.venue, r.get("name") or (r.get("display_name") or "").split(",")[0])]
    if not venues:
        place.note = f"couldn't find \u201c{place.venue}\u201d as a venue near {place.area or city}"
        return
    anchor = (_postcode_point(place.text, http, country)
              or _area_point(place.area, http, city=city, country=country, home=home, region_km=region_km)
              or home)
    if anchor:
        venues.sort(key=lambda r: km(anchor, _point(r)))
    best = venues[0]
    place.point = _point(best)
    place.found_as = best.get("display_name")
    # Several venues of that name more than a kilometre apart: pinned to the best guess but
    # flagged, because "The Royal Oak" is a dozen pubs.
    others = {(round(lon, 2), round(lat, 2)) for lon, lat in map(_point, venues[1:])
              if km(place.point, (lon, lat)) > 1.0}
    place.ambiguous = len(others) + 1 if others else 0


def travel_times(places, home, http: Http):
    """Walk and ride from home to every placed venue, in batched matrix calls."""
    placed = [p for p in places if p.point]
    for mode, attr in (("pedestrian", "walk_s"), ("bicycle", "bike_s")):
        for start in range(0, len(placed), MATRIX_BATCH):
            batch = placed[start:start + MATRIX_BATCH]
            body = {"sources": [{"lon": home[0], "lat": home[1]}],
                    "targets": [{"lon": p.point[0], "lat": p.point[1]} for p in batch],
                    "costing": mode, "units": "kilometers"}
            try:
                row = (http.post(VALHALLA, body).get("sources_to_targets") or [[]])[0]
            except Exception as error:  # routing is a nicety; the map is still worth having
                logger.warning(f"{mode} times unavailable: {error}")
                break
            for place, cell in zip(batch, row):
                setattr(place, attr, (cell or {}).get("time"))


# ---------------------------------------------------------------- links

def links(place: Place) -> dict:
    name = " ".join(x for x in (place.venue, place.area) if x) or place.text[:60]
    out = {"google": f"https://www.google.com/maps/search/?api=1&query={quote(name)}"}
    if place.point:
        lon, lat = place.point
        out["apple"] = f"https://maps.apple.com/?q={quote(place.venue or name)}&ll={lat},{lon}"
        out["citymapper"] = (f"https://citymapper.com/directions?endcoord={lat},{lon}"
                             f"&endname={quote(place.venue or name)}")
    if place.url:
        out["source"] = place.url
    return out


# ---------------------------------------------------------------- build + render

def build(places, http, *, city, country, home=None, region_km=30.0):
    for place in places:
        if place.category in LOCAL:
            geocode(place, http, city=city, country=country, home=home, region_km=region_km)
    if home:
        travel_times(places, home, http)
    return places


def summary(places):
    return {
        "placed": [p for p in places if p.category in LOCAL and p.point],
        "unplaced": [p for p in places if p.category in LOCAL and not p.point],
        "travel": [p for p in places if p.category == "travel"],
        "other": sum(1 for p in places if p.category == "other"),
        "unsorted": [p for p in places if p.category not in LOCAL + ("travel", "other")],
    }


def _row(p: Place) -> dict:
    return {"category": p.category, "venue": p.venue, "area": p.area, "why": p.why, "text": p.text[:240],
            "source": p.source, "point": p.point, "found_as": p.found_as, "ambiguous": p.ambiguous,
            "walk_min": round(p.walk_s / 60) if p.walk_s else None,
            "bike_min": round(p.bike_s / 60) if p.bike_s else None,
            "note": p.note, "links": links(p)}


def geojson(places, home=None) -> dict:
    s = summary(places)
    features = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": list(p.point)},
                 "properties": _row(p)} for p in s["placed"]]
    if home:
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": list(home)},
                         "properties": {"category": "home"}})
    return {"type": "FeatureCollection", "features": features}


def render(places, home=None, title="Places") -> str:
    s = summary(places)
    data = {
        "home": list(home) if home else None,
        "placed": [_row(p) for p in s["placed"]],
        "unplaced": [_row(p) for p in s["unplaced"] + s["unsorted"]],
        "travel": [_row(p) for p in s["travel"]],
        "other": s["other"], "colours": COLOURS, "labels": LABELS,
    }
    # Captions are other people's text. The page inserts everything with textContent, and here
    # every < > & becomes a JSON unicode escape, so nothing from a caption can look like markup
    # to the HTML parser -- not a closing </script>, and not an opening <!--<script either.
    payload = (json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e")
               .replace("&", "\\u0026"))
    return _PAGE.replace("__TITLE__", html.escape(title)).replace("__DATA__", payload)


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/maplibre-gl.css">
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/maplibre-gl.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@maplibre/maplibre-gl-leaflet@0.1.4/leaflet-maplibre-gl.js"></script>
<style>
:root{--bg:#fff;--fg:#141414;--muted:#666;--line:#e6e6e6;--chip:#f3f3f3}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141619;--fg:#ececec;--muted:#9aa0a6;--line:#2b2f35;--chip:#1e2126}}
:root[data-theme=dark]{--bg:#141619;--fg:#ececec;--muted:#9aa0a6;--line:#2b2f35;--chip:#1e2126}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header,section{padding:12px 16px}h1{font-size:19px;margin:0}h2{font-size:15px;margin:18px 0 6px}
.muted{color:var(--muted);font-size:13px}#map{height:58vh;min-height:300px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{border:1px solid var(--line);background:var(--chip);color:var(--fg);border-radius:999px;padding:4px 10px;font-size:13px;cursor:pointer}
.chip[aria-pressed=false]{opacity:.45}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
ul{list-style:none;margin:0;padding:0}li{padding:8px 0;border-bottom:1px solid var(--line)}
.name{font-weight:600}.reason{font-style:italic;margin:1px 0 2px}.links a{margin-right:10px;font-size:13px;color:inherit}.why{font-size:13px;color:var(--muted)}
</style></head><body>
<header><h1>__TITLE__</h1><div class="muted" id="counts"></div><div class="chips" id="chips"></div></header>
<div id="map"></div>
<section id="lists"></section>
<script>
const D = __DATA__;
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const linkRow = (p) => { const d = el('div', 'links');
  for (const [k, label] of [['google','Google Maps'],['apple','Apple Maps'],['citymapper','Citymapper'],['source','Saved post']]) {
    if (!p.links[k]) continue; const a = el('a', null, label); a.href = p.links[k]; a.target = '_blank'; a.rel = 'noopener'; d.append(a); }
  return d; };
const times = p => [p.walk_min != null ? `${p.walk_min} min walk` : null, p.bike_min != null ? `${p.bike_min} min ride` : null].filter(Boolean).join(' \\u00b7 ');

document.getElementById('counts').textContent =
  `${D.placed.length} on the map \\u00b7 ${D.unplaced.length} unplaced \\u00b7 ${D.travel.length} travel \\u00b7 ${D.other} not places`;

const map = L.map('map');
// The view must exist before any marker is added: Leaflet's renderer reads the map's bounds
// on add and throws "reading 'intersects'" without one -- which killed the whole script.
const bounds = D.placed.map(p => [p.point[1], p.point[0]]);
if (D.home) bounds.push([D.home[1], D.home[0]]);
if (bounds.length) map.fitBounds(bounds, {padding: [24, 24], maxZoom: 15}); else map.setView([51.507, -0.128], 11);
// Not the OSM volunteer tile servers: they require a Referer, which a page opened from disk never sends.
L.maplibreGL({style: 'https://tiles.openfreemap.org/styles/' + (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'positron'), attribution: '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> &copy; <a href="https://www.openmaptiles.org/" target="_blank">OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>'}).addTo(map);
const layers = {};
for (const p of D.placed) {
  const [lon, lat] = p.point;
  const box = el('div'); box.append(el('div', 'name', p.venue || p.text.slice(0, 40)));
  if (p.why) box.append(el('div', 'reason', p.why));
  if (p.area) box.append(el('div', 'why', p.area));
  if (times(p)) box.append(el('div', null, times(p)));
  if (p.ambiguous) box.append(el('div', 'why', `${p.ambiguous} places share this name \\u2014 check the pin`));
  box.append(linkRow(p));
  const m = L.circleMarker([lat, lon], {radius: 8, color: '#0005', weight: 1, fillColor: D.colours[p.category], fillOpacity: .9}).bindPopup(box);
  (layers[p.category] ||= L.layerGroup().addTo(map)).addLayer(m);
}
if (D.home) L.circleMarker([D.home[1], D.home[0]], {radius: 7, color: '#2563eb', weight: 3, fillOpacity: .2}).bindPopup('Measured from here').addTo(map);

const chips = document.getElementById('chips');
for (const cat of Object.keys(D.colours)) {
  const n = D.placed.filter(p => p.category === cat).length; if (!n) continue;
  const b = el('button', 'chip'); b.setAttribute('aria-pressed', 'true');
  const dot = el('span', 'dot'); dot.style.background = D.colours[cat]; b.append(dot, `${D.labels[cat]} ${n}`);
  b.onclick = () => { const on = b.getAttribute('aria-pressed') === 'true'; b.setAttribute('aria-pressed', String(!on)); on ? map.removeLayer(layers[cat]) : layers[cat].addTo(map); };
  chips.append(b);
}

const lists = document.getElementById('lists');
const section = (title, items, render) => { if (!items.length) return; lists.append(el('h2', null, title)); const ul = el('ul'); items.forEach(p => ul.append(render(p))); lists.append(ul); };
const placedItem = p => { const li = el('li'); li.append(el('div', 'name', p.venue));
  if (p.why) li.append(el('div', 'reason', p.why));
  li.append(el('div', 'why', [p.area, times(p)].filter(Boolean).join(' \\u00b7 '))); li.append(linkRow(p)); return li; };
for (const cat of Object.keys(D.colours)) {
  const items = D.placed.filter(p => p.category === cat).sort((a, b) => (a.walk_min ?? 1e9) - (b.walk_min ?? 1e9));
  section(D.labels[cat], items, placedItem);
}
section('Unplaced \\u2014 saved, but no pin', D.unplaced, p => { const li = el('li');
  li.append(el('div', 'name', p.venue || p.text.slice(0, 80)));
  li.append(el('div', 'why', `${p.category === 'unsorted' ? 'not classified yet' : p.category} \\u00b7 ${p.note || 'unclassified'}`));
  li.append(linkRow(p)); return li; });
const byArea = {}; for (const p of D.travel) (byArea[p.area || 'Somewhere'] ||= []).push(p);
section('Travel', Object.entries(byArea).map(([area, ps]) => ({area, ps})), ({area, ps}) => { const li = el('li');
  li.append(el('div', 'name', area)); for (const p of ps) { li.append(el('div', 'why', p.text.slice(0, 120))); li.append(linkRow(p)); } return li; });
</script></body></html>"""
