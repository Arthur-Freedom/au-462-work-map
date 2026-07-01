#!/usr/bin/env python3
"""
Build pipeline for the AU Working Holiday (subclass 462) specified-work map.

Inputs (build/raw/):
  - specified462.html ............ scraped immi "Specified 462 work" page (Tables 1-6)
  - australian_postcodes.csv ..... matthewproctor postcode -> state + centroid
  - POA_2021_AUST_GDA2020_15percent.json . ABS Postal Areas 2021 boundaries (GDA2020)

Outputs (data/):
  - eligible_poa.geojson ......... all 2,644 POA polygons, simplified, tagged with
                                   area-eligibility flags (r/n/g/b/d) + state.
  - postcodes.json ............... lookup of every postcode -> {state, flags, centroid,
                                   locality} used for the search box.
  - meta.json .................... build metadata + per-area postcode counts.

Data sources & rules: immi.homeaffairs.gov.au specified-462-work page (LIN 18/197 /
F2018L01539). POA boundaries (c) ABS, CC BY 4.0. Postcode CSV: matthewproctor (public domain).

Run:  python build/process.py     (re-downloads CSV/POA if missing; the immi HTML is bundled)
"""
import csv
import gzip
import html
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "build", "raw")
DATA = os.path.join(ROOT, "data")
os.makedirs(RAW, exist_ok=True)
os.makedirs(DATA, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

HTML_PATH = os.path.join(RAW, "specified462.html")
CSV_PATH = os.path.join(RAW, "australian_postcodes.csv")
POA_PATH = os.path.join(RAW, "POA_2021_AUST_GDA2020_15percent.json")

CSV_URL = "https://raw.githubusercontent.com/matthewproctor/australianpostcodes/master/australian_postcodes.csv"
POA_URL = "https://raw.githubusercontent.com/Offbeatmammal/AU_Postcode_Map/master/POA_2021_AUST_GDA2020_15percent.json"
IMMI_URL = "https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/work-holiday-462/specified-462-work"

# Simplification: ~330 m tolerance (deg) + 4-decimal coordinate rounding (~11 m).
# Good for national/state zoom; ~8 MB raw / ~2 MB gzipped.
SIMPLIFY_TOL = 0.003
COORD_DECIMALS = 4

# Area key -> human label + the job types each area unlocks (from the immi page).
AREAS = {
    "remote":   ("Remote and Very Remote Australia", ["Tourism & hospitality"]),
    "northern": ("Northern Australia",
                 ["Tourism & hospitality", "Plant & animal cultivation",
                  "Fishing & pearling", "Tree farming & felling", "Construction"]),
    "regional": ("Regional Australia", ["Plant & animal cultivation", "Construction"]),
    "bushfire": ("Bushfire declared areas", ["Bushfire recovery work"]),
    "disaster": ("Natural disaster declared areas",
                 ["Flood / cyclone / severe-weather recovery work"]),
}
NAME_TO_KEY = {v[0]: k for k, v in AREAS.items()}

STATE = {
    "new south wales": "NSW", "victoria": "VIC", "queensland": "QLD",
    "south australia": "SA", "western australia": "WA", "tasmania": "TAS",
    "northern territory": "NT", "australian capital territory": "ACT",
    "norfolk island": "NORFOLK", "jervis bay": "JBT",
}


def ensure_inputs():
    if not os.path.exists(CSV_PATH):
        print("downloading postcode CSV ...")
        urllib.request.urlretrieve(CSV_URL, CSV_PATH)
    if not os.path.exists(POA_PATH):
        print("downloading ABS POA 2021 GeoJSON ...")
        urllib.request.urlretrieve(POA_URL, POA_PATH)
    if not os.path.exists(HTML_PATH):
        print("downloading immi specified-462-work page ...")
        req = urllib.request.Request(IMMI_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(HTML_PATH, "wb") as f:
            f.write(r.read())


def strip_tags(t):
    return re.sub(r"<[^>]+>", " ", html.unescape(t)).replace("\xa0", " ")


def parse_postcodes(cell):
    """(whole_state, [postcodes]) from a table cell's HTML."""
    txt = strip_tags(cell)
    low = txt.lower()
    if "all areas" in low or re.search(r"all\s+postcodes", low) or low.strip() in ("all", "all."):
        return True, []
    pcs = []
    for a, b in re.findall(r"(\d{3,4})\s*to\s*(\d{3,4})", txt):
        a, b = int(a), int(b)
        if 0 <= b - a < 2000:
            pcs += [f"{n:04d}" for n in range(a, b + 1)]
    for s in re.findall(r"\b(\d{3,4})\b", re.sub(r"\d{3,4}\s*to\s*\d{3,4}", " ", txt)):
        pcs.append(f"{int(s):04d}")
    return False, sorted(set(pcs))


def parse_area_tables(block_html):
    states = {}
    for tab in re.findall(r"<table.*?</table>", block_html, flags=re.S | re.I):
        for tr in re.findall(r"<tr.*?</tr>", tab, flags=re.S | re.I):
            tds = re.findall(r"<td.*?</td>", tr, flags=re.S | re.I)
            if len(tds) < 2:
                continue
            st = strip_tags(tds[0]).strip().lower()
            code = STATE.get(st) or next((v for k, v in STATE.items() if k in st), None)
            if not code:
                continue
            whole, pcs = parse_postcodes(tds[1])
            e = states.setdefault(code, {"whole_state": False, "postcodes": set()})
            e["whole_state"] |= whole
            e["postcodes"].update(pcs)
    return states


def load_areas():
    raw = open(HTML_PATH, encoding="utf-8", errors="ignore").read()
    i = raw.find("PageSchemaHiddenField")
    if i < 0:
        sys.exit("ERROR: PageSchemaHiddenField not found in immi HTML")
    m = re.search(r'value="', raw[i:])
    vstart = i + m.end()
    val = raw[vstart:raw.find('"', vstart)]
    blocks = json.loads(html.unescape(val))["content"]
    out = {}
    for b in blocks:
        key = NAME_TO_KEY.get(b.get("text"))
        if key:
            out[key] = parse_area_tables(b.get("block", ""))
    missing = set(AREAS) - set(out)
    if missing:
        sys.exit(f"ERROR: areas not parsed: {missing}")
    return out


def load_postcode_csv():
    """postcode -> (state, [lng,lat], locality)."""
    by_pc_state = defaultdict(Counter)
    centroid, locality = {}, {}
    with open(CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pc = (row.get("postcode") or "").strip()
            if not pc.isdigit():
                continue
            pc = f"{int(pc):04d}"
            st = (row.get("state") or "").strip().upper()
            if st:
                by_pc_state[pc][st] += 1
            try:
                lng, lat = float(row["long"]), float(row["lat"])
                if lng and lat and pc not in centroid:
                    centroid[pc] = [round(lng, 4), round(lat, 4)]
                    locality[pc] = (row.get("locality") or "").strip().title()
            except (ValueError, KeyError):
                pass
    state = {pc: c.most_common(1)[0][0] for pc, c in by_pc_state.items()}
    return state, centroid, locality


def expand_area_sets(areas, pc_state):
    """Per area: enumerated postcodes + every postcode of any whole-state grant."""
    sets = {}
    for key, states in areas.items():
        s = set()
        for stcode, cell in states.items():
            s.update(cell["postcodes"])
            if cell["whole_state"]:
                if stcode == "NORFOLK":
                    s.update(pc for pc in pc_state if pc.startswith("2899"))
                    s.add("2899")
                else:
                    s.update(pc for pc, st in pc_state.items() if st == stcode)
        sets[key] = s
    return sets


def round_coords(o, nd):
    if isinstance(o, float):
        return round(o, nd)
    if isinstance(o, list):
        return [round_coords(x, nd) for x in o]
    return o


def build_geojson(area_sets, pc_state):
    from shapely.geometry import shape, mapping
    poa = json.load(open(POA_PATH, encoding="utf-8"))
    # Explicit, COLLISION-FREE short flag codes (remote & regional both start with
    # "r", so first-letter keys would clash). Order = paint precedence reference.
    FLAG = {"remote": "r", "northern": "n", "regional": "g",
            "bushfire": "b", "disaster": "d"}
    feats, area_counts = [], Counter()
    for ft in poa["features"]:
        pc = ft["properties"]["POA_CODE21"]
        try:
            g = shape(ft["geometry"]).simplify(SIMPLIFY_TOL, preserve_topology=True)
            if g.is_empty:
                continue
            geom = mapping(g)
            geom["coordinates"] = round_coords(geom["coordinates"], COORD_DECIMALS)
        except Exception:
            geom = ft["geometry"]
        props = {"p": pc, "s": pc_state.get(pc, "")}
        for k in ["remote", "northern", "regional", "bushfire", "disaster"]:
            on = 1 if pc in area_sets[k] else 0
            props[FLAG[k]] = on
            if on:
                area_counts[k] += 1
        feats.append({"type": "Feature", "properties": props, "geometry": geom})
    fc = {"type": "FeatureCollection", "features": feats}
    out = os.path.join(DATA, "eligible_poa.geojson")
    s = json.dumps(fc, separators=(",", ":"))
    open(out, "w", encoding="utf-8").write(s)
    print(f"  wrote {out}  ({len(s)/1e6:.2f} MB raw, ~{len(gzip.compress(s.encode()))/1e6:.2f} MB gzip, {len(feats)} polygons)")
    return area_counts


def build_lookup(area_sets, pc_state, centroid, locality):
    """Every known postcode -> flags + centroid for the search box."""
    all_pcs = set(pc_state) | set().union(*area_sets.values())
    lut = {}
    for pc in sorted(all_pcs):
        flags = "".join("1" if pc in area_sets[k] else "0"
                        for k in ["remote", "northern", "regional", "bushfire", "disaster"])
        lut[pc] = {
            "s": pc_state.get(pc, ""),
            "f": flags,                      # r,n,g,b,d as a 5-char bit string
            "c": centroid.get(pc),           # [lng,lat] or null
            "l": locality.get(pc, ""),
        }
    out = os.path.join(DATA, "postcodes.json")
    s = json.dumps(lut, separators=(",", ":"))
    open(out, "w", encoding="utf-8").write(s)
    print(f"  wrote {out}  ({len(s)/1e6:.2f} MB, {len(lut)} postcodes)")


def all_localities():
    """postcode -> set of every distinct locality/suburb name in the CSV.

    The postcode lookup only keeps ONE primary locality per postcode, but a
    postcode usually spans several suburbs (2026 = Bondi, Bondi Beach, North
    Bondi, Tamarama...). Name search needs all of them, so this collects the
    full set."""
    locs = defaultdict(set)
    with open(CSV_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pc = (row.get("postcode") or "").strip()
            if not pc.isdigit():
                continue
            pc = f"{int(pc):04d}"
            # canonicalise the curly apostrophe (U+2019) to a straight one so
            # "O'Connor" and "O’Connor" don't appear as two identical results
            name = (row.get("locality") or "").strip().title().replace("’", "'")
            if name:
                locs[pc].add(name)
    return locs


def build_places(area_sets, pc_state):
    """Flat [name, postcode] index powering search-by-area-name.

    Every distinct suburb/locality name, restricted to postcodes we actually
    know (so every result resolves to a real record). State is derived from the
    postcode at lookup time, so it isn't duplicated here."""
    valid = set(pc_state) | set().union(*area_sets.values())
    locs = all_localities()
    entries = []
    for pc in valid:
        for name in locs.get(pc, ()):
            entries.append([name, pc])
    entries.sort(key=lambda e: (e[0].lower(), e[1]))
    out = os.path.join(DATA, "places.json")
    s = json.dumps({"places": entries}, separators=(",", ":"))
    open(out, "w", encoding="utf-8").write(s)
    print(f"  wrote {out}  ({len(s)/1e6:.2f} MB, {len(entries)} place names)")


def main():
    ensure_inputs()
    print("parsing immi specified-462-work tables ...")
    areas = load_areas()
    pc_state, centroid, locality = load_postcode_csv()
    area_sets = expand_area_sets(areas, pc_state)
    for k, s in area_sets.items():
        print(f"  {AREAS[k][0]:35} eligible postcodes: {len(s)}")
    print("building geojson ...")
    area_counts = build_geojson(area_sets, pc_state)
    print("building postcode lookup ...")
    build_lookup(area_sets, pc_state, centroid, locality)
    print("building place-name index ...")
    build_places(area_sets, pc_state)
    meta = {
        "source": "immi.homeaffairs.gov.au specified-462-work (LIN 18/197 / F2018L01539)",
        "boundaries": "ABS Postal Areas (POA) 2021, GDA2020, CC BY 4.0",
        "postcode_csv": "matthewproctor/australianpostcodes",
        "simplify_tolerance_deg": SIMPLIFY_TOL,
        "areas": {k: {"label": AREAS[k][0], "jobs": AREAS[k][1],
                      "poa_count": area_counts[k], "postcode_count": len(area_sets[k])}
                  for k in AREAS},
    }
    json.dump(meta, open(os.path.join(DATA, "meta.json"), "w"), indent=1)
    print("  wrote data/meta.json")
    print("done.")


if __name__ == "__main__":
    main()
