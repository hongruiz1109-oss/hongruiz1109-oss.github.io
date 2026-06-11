#!/usr/bin/env python3
"""
fetch_rankings.py — Live weekly market-share data for the OpenRouter page.

Background
----------
Until ~2026-05-05 the project scraped 90 days of *daily, per-model* token
counts from each model page's embedded RSC payload (see extend_panel.py).
OpenRouter removed that payload, freezing the daily panel. The data then moved
behind the /rankings page's Next.js **server actions**, which this script used
to talk to directly (self-healing the rotating action IDs).

As of ~2026-06 those server actions started rejecting anonymous calls with
``{"__kind":"ERR","error":{"error":{"message":"Authorization invalid",...}}}``
(401) / 500, so the action route is dead for logged-out scraping.

Current source
--------------
We now read the **public, anonymous JSON endpoint** that backs the rankings UI:

    GET https://openrouter.ai/api/frontend/rankings/models
    -> {"data": [{"date","model_permaslug","variant",
                  "total_prompt_tokens","total_completion_tokens",...}, ...]}

That endpoint only exposes a short rolling window (~3 healthy recent days; the
oldest day or two come back sparse/partial). To keep producing a *multi-week*
trend we therefore:

  • FREEZE the pre-outage weekly series (everything the old server-action
    pipeline had already collected, through its last week) and carry it
    verbatim;
  • ACCUMULATE healthy daily snapshots from the public endpoint into
    rankings_raw.json (idempotent — re-fetching a date overwrites it), and
    rebuild the recent ("live") weeks from that accumulator on every run.

So history is preserved, and the live tail re-grows one day at a time. There is
an unavoidable gap across the outage weeks (2026-06-01) where no healthy daily
data was captured; the chart simply has no point there.

Creator series is derived by aggregating models on their ``creator/...`` prefix.

Fail-loud
---------
A transport/parse failure or an empty endpoint response exits non-zero so the
GitHub Action turns red instead of silently shipping stale data. A *successful*
fetch that merely adds no new healthy day is a no-op (the commit step sees no
diff), not an error.

Usage:
    python scripts/fetch_rankings.py            # fetch + write rankings_live.js
    python scripts/fetch_rankings.py --dry-run  # fetch + print, do not write
"""

import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
OUT_JS = ROOT / "assets" / "js" / "rankings_live.js"
OUT_RAW = ROOT / "assets" / "data" / "rankings_raw.json"

MODELS_URL = "https://openrouter.ai/api/frontend/rankings/models"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

# A day is "healthy" (complete enough to bucket) only if it clears both bars.
# The endpoint occasionally returns a near-empty straggler row for an old date
# (a couple of models, ~1e7 tokens); those must not pollute a weekly bucket.
MIN_MODELS_PER_DAY = 50
MIN_TOKENS_PER_DAY = 1e12

TOP_N = 9  # named entities per week; the rest collapse into an "others" bucket


# ── public endpoint ──────────────────────────────────────────────────────────

def fetch_daily(session: requests.Session) -> dict[str, dict[str, float]]:
    """Return {date: {model_permaslug: tokens}} from the public rankings feed.

    Tokens = prompt + completion, summed across a model's variants. Fail-loud
    on transport/parse error or an empty payload.
    """
    try:
        r = session.get(MODELS_URL, headers=HEADERS, timeout=60)
        r.raise_for_status()
        rows = r.json().get("data")
    except Exception as e:  # noqa: BLE001 — any failure here must turn the run red
        sys.exit(f"[FATAL] Could not fetch {MODELS_URL}: {e}")

    if not isinstance(rows, list) or not rows:
        sys.exit("[FATAL] rankings/models returned no data — endpoint changed?")

    daily: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        d = (row.get("date") or "")[:10]
        slug = row.get("model_permaslug")
        if not d or not slug:
            continue
        tok = (row.get("total_prompt_tokens") or 0) + (row.get("total_completion_tokens") or 0)
        daily[d][slug] += tok
    return {d: dict(m) for d, m in daily.items()}


def healthy_days(daily: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """Keep only days complete enough to trust (drops sparse straggler dates)."""
    out = {}
    for d, models in daily.items():
        if len(models) >= MIN_MODELS_PER_DAY and sum(models.values()) >= MIN_TOKENS_PER_DAY:
            out[d] = models
    return out


# ── weekly bucketing ──────────────────────────────────────────────────────────

def week_start(day: str) -> str:
    """Monday (ISO date string) of the week containing `day` (YYYY-MM-DD)."""
    dt = date.fromisoformat(day)
    return (dt - timedelta(days=dt.weekday())).isoformat()


def topn_ys(totals: dict[str, float], others_label: str) -> dict[str, float]:
    """Reduce {entity: tokens} to top-N named entities + a residual bucket."""
    items = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    ys = dict(items[:TOP_N])
    ys[others_label] = sum(v for _, v in items[TOP_N:])
    return ys


def build_live_weeks(daily: dict[str, dict[str, float]], frozen_last_week: str):
    """Build model + creator weekly top-N series from accumulated daily data.

    Only weeks strictly newer than `frozen_last_week` are emitted, so the live
    tail attaches cleanly after the frozen history with no overlap. The frozen
    boundary week keeps its richer pre-outage value rather than being rebuilt
    from a partial day.
    """
    wk_model: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    wk_creator: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for day, models in daily.items():
        wk = week_start(day)
        if wk <= frozen_last_week:
            continue
        for slug, tok in models.items():
            wk_model[wk][slug] += tok
            wk_creator[wk][slug.split("/", 1)[0]] += tok

    model = [{"x": wk, "ys": topn_ys(wk_model[wk], "Others")} for wk in sorted(wk_model)]
    creator = [{"x": wk, "ys": topn_ys(wk_creator[wk], "others")} for wk in sorted(wk_creator)]
    return creator, model


# ── shares / HHI (unchanged) ──────────────────────────────────────────────────

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


# ── raw store (frozen history + accumulating daily) ───────────────────────────

def load_store() -> tuple[list, list, dict]:
    """Return (frozen_creator, frozen_model, daily) from rankings_raw.json.

    Migrates the legacy schema (top-level ``creator``/``model`` weekly series
    written by the old server-action pipeline) into the frozen baseline.
    """
    if not OUT_RAW.exists():
        return [], [], {}
    raw = json.loads(OUT_RAW.read_text(encoding="utf-8"))
    if "frozen_model" in raw:  # current schema
        return raw.get("frozen_creator", []), raw.get("frozen_model", []), raw.get("daily", {})
    # legacy schema → freeze whatever weekly series it carried
    return raw.get("creator", []), raw.get("model", []), {}


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"=== OpenRouter live rankings fetch — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} ===")

    session = requests.Session()
    fetched = fetch_daily(session)
    fresh = healthy_days(fetched)
    print(f"  endpoint: {len(fetched)} day(s), {len(fresh)} healthy "
          f"({', '.join(sorted(fresh)) or 'none'})")

    frozen_creator, frozen_model, daily = load_store()
    # Boundary = last week already present in the frozen baseline. Derived so it
    # tracks however far the pre-outage pipeline got (and any future re-freeze),
    # rather than a brittle constant.
    frozen_last_week = max(
        (frozen_creator[-1]["x"] if frozen_creator else ""),
        (frozen_model[-1]["x"] if frozen_model else ""),
    )
    print(f"  frozen history: creator {len(frozen_creator)}w / model {len(frozen_model)}w "
          f"(through {frozen_last_week or 'n/a'})")

    # Accumulate: idempotently overwrite each healthy day's snapshot.
    before = len(daily)
    daily.update(fresh)
    print(f"  accumulated daily snapshots: {before} → {len(daily)}")

    live_creator, live_model = build_live_weeks(daily, frozen_last_week)
    print(f"  live weeks rebuilt: creator {len(live_creator)} / model {len(live_model)}"
          + (f" (latest {live_model[-1]['x']})" if live_model else ""))

    creator = frozen_creator + live_creator
    model = frozen_model + live_model
    if not creator or not model:
        sys.exit("[FATAL] No creator/model series available (frozen + live both empty).")

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
            "note": "Weekly token-based market share. History through "
                    f"{frozen_last_week} is frozen from the former /rankings "
                    "server-action feed; newer weeks are rebuilt from the public "
                    "openrouter.ai/api/frontend/rankings/models endpoint and "
                    "accumulate daily. Top entities + an 'others' residual bucket.",
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
        return

    OUT_RAW.write_text(json.dumps({
        "schema": 2,
        "source": MODELS_URL,
        "frozen_last_week": frozen_last_week,
        "frozen_creator": frozen_creator,
        "frozen_model": frozen_model,
        "daily": daily,
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
