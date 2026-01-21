# ⚡ Unit Commitment Optimization Tool

This project provides an internal **Django‑based web application** for solving a **Unit Commitment & Economic Dispatch** problem using **Pyomo** and visualizing results with **interactive Plotly graphs**.

The tool allows users to upload market price data, run an optimization, inspect financial metrics, visualize results over time, and extract detailed insights for selected periods.

---

## 🚀 New & Extended Features

The following features were added on top of the original optimization workflow:

### 📊 Interactive Time‑Series Visualization

* Results are visualized using **Plotly** instead of static images.
* The X‑axis uses real **DateTime values** (date → day → hour when zooming).
* Combined hover tooltip shows:

  * Market price (EUR/MWh)
  * Power output (MW)
  * Commitment level (MW)
* Zooming and panning are fully supported.

### 📅 Extracted Period Analysis

Users can select a **custom date range** (e.g. specific days or weeks) from the already‑computed yearly results:

* No re‑optimization is performed
* Calculations are reused from the saved load curve
* A **new interactive plot** is generated **only for the selected period**

### 💰 Financial Metrics for Extracted Period

For the selected date range, the app computes:

* Total revenue and profit
* Costs (coal + CO₂ + startups)
* Profit / cost per MWh
* Commitment hours and relative uptime

These metrics are independent from the full‑year results and reflect **only the extracted period**.

### 📦 ZIP Export (Optional)

For an extracted period, users can download a ZIP archive containing:

* `*_extracted_load_curve.csv` – filtered load curve
* `*_extracted_financials.csv` – financial metrics
* `*_extracted_plot.png` – static image of the extracted plot

All files are generated **in‑memory** and are not stored on the server.

---

## 🧪 Typical Workflow

1. Upload Excel file with hourly market prices
2. Enter plant parameters
3. Run optimization (Pyomo + CBC)
4. View interactive yearly results
5. Select a date range to extract
6. Inspect extracted metrics and plot
7. (Optional) Download ZIP with extracted results

---

## 📂 Outputs

### Stored on server (full run)

* Load curve CSV
* Results CSV (metrics + parameters)
* PNG of full‑year plot

### Generated on demand (extracted period)

* Interactive Plotly graph (HTML)
* PNG (in memory)
* ZIP archive (optional)

---

## 🛠 Tech Stack

* **Backend**: Django, Pyomo, Pandas, NumPy
* **Solver**: CBC
* **Visualization**: Plotly
* **Frontend**: Django templates

---

This README section was updated to reflect the **new interactive plotting**, **extracted period analysis**, and **ZIP export functionality** added to the project.
