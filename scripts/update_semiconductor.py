#!/usr/bin/env python3
"""
Fetch latest semiconductor trade data from UN Comtrade Plus API
and update semiconductor/network_bilateral.html.

What this updates:
  network_bilateral.html — the embedded `const DATA = {...}` object:
    • Adds/refreshes data for the target year
    • Updates the bilateral trade edges and node values
    • Saves raw JSON to semiconductor/data/network_{year}.json

What stays unchanged:
  semiconductor_exports.html / semiconductor_imports.html
    These contain embedded Plotly charts with 128-country quarterly data;
    regenerating them requires the original Python/Plotly pipeline.

Usage:
    python scripts/update_semiconductor.py
    python scripts/update_semiconductor.py --year 2024 --dry-run

Env:
    COMTRADE_API_KEY   required (free tier: register at comtradeplus.un.org)

Free-tier limits: ~250-500 requests/day; this script uses ≤18 requests/run
(9 HS codes × 2 flows), well within limits.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NETWORK_HTML = os.path.join(ROOT, "semiconductor", "network_bilateral.html")
DATA_DIR = os.path.join(ROOT, "semiconductor", "data")

# ── Countries in the network chart ───────────────────────────────────────────
# 22 major semiconductor trade hubs; ISO-3 → UN Comtrade numeric reporter code

COUNTRIES = {
    "CHN": 156,   # China
    "HKG": 344,   # Hong Kong SAR
    "SGP": 702,   # Singapore
    "KOR": 410,   # Korea, Republic of
    "USA": 842,   # United States
    "MYS": 458,   # Malaysia
    "JPN": 392,   # Japan
    "VNM": 704,   # Viet Nam
    "PHL": 608,   # Philippines
    "NLD": 528,   # Netherlands
    "DEU": 276,   # Germany
    "THA": 764,   # Thailand
    "MEX": 484,   # Mexico
    "FRA": 250,   # France
    "IRL": 372,   # Ireland
    "BEL":  56,   # Belgium
    "ISR": 376,   # Israel
    "GBR": 826,   # United Kingdom
    "IDN": 360,   # Indonesia
    "ITA": 380,   # Italy
    "CZE": 203,   # Czechia
    "CAN": 124,   # Canada
}

# ── Semiconductor HS commodity categories ────────────────────────────────────
HS_CATEGORIES = {
    "848610": "晶圆制造设备 Wafer Mfg. Equipment",
    "848620": "半导体测试设备 Semiconductor Testing Equipment",
    "848640": "其他半导体制造设备 Other Semiconductor Equipment",
    "848690": "半导体设备零部件 Parts for Semiconductor Equipment",
    "854149": "半导体器件 Semiconductor Devices",
    "854231": "集成电路 Integrated Circuits",
    "854232": "处理器和控制器 Processors and Controllers",
    "854233": "存储器 Memory Circuits",
    "854239": "其他集成电路 Other Integrated Circuits",
}

COMTRADE_BASE = "https://comtradeplus.un.org/TradeFlow/Annual"


# ── API helpers ───────────────────────────────────────────────────────────────

CODE_TO_ISO = {v: k for k, v in COUNTRIES.items()}


def get_api_key():
    key = os.environ.get("COMTRADE_API_KEY", "")
    if not key:
        print("[WARN] COMTRADE_API_KEY not set — API calls may be rejected.", file=sys.stderr)
    return key


def fetch_comtrade(cmd_code, year, flow, api_key, max_records=2500):
    """
    Fetch annual trade flows for a specific HS code from all reporters to all partners.
    flow: "X" (export) or "M" (import)
    Returns parsed JSON or None on error.
    """
    url = (
        f"{COMTRADE_BASE}/Reporters/all/Types/C/Commodities/{cmd_code}"
        f"/TradeFlows/{flow}/Partners/all/years/{year}"
        f"?maxRecords={max_records}"
    )
    if api_key:
        url += f"&subscription-key={api_key}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "portfolio-data-updater/1.0 (github.com/hongruiz1109-oss)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"    ✗ HTTP {e.code} for HS {cmd_code} flow={flow} year={year}: {e.reason}",
              file=sys.stderr)
        return None
    except Exception as e:
        print(f"    ✗ Error fetching HS {cmd_code}: {e}", file=sys.stderr)
        return None


def parse_bilateral_matrix(response):
    """
    Parse Comtrade response into a bilateral trade matrix.
    Returns dict: {reporter_iso3: {partner_iso3: value_usd_billion}}
    Only rows where both reporter and partner are in COUNTRIES are kept.
    """
    if not response or "data" not in response:
        return {}

    matrix = {}
    for record in response["data"]:
        # Comtrade Plus API field names (v2)
        r_code = record.get("reporterCode") or record.get("ReporterCode")
        p_code = record.get("partnerCode") or record.get("PartnerCode")
        value  = record.get("primaryValue") or record.get("PrimaryValue") or 0

        reporter = CODE_TO_ISO.get(int(r_code)) if r_code is not None else None
        partner  = CODE_TO_ISO.get(int(p_code)) if p_code is not None else None

        if reporter is None or partner is None or reporter == partner:
            continue

        matrix.setdefault(reporter, {})[partner] = (
            matrix.get(reporter, {}).get(partner, 0) + float(value) / 1e9
        )
    return matrix


def build_year_dataset(cmd_code, year, api_key):
    """
    Fetch exports + imports for a commodity/year pair and compute:
      nodeValues: total bilateral trade per country (exports + imports / 2)
      edges:      undirected bilateral links with combined value
    Returns None if no data is available.
    """
    print(f"    HS {cmd_code} exports… ", end="", flush=True)
    resp_x = fetch_comtrade(cmd_code, year, "X", api_key)
    time.sleep(0.5)

    print(f"imports… ", end="", flush=True)
    resp_m = fetch_comtrade(cmd_code, year, "M", api_key)
    time.sleep(0.5)

    matrix_x = parse_bilateral_matrix(resp_x) if resp_x else {}
    matrix_m = parse_bilateral_matrix(resp_m) if resp_m else {}

    if not matrix_x and not matrix_m:
        print("no data")
        return None

    # Build undirected edges: value = average of A→B export and B→A export
    all_countries = set(COUNTRIES)
    seen_pairs = set()
    edges = []
    node_trade = {c: 0.0 for c in all_countries}

    for src in all_countries:
        for tgt in all_countries:
            if src >= tgt:
                continue
            pair = (src, tgt)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            # Export direction A→B (from export matrix)
            ab = matrix_x.get(src, {}).get(tgt, 0)
            # Export direction B→A
            ba = matrix_x.get(tgt, {}).get(src, 0)
            # Import validation (B reporting import from A ≈ A's export to B)
            ab_import = matrix_m.get(tgt, {}).get(src, 0)
            ba_import = matrix_m.get(src, {}).get(tgt, 0)

            # Best estimate: prefer export data, fall back to import mirror
            flow_ab = ab if ab > 0 else ab_import
            flow_ba = ba if ba > 0 else ba_import
            bilateral = (flow_ab + flow_ba) / 2

            if bilateral >= 0.001:  # Filter sub-million flows
                edges.append({"source": src, "target": tgt, "value": round(bilateral, 4)})
                node_trade[src] += bilateral
                node_trade[tgt] += bilateral

    node_values = {c: round(v, 3) for c, v in node_trade.items() if v > 0}
    print(f"{len(edges)} bilateral links, {len(node_values)} active countries")

    return {"nodeValues": node_values, "edges": edges}


# ── HTML DATA object manipulation ─────────────────────────────────────────────

def load_network_html():
    """
    Extract the `const DATA = {...}` JavaScript object from network_bilateral.html.
    Uses json.JSONDecoder.raw_decode so it handles arbitrary nesting correctly.
    Returns (data_dict, html_content, json_start_pos, json_end_pos).
    """
    with open(NETWORK_HTML, "r", encoding="utf-8") as f:
        content = f.read()

    marker = "const DATA = "
    idx = content.find(marker)
    if idx == -1:
        raise ValueError("'const DATA = ' not found in network_bilateral.html")

    json_start = idx + len(marker)
    decoder = json.JSONDecoder()
    obj, offset = decoder.raw_decode(content, json_start)
    json_end = json_start + offset

    return obj, content, json_start, json_end


def save_network_html(content, new_data, json_start, json_end):
    """Replace the DATA JSON in-place and write the file."""
    new_json = json.dumps(new_data, ensure_ascii=False, separators=(",", ":"))
    updated = content[:json_start] + new_json + content[json_end:]
    with open(NETWORK_HTML, "w", encoding="utf-8") as f:
        f.write(updated)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Update semiconductor network data")
    parser.add_argument("--year", type=int,
                        default=datetime.now(timezone.utc).year - 1,
                        help="Year to fetch (default: last calendar year)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and process but do not write files")
    args = parser.parse_args()

    api_key = get_api_key()
    year = args.year
    year_str = str(year)

    print(f"Fetching semiconductor trade data for {year}…")
    print(f"  Comtrade API key: {'set' if api_key else 'NOT SET — may fail'}")

    # ── Fetch per-category data ───────────────────────────────────────────────
    new_datasets = {}   # cmd_code → year_dataset
    for cmd_code, label in HS_CATEGORIES.items():
        print(f"\n  [{cmd_code}] {label}")
        ds = build_year_dataset(cmd_code, year, api_key)
        if ds:
            new_datasets[cmd_code] = ds

    if not new_datasets:
        print("\n[WARN] No data fetched. Comtrade may not have data for this year yet.")
        print("       Try: --year", year - 1)
        sys.exit(0)

    # ── Build aggregate "all categories" dataset ──────────────────────────────
    agg_node = {}
    agg_edges_map = {}
    for ds in new_datasets.values():
        for c, v in ds["nodeValues"].items():
            agg_node[c] = agg_node.get(c, 0) + v
        for edge in ds["edges"]:
            key = (edge["source"], edge["target"])
            agg_edges_map[key] = agg_edges_map.get(key, 0) + edge["value"]

    all_ds = {
        "nodeValues": {c: round(v, 3) for c, v in agg_node.items()},
        "edges": [
            {"source": s, "target": t, "value": round(v, 4)}
            for (s, t), v in agg_edges_map.items()
            if v >= 0.001
        ],
    }
    print(f"\n  [all] Aggregate: {len(all_ds['edges'])} links")

    # ── Save raw data ─────────────────────────────────────────────────────────
    os.makedirs(DATA_DIR, exist_ok=True)
    raw_path = os.path.join(DATA_DIR, f"network_{year}.json")
    raw_payload = {"year": year, "fetched_at": datetime.now(timezone.utc).isoformat(),
                   "categories": new_datasets, "all": all_ds}
    if not args.dry_run:
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(raw_payload, f, ensure_ascii=False, indent=2)
        print(f"\n✓ Raw data saved: {raw_path}")

    # ── Merge into network_bilateral.html ─────────────────────────────────────
    print("\nLoading network_bilateral.html…")
    existing_data, html_content, json_start, json_end = load_network_html()

    # Update the "0" aggregate category
    existing_data["datasets"].setdefault("0", {
        "label": "全品类 All categories",
        "years": {},
    })["years"][year_str] = all_ds

    # Update each specific HS code category
    for cmd_code, ds in new_datasets.items():
        existing_data["datasets"].setdefault(cmd_code, {
            "label": HS_CATEGORIES[cmd_code],
            "years": {},
        })["years"][year_str] = ds

    # Add year to the years list
    years_list = existing_data.get("years", [])
    if year not in years_list:
        existing_data["years"] = sorted(set(years_list) | {year})

    if args.dry_run:
        print("[DRY RUN] Would update network_bilateral.html. Skipping write.")
        return

    save_network_html(html_content, existing_data, json_start, json_end)
    print(f"✓ Updated: {NETWORK_HTML}")


if __name__ == "__main__":
    main()
