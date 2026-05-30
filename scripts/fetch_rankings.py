#!/usr/bin/env python3
"""
fetch_rankings.py — Live weekly market-share data for the OpenRouter page.

Background
----------
Until ~2026-05-05 the project scraped 90 days of *daily, per-model* token
counts from each model page's embedded RSC payload (see extend_panel.py).
OpenRouter removed that payload from the model pages, which silently froze the
daily panel. The equivalent data now lives only behind the /rankings page's
Next.js **server actions** (the "market share" charts).

This script talks to those server actions directly and produces a *weekly,
token-based* market-share dataset that can keep updating going forward. It is
intentionally separate from the frozen historical panel (different cadence,
different unit: tokens vs. requests, top-N + "others" vs. all models).

Self-healing
------------
Server-action IDs rotate on every OpenRouter front-end deploy. We therefore
re-discover them on each run: fetch /rankings, read its JS chunks, collect all
`createServerReference("<id>")` IDs that live in chunks mentioning the rankings
components, call each candidate, and classify by *response shape* (not by name).
Shape classification is resilient to renames.

Fail-loud
---------
Any failure to discover or fetch the two core series (creator + model token
time series) exits non-zero so the GitHub Action turns red instead of
committing a fake "update".

Usage:
    python scripts/fetch_rankings.py            # fetch + write rankings_live.js
    python scripts/fetch_rankings.py --dry-run  # fetch + print, do not write
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
OUT_JS = ROOT / "assets" / "js" / "rankings_live.js"
OUT_RAW = ROOT / "assets" / "data" / "rankings_raw.json"

RANKINGS_URL = "https://openrouter.ai/rankings"
BASE = "https://openrouter.ai"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA}

# Chunks that mention any of these markers are scanned for server-action IDs.
# (Markers are the rankings client-component names registered via e.s([...]).)
COMPONENT_MARKERS = (
    "ModelRankings", "WeeklyActivity", "AppRankings",
    "Benchmark", "benchmark", "Rankings",
)

CHUNK_RE = re.compile(r'/_next/static/chunks/[^"\\]+\.js')
ACTION_RE = re.compile(r'createServerReference\)\("([0-9a-f]{32,})"')


# ── server-action discovery ──────────────────────────────────────────────────

def discover_action_ids(session: requests.Session) -> list[str]:
    """Collect candidate server-action IDs from the rankings page chunks."""
    page = session.get(RANKINGS_URL, headers=HEADERS, timeout=30)
    page.raise_for_status()
    chunks = sorted(set(CHUNK_RE.findall(page.text)))
    print(f"  rankings page: {len(chunks)} JS chunks")

    ids: list[str] = []
    seen: set[str] = set()
    for c in chunks:
        try:
            t = session.get(BASE + c, headers=HEADERS, timeout=30).text
        except Exception:
            continue
        if not any(m in t for m in COMPONENT_MARKERS):
            continue
        for aid in ACTION_RE.findall(t):
            if aid not in seen:
                seen.add(aid)
                ids.append(aid)
    print(f"  discovered {len(ids)} candidate action IDs in rankings chunks")
    if not ids:
        sys.exit("[FATAL] No server-action IDs found — OpenRouter page structure changed.")
    return ids


def call_action(session: requests.Session, action_id: str, body: str = "[]"):
    """Invoke a Next.js server action; return the parsed payload (flight row 1)."""
    r = session.post(
        RANKINGS_URL,
        headers={**HEADERS, "Next-Action": action_id,
                 "Content-Type": "text/plain;charset=UTF-8"},
        data=body, timeout=30,
    )
    if r.status_code != 200:
        return None
    m = re.search(r'\n1:(.*)', r.text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


# ── shape classifiers ─────────────────────────────────────────────────────────

def _is_token_series(payload) -> list | None:
    """Return the [{x, ys:{key: tokens}}] list if payload looks like one."""
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list) or not data:
        return None
    p0 = data[0]
    if not (isinstance(p0, dict) and "x" in p0 and isinstance(p0.get("ys"), dict)):
        return None
    return data


def classify(payload):
    """Classify a server-action payload into 'creator' / 'model' / 'bench' / None."""
    if isinstance(payload, dict) and "intelligence" in payload:
        return "bench", payload
    series = _is_token_series(payload)
    if series is not None:
        keys = list(series[-1]["ys"].keys())
        # model series keys are "creator/model"; creator series keys are bare.
        slashed = sum("/" in k for k in keys)
        return ("model" if slashed >= max(1, len(keys) // 2) else "creator"), series
    return None, None


# ── transforms ─────────────────────────────────────────────────────────────────

def shares_from_series(series: list, other_keys=("others", "Others", "other")) -> dict:
    """Convert a token time series into per-key % shares + weekly HHI.

    HHI is computed over the named entities plus the residual 'others' bucket
    treated as a single block — an upper bound on true concentration, since the
    long tail is lumped. Labelled approximate in the UI.
    """
    points = []
    hhi = []
    keys = set()
    for pt in series:
        ys = pt["ys"]
        total = sum(ys.values())
        if total <= 0:
            continue
        row = {k: round(v / total * 100, 4) for k, v in ys.items()}
        points.append({"x": pt["x"], "shares": row})
        keys.update(ys.keys())
        hhi.append({"x": pt["x"],
                    "hhi": round(sum((v / total) ** 2 for v in ys.values()) * 10000, 2)})
    # stable key ordering by latest share, "others" last
    latest = points[-1]["shares"] if points else {}
    named = sorted((k for k in keys if k.lower() not in other_keys),
                   key=lambda k: latest.get(k, 0), reverse=True)
    others = [k for k in keys if k.lower() in other_keys]
    return {"order": named + others, "points": points, "hhi": hhi}


def bench_table(payload: dict) -> list:
    """Flatten the benchmark payload to [{slug, name, intelligence}]."""
    out = []
    for row in payload.get("intelligence", []):
        slug = row.get("openrouter_slug") or row.get("heuristic_openrouter_slug") or row.get("uid")
        out.append({
            "slug": slug,
            "name": row.get("aa_name") or row.get("uid"),
            "intelligence": row.get("score"),
        })
    return out


# ── main ────────────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"=== OpenRouter live rankings fetch — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} ===")

    session = requests.Session()
    action_ids = discover_action_ids(session)

    # Collect ALL candidates per kind, then keep the most complete one. Several
    # category-specific rankings (audio/image/tool…) share the same shape but
    # cover fewer weeks/entities; the global series has the widest coverage.
    cands = {"creator": [], "model": [], "bench": []}
    for aid in action_ids:
        payload = call_action(session, aid)
        if payload is None:
            continue
        kind, data = classify(payload)
        if kind in cands:
            cands[kind].append((aid, data, payload))
        time.sleep(0.3)

    def best_series(items):
        # rank by #weeks, then #entities in the latest week
        return max(items, key=lambda it: (len(it[1]), len(it[1][-1]["ys"])), default=None)

    best_creator = best_series(cands["creator"])
    best_model = best_series(cands["model"])
    best_bench = max(cands["bench"],
                     key=lambda it: len(it[2].get("intelligence", [])), default=None)

    if not best_creator or not best_model:
        sys.exit("[FATAL] Could not locate creator/model token series — aborting (no fake update).")

    raw = {}
    creator = best_creator[1]; raw["creator_action"] = best_creator[0]
    model = best_model[1];     raw["model_action"] = best_model[0]
    bench = best_bench[2] if best_bench else None
    if best_bench:
        raw["bench_action"] = best_bench[0]
    print(f"  [creator] {best_creator[0][:12]} — {len(creator)} weeks, "
          f"{len(creator[-1]['ys'])} entities, latest {creator[-1]['x']}")
    print(f"  [model]   {best_model[0][:12]} — {len(model)} weeks, "
          f"{len(model[-1]['ys'])} entities, latest {model[-1]['x']}")
    if bench:
        print(f"  [bench]   {best_bench[0][:12]} — {len(bench.get('intelligence', []))} models")

    creator_shares = shares_from_series(creator)
    model_shares = shares_from_series(model)

    # latest-week top models (tokens) for the bar chart
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
            "note": "Weekly token-based market share from the OpenRouter /rankings "
                    "server actions. Top entities + an 'others' residual bucket. "
                    "Distinct from the frozen daily request panel.",
        },
        "creator": creator_shares,
        "model": model_shares,
        "top_models": top_models,
        "benchmark": bench_table(bench) if bench else [],
    }

    if dry_run:
        print(json.dumps(out["meta"], ensure_ascii=False, indent=2))
        print(f"[DRY RUN] creator entities: {out['creator']['order']}")
        return

    OUT_RAW.write_text(json.dumps(raw | {"creator": creator, "model": model}, ensure_ascii=False),
                       encoding="utf-8")
    js = ("// Auto-generated by scripts/fetch_rankings.py — do not edit by hand\n"
          f"// generated: {out['meta']['generated_at']}  |  weeks: "
          f"{out['meta']['week_min']} → {out['meta']['week_max']}\n"
          "const RANKINGS_LIVE = " + json.dumps(out, ensure_ascii=False) + ";\n")
    OUT_JS.write_text(js, encoding="utf-8")
    print(f"Wrote {OUT_JS.relative_to(ROOT)} ({OUT_JS.stat().st_size/1024:.1f} KB), "
          f"weeks {out['meta']['week_min']} → {out['meta']['week_max']}")


if __name__ == "__main__":
    main()
