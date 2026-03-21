# 🎓 Hongrui ZHU — Academic Portfolio

> PhD Student in Industrial Economics · UCASS
> 🌐 [hongruiz1109-oss.github.io](https://hongruiz1109-oss.github.io)

---

## 🔬 Research

### 📄 Simulating Tacit Collusion Under Algorithmic Pricing
*Algorithmic Collusion in Q-Learning Pricing Agents*

How do supra-competitive prices arise in markets dominated by reinforcement learning algorithms — without any explicit human coordination? We build a MARL-based oligopoly sandbox and show that under specific exploration rates and discount factors, pricing algorithms inevitably converge toward tacit collusion.

> 💡 Provides antitrust regulators (FTC, SAMR) with a quantitative toolkit for detecting and auditing algorithmic collusion in digital markets.

---

### 📄 Consumer Demand Estimation with Large Language Model Agents
*Estimating Consumer Demand via LLM Agents*

Traditional discrete choice models fail to capture consumer preferences when faced with unstructured textual product information. We construct a population of heterogeneous consumer agents powered by GPT-4, feeding product text descriptions to reconstruct demand curves in a zero-shot setting.

> 💡 Provides e-commerce platforms and dynamic pricing systems with more realistic zero-shot market testing environments, reducing reliance on costly field experiments.

---

🏷️ **Research Interests:** Algorithmic Pricing · LLM as Economic Agents · Platform Economics

---

## 📊 Data Visualization

### 📡 LLM Market Dynamics on OpenRouter

A longitudinal panel dataset tracking daily API usage for **400 large language models** on OpenRouter, spanning **July 2024 – February 2026**, merged with Artificial Analysis benchmark scores.

| | |
|---|---|
| 📦 Data sources | OpenRouter Wayback Machine Archives + Artificial Analysis |
| 🤖 Models tracked | 400 models across 57 creators |
| 📅 Period | 19 months (Jul 2024 – Feb 2026) |
| 🔢 Total requests | 38 billion API calls |

**Interactive charts:**

- 📈 **Reasoning model adoption** — Monthly bar chart of reasoning model share (0% → 60% in 13 months)
- 🏢 **Creator market share** — Stacked bar chart with Weekly / Monthly granularity toggle
- 📉 **Market concentration (HHI)** — Line chart with Daily / Weekly / Monthly granularity toggle
- 🧠 **Intelligence vs. Usage** — 4-quadrant scatter (median crosshair); Popular & Smart / Niche
- 💰 **Price vs. Intelligence** — 4-quadrant scatter (log-scale price); Premium / Best Value / Budget

```
openrouter.html                    # interactive data page
assets/js/openrouter_data.js       # auto-generated data (169 KB)
openrouter/research/
├── generate_openrouter_viz.py     # aggregates panel_final.csv → openrouter_data.js
└── data/processed/panel_final.csv # cleaned daily panel
```

---

### 🔭 半导体全球价值链 · Semiconductor Global Value Chain

Interactive visualization of quarterly bilateral semiconductor trade flows across **40+ countries**, **2020–2024**.

| | |
|---|---|
| 📦 Data source | UN Comtrade |
| 🗂️ Coverage | HS 8482 – 8542 |
| 📅 Period | 2020 Q1 – 2024 Q4 |
| 🌍 Countries | 40+ |

**Three views:**

- 📤 **出口视角 · Export** — Plotly chart of export flows by commodity category, with country filter and trade-shock event overlay
- 📥 **进口视角 · Import** — Import flows with equivalent interactive controls
- 🕸️ **双边贸易 · Network** — D3.js force-directed graph; node size = total bilateral trade, edge width = bilateral flow; animated year slider (2020 → 2024)

```
semiconductor/
├── semiconductor_gvc.html       # landing page (tab container)
├── semiconductor_exports.html   # Plotly export chart
├── semiconductor_imports.html   # Plotly import chart
└── network_bilateral.html       # D3 v7 force network (self-contained, D3 inlined)
```

**Key data findings:**
- 📈 Export controls created a 6-month shock — then semiconductor trade rebounded to record highs
- 🧠 Memory chips lost share after US controls; processors & controllers gained it
- 🇨🇳 China and Hong Kong SAR quietly became the world's top semiconductor exporters by 2025
- 💰 Despite export controls, China's semiconductor imports hit a record $234B in 2024

---

## 🗂️ Repo Structure

```
/
├── 📄 index.html               # main portfolio page
├── 📄 openrouter.html          # LLM Market Dynamics interactive page
├── 🖼️  assets/
│   ├── js/openrouter_data.js   # auto-generated chart data
│   └── img/                    # thumbnail images
└── 📁 semiconductor/           # GVC visualization pages
```

---

## 🛠️ Tech Stack

| Layer | Tools |
|---|---|
| Frontend | HTML · CSS · Vanilla JS |
| Charts | Plotly.js · D3.js v7 |
| Data processing | Python · pandas |
| Hosting | GitHub Pages |
