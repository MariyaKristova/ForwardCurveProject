import os
import base64
from datetime import datetime
import pyomo.environ as pyo
import numpy as np
import pandas as pd
import plotly.graph_objs as go
import csv
from django.conf import settings
from django.http import Http404


# UTILITIES
def read_excel_file(excel_filename):
    excel_path = os.path.join(settings.DATA_INPUT_DIR, excel_filename)
    df = pd.read_excel(excel_path)

    df['DateTime'] = pd.to_datetime(df['DateTime'])
    bgn_euro_rate = 1.95583
    df['Price'] = df['Price'] * bgn_euro_rate
    market_price = df['Price'].to_numpy()

    return df, market_price


def calculate_degradation(n_hours, year):
    # plant started on 09.05.2011
    START_YEAR = 2011
    START_MONTH = 5
    total_months_elapsed = (year - START_YEAR) * 12 + (START_MONTH - 1)

    months = range(total_months_elapsed, total_months_elapsed+12)

    if n_hours == 365 * 24:
        month_lengths = [31,28,31,30,31,30,31,31,30,31,30,31]
    else:
        month_lengths = [31,29,31,30,31,30,31,31,30,31,30,31]

    degradation = np.zeros(n_hours)
    start_idx = 0
    for idx, month_length in enumerate(month_lengths):
        stop_idx = start_idx + month_length * 24
        if stop_idx > n_hours:
            stop_idx = n_hours
        degradation[start_idx:stop_idx] = 1.071 + 0.0002 * months[idx]
        start_idx = stop_idx

    return degradation


def read_load_curve(run_id):
    files = os.listdir(settings.DATA_OUTPUT_DIR)
    load_curve_file = next((f for f in files if f.startswith(run_id) and f.endswith("_load_curve.csv")), None)

    if not load_curve_file:
        raise Http404("Load curve CSV not found")

    return load_curve_file


def read_results_csv(run_id):
    files = os.listdir(settings.DATA_OUTPUT_DIR)
    results_csv_file = next((f for f in files if f.startswith(run_id) and f.endswith("_results.csv")), None)

    if not results_csv_file:
        raise Http404("Results CSV not found")

    return results_csv_file


def read_params_from_results_csv(run_id):
    results_file = read_results_csv(run_id)
    params = {}

    with open(os.path.join(settings.DATA_OUTPUT_DIR, results_file), newline="") as f:
        reader = csv.reader(f)
        section = "metrics"
        for row in reader:
            if row and row[0] == "---parameters---":
                section = "params"
                continue
            if section == "params" and row:
                key, value = row[0], row[1]
                try: params[key] = float(value)
                except: params[key] = value
    return params


def read_data_from_load_curve(period_df):
    T = list(range(len(period_df)))
    power = period_df["Power_Output_MW"].to_list()
    commitment = period_df["Commitment"].to_list()
    startups = period_df["Startups"].to_list()
    market_price = period_df["Market_Price_BGN_per_MWh"].to_numpy()
    year = period_df['DateTime'].dt.year.iloc[0]
    date_series = period_df['DateTime'].to_list()

    return T, power, commitment, startups, market_price, year, date_series


def get_date_from_filename(filename):
        # format: 0001_20251108_1100_results.csv
        try:
            parts = filename.split("_")
            date_raw = parts[1]
            time_raw = parts[2]
            dt = datetime.strptime(date_raw + time_raw, "%Y%m%d%H%M")
            return dt.strftime("%d %B %Y %H:%M")
        except Exception:
            return "Unknown"

# PYOMO MODEL VIEWS
def build_model(n_hours, market_price, params, degradation):
    T = range(n_hours)
    model = pyo.ConcreteModel()
    model.hour = pyo.RangeSet(0, n_hours - 1)

    # variables
    model.u = pyo.Var(model.hour, within=pyo.Binary)
    model.v = pyo.Var(model.hour, within=pyo.Binary)
    model.p = pyo.Var(model.hour, within=pyo.NonNegativeReals)

    # constraints
    model.gen_limit_upper = pyo.Constraint(model.hour, rule=lambda m, h: m.p[h] <= params["max_power"] * m.u[h])
    model.gen_limit_lower = pyo.Constraint(model.hour, rule=lambda m, h: m.p[h] >= params["min_power"] * m.u[h])
    model.ramp_up = pyo.Constraint(model.hour,
                                   rule=lambda m, h: pyo.Constraint.Skip if h == 0 else m.p[h] - m.p[h - 1] <= params[
                                       "ramp_up"] + params["max_power"] * (m.u[h] - m.u[h - 1]))
    model.ramp_down = pyo.Constraint(model.hour,
                                     rule=lambda m, h: pyo.Constraint.Skip if h == 0 else m.p[h - 1] - m.p[h] <= params[
                                         "ramp_down"] + params["max_power"] * (m.u[h - 1] - m.u[h]))
    model.startup_logic = pyo.Constraint(model.hour, rule=lambda m, h: m.v[h] >= m.u[h] - (m.u[h - 1] if h > 0 else 0))
    model.max_startups_constraint = pyo.Constraint(rule=lambda m: sum(m.v[t] for t in T) <= params["max_startups"])
    model.min_cumulative_power = pyo.Constraint(rule=lambda m: sum(m.p[t] for t in T) >= params["min_cumulative_power"])
    model.min_cumulative_uptime = pyo.Constraint(
        rule=lambda m: sum(m.u[t] for t in T) >= params["min_cumulative_uptime"])

    # objective
    def obj_rule(m):
        revenue = sum(m.p[t] * market_price[t] for t in T)
        gen_cost = sum((params["coal_price"] * params["heat_rate"] * degradation[t] + params["co2_price_bgn"] * params[
            "emissions"]) * m.p[t] for t in T)
        startup_costs = sum(m.v[t] * params["startup_cost"] for t in T)
        return revenue - gen_cost - startup_costs

    model.obj = pyo.Objective(rule=obj_rule, sense=pyo.maximize)
    return model, T


def solve_model(model):
    solver = pyo.SolverFactory("cbc")
    solver.solve(model)
    return model


def extract_results(model, T):
    power = [pyo.value(model.p[t]) for t in T]
    commitment = [pyo.value(model.u[t]) for t in T]
    startups = [pyo.value(model.v[t]) for t in T]
    return power, commitment, startups

# FINANCIAL CALCULATIONS VIEWS
def compute_financials(power, commitment, startups, market_price, params, degradation):
    hours = len(power)
    revenue = sum(power[t] * market_price[t] for t in range(hours))
    gen_cost = sum(
        (params["coal_price"] * params["heat_rate"] * degradation[t] + params["co2_price_bgn"] * params["emissions"]) * power[t] for t in
        range(hours))
    startup_total = sum(startups[t] * params["startup_cost"] for t in range(hours))
    total_profit = revenue - gen_cost - startup_total

    total_power = sum(power)
    return {
        "total_power": total_power,
        "total_commitment_hours": sum(commitment),
        "relative_uptime_percent": (sum(commitment) / len(power)) * 100,
        "total_revenue": revenue,
        "revenue_per_MWh": revenue / total_power if total_power > 0 else 0,
        "total_profit": total_profit,
        "profit_per_MWh": total_profit / total_power if total_power > 0 else 0,
        "total_expenses": gen_cost + startup_total,
        "expenses_per_MWh": (gen_cost + startup_total) / total_power if total_power > 0 else 0,
        "coal_co2_expenses": gen_cost,
        "coal_co2_per_MWh": gen_cost / total_power if total_power > 0 else 0,
        "total_startups": sum(startups),
        "startup_cost_total": startup_total,
        "startup_cost_per_MWh": startup_total / total_power if total_power > 0 else 0
    }


def save_results_csv(financials, load_curve_df, index_str, date_str, params):
    results_csv_filename = f"{index_str}_{date_str}_results.csv"
    load_curve_csv_filename = f"{index_str}_{date_str}_load_curve.csv"

    # save load curve
    load_curve_df.to_csv(os.path.join(settings.DATA_OUTPUT_DIR, load_curve_csv_filename), index=False)

    # save financials and params
    rows = [("metric", "value", "unit")]
    for k, v in financials.items():
        rows.append((k, v, "BGN" if "cost" in k or "revenue" in k or "profit" in k else ""))

    if params:
        rows.append(("---parameters---", "", ""))
        for k, v in params.items():
            rows.append((k, v, ""))

    with open(os.path.join(settings.DATA_OUTPUT_DIR, results_csv_filename), "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    return results_csv_filename, load_curve_csv_filename

# PLOTTING VIEWS
def create_interactive_plot(T, market_price, power, commitment, max_power, title, date_series=None):
    # convert inputs to lists (range or other iterables)
    market_price = list(market_price)
    power = list(power)
    commitment = list(commitment)

    if date_series is not None:
        x_axis = list(date_series)
    else:
        x_axis = list(T)

    # create step curve for power and commitment
    T_step = []
    power_step = []
    commit_step = []

    for i in range(len(x_axis)-1):
        # duplicate points to make steps
        T_step.extend([x_axis[i], x_axis[i+1]])
        power_step.extend([power[i], power[i]])
        commit_step.extend([max_power * commitment[i], max_power * commitment[i]])

    # append last point
    T_step.append(x_axis[-1])
    power_step.append(power[-1])
    commit_step.append(max_power * commitment[-1])

    # create hover text showing all three values at each original hour
    hover_combined = [
        f"{x_axis[i]:%d %b %Y %H:%M} <br> market price: {market_price[i]:.2f} bgn/mwh <br>"
        f"power output: {power[i]:.2f} mw<br>committed: {max_power * commitment[i]:.2f} mw"
        for i in range(len(x_axis))
    ]

    fig = go.Figure()

    # market price line (hover shows all three values)
    fig.add_trace(go.Scatter(
        x=x_axis, y=market_price, mode='lines', name='Market price (BGN/MWh)',
        line=dict(color='black'),
        hoverinfo='text', hovertext=hover_combined
    ))

    # step curve for power output
    fig.add_trace(go.Scatter(
        x=T_step, y=power_step, mode='lines', name='Power output (MW)',
        line=dict(color='blue', width=2),
        hoverinfo='skip'  # skip hover because combined hover is used above
    ))

    # filled area for committed power
    fig.add_trace(go.Scatter(
        x=T_step + T_step[::-1],
        y=commit_step + [0]*len(commit_step),
        name='Commitment',
        fill='toself',
        fillcolor='rgba(144,238,144,0.3)',
        line=dict(color='rgba(0,0,0,0)'),
        hoverinfo='skip'  # skip hover for fill
    ))

    # layout settings
    fig.update_layout(
        title=title,
        xaxis_title='Date & Time',
        yaxis_title='Value',
        template='plotly_white',
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        xaxis = dict(fixedrange=False),
        yaxis = dict(fixedrange=True)
    )

    return fig


def save_plot_png(fig, filename, width=1200, height=600):
    # save a plotly figure as png on the server
    png_bytes = pio.to_image(fig, format='png', width=width, height=height)
    os.makedirs(settings.DATA_OUTPUT_DIR, exist_ok=True)
    png_path = os.path.join(settings.DATA_OUTPUT_DIR, filename)
    with open(png_path, "wb") as f:
        f.write(png_bytes)
    return png_path, png_bytes


def generate_interactive_html(fig):
    # return html string for embedding interactive plot
    return fig.to_html(include_plotlyjs='cdn', full_html=False)


def full_plot(T, market_price, power, commitment, max_power, index_str, date_str, date_series=None):
    fig = create_interactive_plot(
        T,
        market_price,
        power,
        commitment,
        max_power,
        title="Unit Commitment with Economic Dispatch",
        date_series=date_series
    )

    # save png on server
    png_filename = f"{index_str}_{date_str}_plot.png"
    png_path, png_bytes = save_plot_png(fig, png_filename)

    # convert to base64 if needed
    png_base64 = base64.b64encode(png_bytes).decode("utf-8")

    # generate html for web
    html_code = generate_interactive_html(fig)

    # return filename and base64
    return {
        "png_file": png_filename,
        "png_base64": png_base64,
        "html": html_code,
        "fig": fig
    }
