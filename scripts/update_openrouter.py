#!/usr/bin/env python3
"""
Auto-update assets/js/openrouter_data.js with fresh model data.

What this updates:
  • price_vs_intelligence  — fresh pricing from OpenRouter API
  • summary.n_models       — current model count
  • summary.date_updated   — today's date

What stays frozen:
  • Time-series (monthly_reasoning, creator shares, HHI, hhi_daily, etc.)
    These come from the original Wayback Machine panel — untouched.
  • perf_vs_usage          — cumulative lifetime counts, frozen.
  • intelligence_index     — kept from existing data; new models without
                             a known score are excluded from the scatter.

Usage:
    python scripts/update_openrouter.py
    python scripts/update_openrouter.py --dry-run

Env:
    OPENROUTER_API_KEY   optional — increases rate limits on the /models endpoint
"""

import json
import math
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "assets", "js", "openrouter_data.js")
OR_MODELS_URL = "https://openrouter.ai/api/v1/models"


# ── Helpers ──────────────────────────────────────────────────────────────────

def http_get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {})
    req.add_header("User-Agent", "portfolio-data-updater/1.0 (github.com/hongruiz1109-oss)")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_existing_data():
    """Parse openrouter_data.js and return the Python dict."""
    with open(DATA_JS, "r", encoding="utf-8") as f:
        content = f.read()
    # Strip the JS const declaration, grab the JSON object
    m = re.search(r"const OPENROUTER_DATA\s*=\s*(\{)", content)
    if not m:
        raise ValueError("Could not find 'const OPENROUTER_DATA = {' in the JS file")
    start = m.start(1)
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(content, start)
    return obj


def write_data_js(data):
    """Write the updated dict back as a JS file."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"// Auto-generated — do not edit by hand",
        f"// price_vs_intelligence updated: {now}",
        f"// time-series data (monthly_reasoning, creator_shares, hhi_*) is frozen historical data",
        f"const OPENROUTER_DATA = {json.dumps(data, ensure_ascii=False, separators=(',', ':'))};",
    ]
    with open(DATA_JS, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── OpenRouter API ────────────────────────────────────────────────────────────

def fetch_or_models():
    """Fetch all models from the OpenRouter public API."""
    headers = {}
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        data = http_get(OR_MODELS_URL, headers=headers)
        models = data.get("data", [])
        print(f"  ✓ OpenRouter API: {len(models)} models")
        return models
    except urllib.error.HTTPError as e:
        print(f"  ✗ OpenRouter API HTTP {e.code}: {e.reason}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  ✗ OpenRouter API error: {e}", file=sys.stderr)
        return []


def creator_from_id(model_id):
    return model_id.split("/")[0] if "/" in model_id else "unknown"


def blended_price_per_mtok(pricing):
    """Compute (prompt + completion) / 2 in USD per million tokens."""
    try:
        p = float(pricing.get("prompt", 0) or 0)
        c = float(pricing.get("completion", 0) or 0)
        if p == 0 and c == 0:
            return None
        return round((p + c) / 2 * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


# ── Merge logic ───────────────────────────────────────────────────────────────

def build_intel_map(existing):
    """Build model_id → intelligence_index from existing scatter data."""
    intel = {}
    for section in ("price_vs_intelligence", "perf_vs_usage"):
        for row in existing.get(section, []):
            mid = row.get("model_id")
            score = row.get("intelligence_index")
            if mid and score is not None and mid not in intel:
                intel[mid] = score
    return intel


def update_price_vs_intelligence(existing, or_models):
    """
    Rebuild price_vs_intelligence using fresh OpenRouter prices.
    Only includes models where we already have an intelligence score.
    """
    intel_map = build_intel_map(existing)

    # Preserve existing usage counts for matched models
    usage_map = {
        row["model_id"]: (row.get("total_count", 0), row.get("log_count", 0))
        for row in existing.get("price_vs_intelligence", [])
    }

    result = []
    skipped_no_intel = 0
    skipped_free = 0

    for m in or_models:
        mid = m.get("id", "")
        price = blended_price_per_mtok(m.get("pricing", {}))
        if price is None:
            skipped_free += 1
            continue

        intel = intel_map.get(mid)
        if intel is None:
            skipped_no_intel += 1
            continue

        total_count, log_count = usage_map.get(mid, (0, 0))

        result.append({
            "model_id": mid,
            "display_name": m.get("name", mid),
            "creator": creator_from_id(mid),
            "intelligence_index": intel,
            "price_blended": price,
            "total_count": total_count,
            "log_count": log_count,
        })

    # Sort by total_count descending (most-used first, consistent with existing)
    result.sort(key=lambda r: r["total_count"], reverse=True)

    print(f"  price_vs_intelligence: {len(result)} entries "
          f"({skipped_free} free/unknown price, {skipped_no_intel} missing intel score)")
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv

    print("Loading existing openrouter_data.js…")
    existing = load_existing_data()

    print("Fetching OpenRouter model list…")
    or_models = fetch_or_models()
    if not or_models:
        print("No models fetched — aborting to avoid data loss.", file=sys.stderr)
        sys.exit(1)

    # Update price scatter
    new_pvi = update_price_vs_intelligence(existing, or_models)
    existing["price_vs_intelligence"] = new_pvi

    # Update summary
    existing["summary"]["n_models"] = len(or_models)
    existing["summary"]["date_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if dry_run:
        print("[DRY RUN] No files written.")
        return

    write_data_js(existing)
    print(f"✓ Written: {DATA_JS}")


if __name__ == "__main__":
    main()
