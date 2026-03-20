# Hongrui ZHU — Academic Portfolio

Personal portfolio site for **Hongrui ZHU**, PhD student in Industrial Economics.
Live at: [hongruiz1109-oss.github.io](https://hongruiz1109-oss.github.io)

---

## Research

**Simulating Tacit Collusion Under Algorithmic Pricing**
*Algorithmic Collusion in Q-Learning Pricing Agents*
How do supra-competitive prices arise in markets dominated by reinforcement learning algorithms — without any explicit human coordination? We build a MARL-based oligopoly sandbox and show that under specific exploration rates and discount factors, pricing algorithms inevitably converge toward tacit collusion.

**Consumer Demand Estimation with Large Language Model Agents**
*Estimating Consumer Demand via LLM Agents*
Traditional discrete choice models fail to capture consumer preferences when faced with unstructured textual product information. We construct a population of heterogeneous consumer agents powered by GPT-4, feeding product text descriptions to reconstruct demand curves in a zero-shot setting.

Research interests: Algorithmic Pricing · LLM as Economic Agents · Platform Economics

---

## Data Visualization

### 半导体全球价值链 · Semiconductor Global Value Chain

Interactive visualization of quarterly bilateral semiconductor trade flows across 40+ countries, 2020–2024. Data source: UN Comtrade (HS 8482–8542).

Three views:
- **出口视角 · Export** — export flows by commodity category, with country and shock-event filters
- **进口视角 · Import** — import flows with equivalent controls
- **双边贸易 · Network** — D3.js force-directed network; node size = total bilateral trade, edge width = bilateral flow; year slider animates 2020–2024

```
semiconductor/
├── semiconductor_gvc.html       # landing page (tab container)
├── semiconductor_exports.html   # Plotly export chart
├── semiconductor_imports.html   # Plotly import chart
└── network_bilateral.html       # D3 v7 force network (self-contained, D3 inlined)
```

---

## Repo Structure

```
/
├── index.html            # main portfolio page
├── assets/               # thumbnail images (fig1–fig4)
└── semiconductor/        # GVC visualization pages
```

---

*Built with HTML/CSS/JS · Plotly.js · D3.js v7 · Data processed in Python (pandas)*
