# Project Knowledge — AU Working Holiday (462) Specified-Work Map

Background, decisions, and rules behind this project. Read this before changing the data
pipeline or the eligibility logic.

---

## 1. What this is

A static, single-purpose web map showing the Australian postcode areas where **specified work**
counts toward a **second or third Working Holiday (subclass 462) visa**, colour-coded by
designated area and the job types each area unlocks.

It is an informational aid, **not legal advice**. The source of truth is always the official
Home Affairs page; lists change over time.

---

## 2. The key correction (read this first)

The project was originally pointed at the wrong list. The page first supplied —
*"Designated regional area postcodes"*
(`.../skill-occupation-list/regional-postcodes`) — is the **skilled-migration** regional
definition for subclasses **491 / 494 / 191**. Its own text says it "offers regional incentives
for **skilled migrants**." It has nothing to do with Working Holiday Makers and is **not used**
by this project.

The correct source for a 462 holder is:
**https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/work-holiday-462/specified-462-work**
(legal basis **LIN 18/197**, Federal Register **F2018L01539**).

Two separate truths drive the design:
1. **Where a 462 holder can work day-to-day = anywhere** in Australia for 12 months (only limit:
   max 6 months per employer, condition 8547). So "where can she work" is not a postcode subset.
2. **Where "regional" matters = only to earn a 2nd/3rd-year visa** via specified work
   (3 months for the 2nd, 6 months for the 3rd). That is what the map colours.

---

## 3. The actual 462 rules (area × job type)

Eligibility depends on **both** the area **and** the kind of work. The five designated areas and
what each unlocks:

| Area (map colour) | Job types that count there |
|---|---|
| **Northern Australia** (purple) | Tourism & hospitality, Plant & animal cultivation, Fishing & pearling, Tree farming & felling, Construction — *everything* |
| **Regional Australia** (green) | Plant & animal cultivation, Construction *(the classic "farm work")* |
| **Remote & Very Remote** (orange) | Tourism & hospitality |
| **Bushfire declared areas** (pink) | Bushfire recovery work (declared periods) |
| **Natural disaster declared areas** (blue) | Flood / cyclone / severe-weather recovery |

Consequences worth remembering:
- **A single job type can span two areas.** e.g. *Tourism & hospitality* counts in **Northern
  ∪ Remote & Very Remote** — so "where can I work as a barista" ≈ purple + orange, not just one.
- **Fishing, pearling and tree-felling count in Northern Australia ONLY.**
- **Regional Australia is huge** — for cultivation/construction, almost everywhere *outside the
  capital cities* qualifies (all of SA/TAS/NT/Norfolk + most of regional NSW/VIC/QLD/WA). That is
  why the map is mostly green; the **hatched** zones (Sydney, Melbourne, Brisbane, Perth,
  Adelaide) are the main places it does **not** count.
- Tourism & hospitality is a **462-only** category (not available to the 417 visa).

### Map colour precedence
Each postcode is coloured by the *most permissive* area it falls in, in this order:
**Northern > Regional > Remote**. This is deliberate: a farm town that is in both Regional and
Remote reads as green (cultivation) rather than orange (tourism), which matches the typical
2nd-year "farm work" goal. The hover/click panel still lists *all* areas a postcode belongs to.
Toggling layers off reveals the areas underneath.

---

## 4. Architecture decision (why this stack)

Researched before building. Summary of the verdict:

- **MapLibre GL JS, not Google Maps.** Google's data-driven boundary styling **does not cover
  Australian postal codes** (only country / state / locality), so Google literally cannot colour
  AU postcodes from its own tiles. The only Google path is drawing our own polygons over a Google
  basemap via deck.gl — which still needs a **billable** Maps API key. MapLibre is free, needs no
  key, renders ~2,644 WebGL polygons smoothly, and deploys as a fully static site.
- **Plain simplified GeoJSON, not vector tiles/PMTiles.** After simplification the layer is ~8 MB
  (~2 MB gzipped) — small enough to load as a single GeoJSON source. PMTiles/tippecanoe would be
  warranted only above ~5–10 MB.
- **No backend, no bundler.** One `index.html` + a `data/` folder. Basemap: CARTO Positron (no
  key). Build step: one Python script.

Runner-up if "must look like Google" were a hard requirement: Google basemap + deck.gl GPU
overlay (same polygons, paid key). It was not required.

---

## 5. Data sources & pipeline

Three sources feed the build (`build/process.py`):

1. **Eligibility (which postcode → which area/job): ALL from the specified-462-work page.**
   The postcode tables (Tables 1–6) are embedded in the page HTML as JSON inside a hidden
   `PageSchemaHiddenField`. The parser expands inclusive ranges ("4417 to 4420") and reads the
   five areas. *Nuance:* the page grants some areas as "All postcodes in NT/SA/TAS/Norfolk" without
   listing them — those whole-state grants are expanded using the postcode→state CSV below.
2. **Boundaries: ABS Postal Areas (POA) 2021** (ASGS Edition 3, GDA2020), **CC BY 4.0**. 2,644
   polygons. The map draws *all* of them and tags each with the area flags; codes not on the immi
   lists render as "not eligible" (grey/hatched). POAs are statistical *approximations* of
   postcodes — they don't perfectly match Australia Post boundaries, and PO-box-only postcodes
   have no polygon.
3. **Postcode → state + centroid: `matthewproctor/australianpostcodes`** (community, public
   domain). Used to expand the whole-state grants and to power the search box (fly-to + existence).

**Pipeline:** download POA + CSV (immi HTML is bundled) → parse the 5 area sets → expand
whole-state grants → for each POA polygon, simplify (~330 m) + round coords (4 dp) + tag flags
`r/n/g/b/d` → write `data/eligible_poa.geojson`, `data/postcodes.json`, `data/meta.json`.

Re-run with: `pip install shapely && python build/process.py` (re-scrapes immi).

### Flag encoding (don't repeat the bug)
Short flag codes are **explicit** because `"remote"` and `"regional"` both start with `r`:
`remote→r, northern→n, regional→g, bushfire→b, disaster→d`. The lookup `f` bit-string order is
`r,n,g,b,d`. (An earlier `props[name[0]]` collision silently dropped the regional flag — fixed.)

---

## 6. Verification done

- **Independent re-parse** of the immi page (a second, separate parser) cross-checked against both
  output files: **0 mismatches across all 2,644 polygons and 4,145 postcodes.** Spot-checks
  confirmed e.g. 2000 Sydney / 6000 Perth = not eligible, 3000 Melbourne = disaster-only,
  0800 Darwin & 4870 Cairns = all areas, range edges (4417–4420) correct.
- Rendered & interaction-tested headless (DevTools Protocol) on desktop + mobile: search, hover,
  click, layer toggles, detail panel — no JS errors.
- Adversarial review pass drove fixes: XSS-escaping of data strings, data-load error handling,
  `aria-live`/keyboard/reduced-motion accessibility, contrast, search debounce, the empty-toggle
  paint bug (a branch-less `case` was being rejected, leaving the last colour stuck).

---

## 7. Caveats & maintenance

- **Lists change** — especially bushfire/disaster (declaration-based) and immi's periodic
  revisions. The committed data is a **snapshot**; re-run the build to refresh.
- **POA ≠ legal postcode boundaries** — statistical approximation only.
- **Not legal advice** — confirm any real visa decision against the official page.
- If ABS ships **ASGS Edition 4** (expected ~2026), update the POA source URL in `process.py`.

---

## 8. File map

```
index.html            # the whole app (MapLibre map, layers, search, detail panel) — no build step
data/eligible_poa.geojson  # 2,644 simplified POA polygons tagged with area flags r/n/g/b/d + state
data/postcodes.json        # every postcode → {state, flags, centroid, locality} for search
data/meta.json             # build provenance + per-area counts
build/process.py           # the data pipeline (scrape → tag → simplify → emit)
build/raw/                 # raw inputs (gitignored; re-downloaded on build)
docs/preview.png           # README screenshot
README.md                  # user-facing: what it shows, run, deploy, attribution
KNOWLEDGE.md               # this file
```
