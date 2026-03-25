"""
generate_viz.py
Reads assets/data/panel_final.csv.gz and produces assets/js/openrouter_data.js
with aggregated data structures for Plotly visualisations.

Usage:
    python scripts/generate_viz.py
"""

import gzip
import json
import math
from pathlib import Path

import pandas as pd

# ── paths (relative to repo root) ────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
INPUT_CSV  = ROOT / "assets" / "data" / "panel_final.csv.gz"
OUTPUT_JS  = ROOT / "assets" / "js" / "openrouter_data.js"

# ── load ─────────────────────────────────────────────────────────────────────
print("Loading data …")
with gzip.open(INPUT_CSV, "rb") as f:
    df = pd.read_csv(f, low_memory=False)

# Ensure supports_reasoning is bool (fill NaN → False)
df["supports_reasoning"] = df["supports_reasoning"].fillna(False).astype(bool)

# Month column
df["month"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m")

# Week column — ISO week start (Monday), formatted as YYYY-MM-DD
df["week"] = (
    pd.to_datetime(df["date"])
    .dt.to_period("W-MON")
    .apply(lambda p: p.start_time)
    .dt.strftime("%Y-%m-%d")
)

print(f"  rows: {len(df):,}  models: {df['model_id'].nunique():,}  months: {df['month'].nunique()}  weeks: {df['week'].nunique()}")

# ── helper ───────────────────────────────────────────────────────────────────
def round_floats(obj, ndigits=4):
    """Recursively round all floats in a nested structure."""
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: round_floats(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [round_floats(v, ndigits) for v in obj]
    return obj


# ── 1. monthly_reasoning ─────────────────────────────────────────────────────
print("Building monthly_reasoning …")
monthly = (
    df.groupby("month")
    .agg(
        total_count=("count", "sum"),
        reasoning_count=("count", lambda s: df.loc[s.index, "count"][df.loc[s.index, "supports_reasoning"]].sum()),
    )
    .reset_index()
    .sort_values("month")
)
monthly["reasoning_pct"] = monthly["reasoning_count"] / monthly["total_count"] * 100

monthly_reasoning = [
    {
        "month": row.month,
        "total_count": int(row.total_count),
        "reasoning_count": int(row.reasoning_count),
        "reasoning_pct": round(float(row.reasoning_pct), 4),
    }
    for row in monthly.itertuples()
]

# ── 2. weekly_creator ────────────────────────────────────────────────────────
print("Building weekly_creator …")
# Top-12 creators by overall count
top8_creators = (
    df.groupby("creator")["count"].sum()
    .nlargest(12)
    .index.tolist()
)
print(f"  top-12 creators: {top8_creators}")

# Weekly totals
week_total = (
    df.groupby("week")["count"].sum().to_dict()
)

# Weekly count per creator
creator_weekly = (
    df.groupby(["week", "creator"])["count"]
    .sum()
    .reset_index()
    .rename(columns={"count": "cnt"})
)
creator_weekly["share"] = creator_weekly.apply(
    lambda r: r.cnt / week_total[r.week] * 100, axis=1
)

sorted_weeks = sorted(week_total.keys())

weekly_creator: dict = {}
for creator in top8_creators:
    sub = creator_weekly[creator_weekly["creator"] == creator].set_index("week")["share"]
    weekly_creator[creator] = [
        {"week": w, "share": round(float(sub.get(w, 0.0)), 4)}
        for w in sorted_weeks
    ]

# "Other" = 100 - sum(top-8 shares) per week
top8_share_by_week = (
    creator_weekly[creator_weekly["creator"].isin(top8_creators)]
    .groupby("week")["share"]
    .sum()
)
weekly_creator["Other"] = [
    {
        "week": w,
        "share": round(float(100 - top8_share_by_week.get(w, 0)), 4),
    }
    for w in sorted_weeks
]

# ── 2b. monthly_creator ──────────────────────────────────────────────────────
print("Building monthly_creator …")
month_total = monthly.set_index("month")["total_count"].to_dict()
sorted_months = sorted(month_total.keys())

creator_monthly = (
    df.groupby(["month", "creator"])["count"]
    .sum().reset_index().rename(columns={"count": "cnt"})
)
creator_monthly["share"] = creator_monthly.apply(
    lambda r: r.cnt / month_total[r.month] * 100, axis=1
)

monthly_creator: dict = {}
for creator in top8_creators:
    sub = creator_monthly[creator_monthly["creator"] == creator].set_index("month")["share"]
    monthly_creator[creator] = [
        {"month": m, "share": round(float(sub.get(m, 0.0)), 4)}
        for m in sorted_months
    ]
top8_share_by_month = (
    creator_monthly[creator_monthly["creator"].isin(top8_creators)]
    .groupby("month")["share"].sum()
)
monthly_creator["Other"] = [
    {"month": m, "share": round(float(100 - top8_share_by_month.get(m, 0)), 4)}
    for m in sorted_months
]

# ── 3. hhi (monthly / weekly / daily) ────────────────────────────────────────
print("Building hhi_monthly …")
creator_monthly["share_frac"] = creator_monthly["share"] / 100
hhi_monthly = (
    creator_monthly.groupby("month")
    .apply(lambda g: (g["share_frac"] ** 2).sum() * 10000)
    .reset_index(name="hhi").sort_values("month")
)
hhi_monthly_list = [
    {"month": row.month, "hhi": round(float(row.hhi), 4)}
    for row in hhi_monthly.itertuples()
]

print("Building hhi_weekly …")
week_total_s = df.groupby("week")["count"].sum()
creator_weekly_hhi = (
    df.groupby(["week", "creator"])["count"]
    .sum().reset_index().rename(columns={"count": "cnt"})
)
creator_weekly_hhi["share_frac"] = creator_weekly_hhi.apply(
    lambda r: r.cnt / week_total_s[r.week], axis=1
)
hhi_weekly = (
    creator_weekly_hhi.groupby("week")
    .apply(lambda g: (g["share_frac"] ** 2).sum() * 10000)
    .reset_index(name="hhi").sort_values("week")
)
hhi_weekly_list = [
    {"week": row.week, "hhi": round(float(row.hhi), 4)}
    for row in hhi_weekly.itertuples()
]

print("Building hhi_daily …")
day_total = df.groupby("date")["count"].sum()
creator_daily_hhi = (
    df.groupby(["date", "creator"])["count"]
    .sum().reset_index().rename(columns={"count": "cnt"})
)
creator_daily_hhi["share_frac"] = creator_daily_hhi.apply(
    lambda r: r.cnt / day_total[r.date], axis=1
)
hhi_daily = (
    creator_daily_hhi.groupby("date")
    .apply(lambda g: (g["share_frac"] ** 2).sum() * 10000)
    .reset_index(name="hhi").sort_values("date")
)
hhi_daily_list = [
    {"date": row.date, "hhi": round(float(row.hhi), 4)}
    for row in hhi_daily.itertuples()
]

# ── 4. perf_vs_usage ─────────────────────────────────────────────────────────
print("Building perf_vs_usage …")
perf_agg = (
    df.groupby("model_id")
    .agg(
        total_count=("count", "sum"),
        intelligence_index=("aa_intelligence_index", "first"),
        creator=("creator", "first"),
        display_name=("display_name", "first"),
    )
    .reset_index()
)
# first non-null for intelligence_index
intel_first = (
    df.dropna(subset=["aa_intelligence_index"])
    .groupby("model_id")["aa_intelligence_index"]
    .first()
)
perf_agg["intelligence_index"] = perf_agg["model_id"].map(intel_first)

perf_filtered = perf_agg[
    perf_agg["intelligence_index"].notna() & (perf_agg["total_count"] > 10000)
].copy()
perf_filtered["log_count"] = perf_filtered["total_count"].apply(lambda x: round(math.log10(x), 4))
perf_filtered = perf_filtered.sort_values("total_count", ascending=False)

perf_vs_usage = [
    {
        "model_id": row.model_id,
        "display_name": row.display_name,
        "creator": row.creator,
        "intelligence_index": round(float(row.intelligence_index), 4),
        "total_count": int(row.total_count),
        "log_count": float(row.log_count),
    }
    for row in perf_filtered.itertuples()
]
print(f"  perf_vs_usage models: {len(perf_vs_usage)}")

# ── 5. top_models ─────────────────────────────────────────────────────────────
print("Building top_models …")
model_totals = df.groupby("model_id")["count"].sum().reset_index().rename(columns={"count": "total_count"})
model_meta = (
    df.dropna(subset=["display_name"])
    .groupby("model_id")
    .agg(display_name=("display_name", "first"), creator=("creator", "first"))
    .reset_index()
)
top_models_df = (
    model_totals.merge(model_meta, on="model_id", how="left")
    .nlargest(20, "total_count")
)
top_models = [
    {
        "model_id": row.model_id,
        "display_name": row.display_name,
        "creator": row.creator,
        "total_count": int(row.total_count),
    }
    for row in top_models_df.itertuples()
]

# ── 6. summary_stats ─────────────────────────────────────────────────────────
print("Building summary_stats …")
first_nonzero_month = next(
    (r["month"] for r in monthly_reasoning if r["reasoning_pct"] > 0), None
)
summary_stats = {
    "total_rows": int(len(df)),
    "n_models": int(df["model_id"].nunique()),
    "n_creators": int(df["creator"].nunique()),
    "date_min": str(df["date"].min()),
    "date_max": str(df["date"].max()),
    "total_requests": int(df["count"].sum()),
    "total_completion_tokens": int(df["total_completion_tokens"].sum()),
    "reasoning_share_latest": float(monthly_reasoning[-1]["reasoning_pct"]),
    "reasoning_share_first_nonzero": first_nonzero_month,
    "hhi_min": round(float(min(r["hhi"] for r in hhi_monthly_list)), 4),
    "hhi_max": round(float(max(r["hhi"] for r in hhi_monthly_list)), 4),
}

# ── 7. price_vs_intelligence ─────────────────────────────────────────────────
print("Building price_vs_intelligence …")
price_intel_agg = (
    df.groupby("model_id")
    .agg(
        total_count=("count", "sum"),
        display_name=("display_name", "first"),
        creator=("creator", "first"),
    )
    .reset_index()
)

# First non-null values for intelligence and price
intel_nn = (
    df.dropna(subset=["aa_intelligence_index"])
    .groupby("model_id")["aa_intelligence_index"]
    .first()
)
price_nn = (
    df.dropna(subset=["aa_price_1m_blended"])
    .groupby("model_id")["aa_price_1m_blended"]
    .first()
)
price_intel_agg["intelligence_index"] = price_intel_agg["model_id"].map(intel_nn)
price_intel_agg["price_blended"] = price_intel_agg["model_id"].map(price_nn)

price_intel_filtered = price_intel_agg[
    price_intel_agg["intelligence_index"].notna()
    & price_intel_agg["price_blended"].notna()
    & (price_intel_agg["total_count"] > 5000)
].copy()
price_intel_filtered["log_count"] = price_intel_filtered["total_count"].apply(
    lambda x: round(math.log10(x), 4)
)
price_intel_filtered = price_intel_filtered.sort_values("intelligence_index", ascending=False)

price_vs_intelligence = [
    {
        "model_id": row.model_id,
        "display_name": row.display_name,
        "creator": row.creator,
        "intelligence_index": round(float(row.intelligence_index), 4),
        "price_blended": round(float(row.price_blended), 4),
        "total_count": int(row.total_count),
        "log_count": float(row.log_count),
    }
    for row in price_intel_filtered.itertuples()
]
print(f"  price_vs_intelligence models: {len(price_vs_intelligence)}")

# ── assemble output ───────────────────────────────────────────────────────────
output_data = {
    "summary": summary_stats,
    "monthly_reasoning": monthly_reasoning,
    "weekly_creator": weekly_creator,
    "monthly_creator": monthly_creator,
    "hhi_monthly": hhi_monthly_list,
    "hhi_weekly": hhi_weekly_list,
    "hhi_daily": hhi_daily_list,
    "perf_vs_usage": perf_vs_usage,
    "top_models": top_models,
    "price_vs_intelligence": price_vs_intelligence,
}

json_str = json.dumps(output_data, ensure_ascii=False, indent=2)

js_content = (
    "// Auto-generated by generate_openrouter_viz.py\n"
    "const OPENROUTER_DATA = " + json_str + ";\n"
)

OUTPUT_JS.write_text(js_content, encoding="utf-8")

size_kb = OUTPUT_JS.stat().st_size / 1024
print(f"\nDone! Written to {OUTPUT_JS}")
print(f"File size: {size_kb:.1f} KB")
print("\nSummary stats:")
for k, v in summary_stats.items():
    print(f"  {k}: {v}")
