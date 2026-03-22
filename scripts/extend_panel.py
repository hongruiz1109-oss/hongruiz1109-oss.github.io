#!/usr/bin/env python3
"""
extend_panel.py — Daily incremental update for OpenRouter panel data.

Fetches today's token usage data for all active models via the OpenRouter
RSC (React Server Component) API and appends it to panel_final.csv.gz.

This is a distilled version of ~/openrouter/research/scraper.py focused
solely on the portfolio's automated data pipeline.

Usage:
    python scripts/extend_panel.py            # full run (~5 min, all models)
    python scripts/extend_panel.py --test     # quick test (5 models only)
    python scripts/extend_panel.py --dry-run  # fetch but don't write

Required packages: requests, pandas
"""

import gzip
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import pandas as pd

ROOT = Path(__file__).parent.parent
PANEL_GZ = ROOT / "assets" / "data" / "panel_final.csv.gz"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


# ── Load / save compressed panel ─────────────────────────────────────────────

def load_panel() -> pd.DataFrame:
    print(f"Loading {PANEL_GZ} …")
    with gzip.open(PANEL_GZ, "rb") as f:
        df = pd.read_csv(f, low_memory=False)
    print(f"  {len(df):,} rows, {df['model_id'].nunique():,} models, "
          f"date range: {df['date'].min()} → {df['date'].max()}")
    return df


def save_panel(df: pd.DataFrame):
    print(f"Saving {PANEL_GZ} …")
    df.to_csv(PANEL_GZ, compression="gzip", index=False)
    print(f"  Saved {PANEL_GZ.stat().st_size / 1e6:.1f} MB compressed, "
          f"{len(df):,} total rows")


# ── OpenRouter API: model metadata ────────────────────────────────────────────

def fetch_models() -> list[dict]:
    """Fetch current model list and metadata from the public OpenRouter API."""
    resp = requests.get(
        "https://openrouter.ai/api/v1/models",
        headers=BASE_HEADERS, timeout=30
    )
    resp.raise_for_status()
    models = resp.json().get("data", [])
    print(f"  OpenRouter API: {len(models)} models")
    return models


def parse_meta(model: dict) -> dict:
    pricing = model.get("pricing", {})
    arch = model.get("architecture", {})
    mid = model.get("id", "")
    parts = mid.split("/")
    return {
        "model_id": mid,
        "creator": parts[0] if len(parts) >= 2 else "unknown",
        "display_name": model.get("name", ""),
        "price_prompt": float(pricing.get("prompt") or 0),
        "price_completion": float(pricing.get("completion") or 0),
        "context_length": model.get("context_length", 0),
        "supports_reasoning": "reasoning" in model.get("supported_parameters", []),
    }


# ── OpenRouter RSC API: daily token counts ────────────────────────────────────

RSC_PATTERN = re.compile(
    r'\{"date":"([^"]+)","model_permaslug":"([^"]+)","variant":"([^"]+)"'
    r',"total_completion_tokens":(\d+),"total_prompt_tokens":(\d+)'
    r',"total_native_tokens_reasoning":(\d+),"count":(\d+)'
    r'[^}]*"total_native_tokens_cached":(\d+)'
    r'[^}]*"total_tool_calls":(\d+)'
    r'[^}]*"requests_with_tool_call_errors":(\d+)'
)


def fetch_rsc_stats(model_id: str, session: requests.Session) -> list[dict]:
    """
    Fetch the RSC payload for a model page to get 90 days of daily usage.
    Uses the RSC:1 header to get raw server component JSON instead of HTML.
    """
    url = f"https://openrouter.ai/{model_id}"
    try:
        resp = session.get(url, headers={**BASE_HEADERS, "RSC": "1"}, timeout=30)
        if resp.status_code != 200:
            return []
        content = resp.text
    except Exception:
        return []

    records = []
    for m in RSC_PATTERN.finditer(content):
        records.append({
            "date":           m.group(1).split(" ")[0],
            "model_permaslug": m.group(2),
            "variant":        m.group(3),
            "total_prompt_tokens":             int(m.group(5)),
            "total_completion_tokens":         int(m.group(4)),
            "total_native_tokens_reasoning":   int(m.group(6)),
            "count":                           int(m.group(7)),
            "total_native_tokens_cached":      int(m.group(8)),
            "total_tool_calls":                int(m.group(9)),
            "requests_with_tool_call_errors":  int(m.group(10)),
        })
    return records


# ── Merge helpers ─────────────────────────────────────────────────────────────

def build_aa_map(panel: pd.DataFrame) -> pd.DataFrame:
    """
    For each model_id, extract the latest known Artificial Analysis scores.
    These scores are propagated forward to new date rows so the scatter charts
    remain accurate without re-running the AA matching pipeline.
    """
    aa_cols = [c for c in panel.columns if c.startswith("aa_")]
    if not aa_cols:
        return pd.DataFrame(columns=["model_id"])
    latest = (
        panel.dropna(subset=["aa_intelligence_index"])
        .sort_values("date")
        .groupby("model_id")[aa_cols]
        .last()
        .reset_index()
    )
    return latest


def build_new_rows(
    target_dates: set,
    models: list[dict],
    token_data: dict,
    aa_map: pd.DataFrame,
    existing_model_ids: set,
    backfill: bool = False,
) -> pd.DataFrame:
    """
    Assemble rows for all target_dates: one row per (date, model_id, variant).
    In normal mode (single date), falls back to most recent record if today's
    data isn't published yet. In backfill mode, only uses exact date matches.
    """
    meta_map = {m["model_id"]: m for m in models}
    rows = []

    for mid, records in token_data.items():
        matching = [r for r in records if r["date"] in target_dates]
        if not matching and not backfill:
            # Fall back to most recent record if today's isn't available yet
            matching = sorted(records, key=lambda r: r["date"], reverse=True)[:1]
        if not matching:
            continue

        meta = meta_map.get(mid, {})
        for rec in matching:
            row = {
                "date":           rec["date"],
                "model_id":       mid,
                "count":          rec["count"],
                "total_completion_tokens":        rec["total_completion_tokens"],
                "total_prompt_tokens":            rec["total_prompt_tokens"],
                "total_native_tokens_reasoning":  rec["total_native_tokens_reasoning"],
                "total_native_tokens_cached":     rec["total_native_tokens_cached"],
                "total_tool_calls":               rec["total_tool_calls"],
                "requests_with_tool_call_errors": rec["requests_with_tool_call_errors"],
                "source":         "realtime",
                "variant":        rec.get("variant", "standard"),
                "creator":        meta.get("creator", mid.split("/")[0]),
                "display_name":   meta.get("display_name", mid),
                "price_prompt":   meta.get("price_prompt", 0),
                "price_completion": meta.get("price_completion", 0),
                "context_length": meta.get("context_length", 0),
                "supports_reasoning": meta.get("supports_reasoning", False),
            }
            rows.append(row)

    if not rows:
        return pd.DataFrame()

    new_df = pd.DataFrame(rows)

    # Merge in AA scores from the existing panel
    if not aa_map.empty and "model_id" in aa_map.columns:
        new_df = new_df.merge(aa_map, on="model_id", how="left")

    return new_df


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    test_mode = "--test"     in sys.argv
    dry_run   = "--dry-run"  in sys.argv
    backfill  = "--backfill" in sys.argv
    today     = date.today().strftime("%Y-%m-%d")

    print(f"=== OpenRouter panel extension — {today} {'[BACKFILL]' if backfill else ''} ===")

    # Load existing panel
    panel = load_panel()
    existing_dates = set(panel["date"].unique())

    if not backfill and today in existing_dates:
        print(f"[INFO] Today ({today}) already in panel — skipping.")
        return

    # Build AA score lookup from existing data
    aa_map = build_aa_map(panel)
    print(f"  AA map: {len(aa_map)} models with intelligence scores")

    # Fetch current model list
    print("Fetching model list …")
    models = fetch_models()
    if not models:
        print("[ERROR] No models fetched.", file=sys.stderr)
        sys.exit(1)

    if test_mode:
        models = models[:5]
        print(f"  [TEST] Limited to {len(models)} models")

    # Fetch RSC token stats for each model
    print(f"Fetching token stats for {len(models)} models …")
    session = requests.Session()
    token_data = {}
    n_ok = 0

    for i, model in enumerate(models):
        mid = model.get("id", "")
        if not mid or "/" not in mid:
            continue
        records = fetch_rsc_stats(mid, session)
        if records:
            token_data[mid] = records
            n_ok += 1
        if (i + 1) % 50 == 0:
            print(f"  Progress: {i+1}/{len(models)}, {n_ok} with data")
        time.sleep(0.8)

    print(f"  Fetched data for {n_ok}/{len(models)} models")

    if not token_data:
        print("[WARN] No token data retrieved — aborting.", file=sys.stderr)
        sys.exit(0)

    # Determine target dates
    if backfill:
        all_rsc_dates = {r["date"] for records in token_data.values() for r in records}
        target_dates = all_rsc_dates - existing_dates
        print(f"  Backfill: {len(target_dates)} missing dates found "
              f"({min(target_dates)} → {max(target_dates)})")
        if not target_dates:
            print("[INFO] Nothing to backfill — panel is up to date.")
            return
    else:
        target_dates = {today}

    # Assemble new rows
    new_df = build_new_rows(
        target_dates, [parse_meta(m) for m in models],
        token_data, aa_map, set(panel["model_id"].unique()),
        backfill=backfill,
    )

    if new_df.empty:
        print("[WARN] No new rows to add.")
        sys.exit(0)

    print(f"  New rows: {len(new_df)} across {new_df['date'].nunique()} dates")

    if dry_run:
        print("[DRY RUN] Would append rows. Skipping write.")
        return

    # Append and save
    combined = pd.concat([panel, new_df], ignore_index=True, sort=False)
    combined = combined.drop_duplicates(subset=["date", "model_id", "variant"], keep="last")
    combined = combined.sort_values(["date", "model_id"])
    save_panel(combined)
    print("Done.")


if __name__ == "__main__":
    main()
