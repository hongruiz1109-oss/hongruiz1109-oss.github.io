#!/usr/bin/env python3
"""
fetch_rankings.py — Live weekly market-share data for the OpenRouter page.

Background
----------
This feed has moved repeatedly as OpenRouter reworked its site:

  • Until ~2026-05-05 we scraped 90 days of *daily, per-model* token counts from
    each model page's embedded RSC payload (see extend_panel.py). OpenRouter
    removed that payload, freezing the old daily panel.
  • The data then moved behind the /rankings page's Next.js server actions
    (self-healing rotating action IDs), which later started rejecting anonymous
    calls.
  • The workaround became the public JSON endpoint
    ``/api/frontend/rankings/models`` — a short rolling daily window we
    accumulated and froze week by week.
  • As of ~2026-06-19 the whole anonymous ``/api/frontend/*`` surface was
    restructured under a ``/v1/`` segment and, crucially, OpenRouter now exposes
    purpose-built, already-bucketed **weekly** endpoints that back the rankings
    charts directly.

Current source (anonymous, public)
-----------------------------------
    GET /api/frontend/v1/rankings/market-share
        -> {"data": [{"x": "<Mon YYYY-MM-DD>",
                      "ys": {"<creator>": <tokens>, ..., "others": <tokens>}}, ...]}

    GET /api/frontend/v1/rankings/model-rankings-chart
        -> {"data": {"data": [{"x": "<Mon YYYY-MM-DD>",
                              "ys": {"<creator/model>": <tokens>, ..., "Others": <tokens>}}, ...],
                     "cachedAt": ...}}

Both return the full continuous weekly history (~52 weeks) and are already
reduced server-side to the top entities plus an "others" residual — so the
former freeze / daily-accumulate machinery (and the 2026-06 outage gap it left
behind) is no longer needed. We read each series, convert tokens to weekly %
shares + HHI, and emit assets/js/rankings_live.js.

Fail-loud
---------
A transport/parse failure or an empty endpoint response exits non-zero so the
GitHub Action turns red instead of silently shipping stale data.

Usage:
    python scripts/fetch_rankings.py            # fetch + write rankings_live.js
    python scripts/fetch_rankings.py --dry-run  # fetch + print, do not write
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
OUT_JS = ROOT / "assets" / "js" / "rankings_live.js"
OUT_RAW = ROOT / "assets" / "data" / "rankings_raw.json"

BASE = "https://openrouter.ai/api/frontend/v1/rankings"
CREATOR_URL = f"{BASE}/market-share"
MODEL_URL = f"{BASE}/model-rankings-chart"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "application/json"}


# ── public endpoints ───────────────────────────────────────────────────────

def fetch_weekly(session: requests.Session, url: str) -> list:
    """Return a ``[{"x": week, "ys": {entity: tokens}}, ...]`` weekly series.

    Handles both the flat ``{"data": [...]}`` shape (market-share) and the
    nested ``{"data": {"data": [...]}}`` shape (model-rankings-chart). Fail-loud
    on transport/parse error or an empty/unexpected payload.
    """
    try:
        r = session.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        data = r.json().get("data")
    except Exception as e:  # noqa: BLE001 — any failure here must turn the run red
        sys.exit(f"[FATAL] Could not fetch {url}: {e}")

    if isinstance(data, dict):  # nested {"data": {"data": [...], "cachedAt": ...}}
        data = data.get("data")

    if not isinstance(data, list) or not data:
        sys.exit(f"[FATAL] {url} returned no weekly data — endpoint changed?")

    for pt in data:
        if not isinstance(pt, dict) or "x" not in pt or not isinstance(pt.get("ys"), dict):
            sys.exit(f"[FATAL] {url} returned an unexpected row shape — endpoint changed?")
    return data


# ── shares / HHI ───────────────────────────────────────────────────────────

def shares_from_series(series: list, other_keys=("others", "Others", "other")) -> dict:
    """Convert a token time series into per-key % shares + weekly HHI.

    HHI is computed over the named entities plus the residual 'others' bucket
    treated as a single block — an upper bound on true concentration, since the
    long tail is lumped. Labelled approximate in the UI.
    """
    points, hhi, keys = [], [], set()
    for pt in series:
        ys = pt["ys"]
        total = sum(ys.values())
        if total <= 0:
            continue
        points.append({"x": pt["x"], "shares": {k: round(v / total * 100, 4) for k, v in ys.items()}})
        keys.update(ys.keys())
        hhi.append({"x": pt["x"],
                    "hhi": round(sum((v / total) ** 2 for v in ys.values()) * 10000, 2)})
    latest = points[-1]["shares"] if points else {}
    named = sorted((k for k in keys if k.lower() not in other_keys),
                   key=lambda k: latest.get(k, 0), reverse=True)
    others = [k for k in keys if k.lower() in other_keys]
    return {"order": named + others, "points": points, "hhi": hhi}


# ── main ───────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"=== OpenRouter live rankings fetch — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} ===")

    session = requests.Session()
    creator = fetch_weekly(session, CREATOR_URL)
    model = fetch_weekly(session, MODEL_URL)
    print(f"  creator: {len(creator)}w ({creator[0]['x']} → {creator[-1]['x']})")
    print(f"  model:   {len(model)}w ({model[0]['x']} → {model[-1]['x']})")

    creator_shares = shares_from_series(creator)
    model_shares = shares_from_series(model)

    last = model[-1]["ys"]
    top_models = [{"model_id": k, "tokens": v}
                  for k, v in sorted(last.items(), key=lambda kv: kv[1], reverse=True)
                  if k.lower() not in ("others", "other")]

    out = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "week_min": creator[0]["x"],
            "week_max": creator[-1]["x"],
            "unit": "tokens",
            "note": "Weekly token-based market share, read from the public "
                    "openrouter.ai/api/frontend/v1/rankings endpoints "
                    "(market-share + model-rankings-chart). Top entities + an "
                    "'others' residual bucket, bucketed server-side.",
        },
        "creator": creator_shares,
        "model": model_shares,
        "top_models": top_models,
        "benchmark": [],  # intelligence feed is auth-gated; unused by the page
    }

    if dry_run:
        print(json.dumps(out["meta"], ensure_ascii=False, indent=2))
        print(f"[DRY RUN] creator order: {out['creator']['order']}")
        print(f"[DRY RUN] {len(creator)} creator weeks {creator[0]['x']} → {creator[-1]['x']}")
        print(f"[DRY RUN] top models latest: {[m['model_id'] for m in top_models[:5]]}")
        return

    OUT_RAW.write_text(json.dumps({
        "schema": 3,
        "sources": {"creator": CREATOR_URL, "model": MODEL_URL},
        "fetched_at": out["meta"]["generated_at"],
        "creator": creator,
        "model": model,
    }, ensure_ascii=False), encoding="utf-8")

    js = ("// Auto-generated by scripts/fetch_rankings.py — do not edit by hand\n"
          f"// generated: {out['meta']['generated_at']}  |  weeks: "
          f"{out['meta']['week_min']} → {out['meta']['week_max']}\n"
          "const RANKINGS_LIVE = " + json.dumps(out, ensure_ascii=False) + ";\n")
    OUT_JS.write_text(js, encoding="utf-8")
    print(f"Wrote {OUT_JS.relative_to(ROOT)} ({OUT_JS.stat().st_size/1024:.1f} KB), "
          f"weeks {out['meta']['week_min']} → {out['meta']['week_max']}")


if __name__ == "__main__":
    main()
