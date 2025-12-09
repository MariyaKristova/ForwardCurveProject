# standard library
import os
import re
import csv
import zipfile
import base64
from datetime import datetime
from io import BytesIO, StringIO

# third-party libraries
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pyomo.environ as pyo
import plotly.graph_objs as go
import plotly.io as pio

# django
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import render, redirect

# local app imports
from .forms import PlantParametersForm, ExtractPeriodForm


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


# MAIN VIEWS
def upload_view(request):
    if request.method == "POST":
        form = PlantParametersForm(request.POST)
        if form.is_valid():
            # read market price
            df, market_price = read_excel_file(form.cleaned_data["excel_file"])
            n_hours = len(df)

            # calculate degradation
            year = df['DateTime'].dt.year.iloc[0]
            degradation = calculate_degradation(n_hours, year)

            # prepare parameters
            params = {k: form.cleaned_data[k] for k in [
                "min_power", "max_power", "ramp_up", "ramp_down", "emissions",
                "coal_price", "heat_rate", "co2_price_bgn", "startup_cost",
                "max_startups", "min_cumulative_power", "min_cumulative_uptime"
            ]}

            # build & solve model
            model, T = build_model(n_hours, market_price, params, degradation)
            model = solve_model(model)
            power, commitment, startups = extract_results(model, T)

            # financial metrics
            financials = compute_financials(power, commitment, startups, market_price, params, degradation)

            # generate run_id and date_str
            existing_files = os.listdir(settings.DATA_OUTPUT_DIR)
            numbers = [int(m.group(1)) for f in existing_files if (m := re.match(r"^(\d{4})_", f))]
            next_index = max(numbers) + 1 if numbers else 1
            run_id = str(next_index).zfill(4)
            date_str = datetime.now().strftime("%Y%m%d_%H%M")
            dt = datetime.strptime(date_str, "%Y%m%d_%H%M")
            date = dt.strftime("%d %B %Y %H:%M")

            # save load curve with DateTime
            load_curve_df = pd.DataFrame({
                'DateTime': df['DateTime'],
                "Hour": list(T),
                "Power_Output_MW": power,
                "Commitment": commitment,
                "Startups": startups,
                "Market_Price_BGN_per_MWh": market_price
            })
            results_csv_file, load_curve_csv_file = save_results_csv(financials, load_curve_df, run_id, date_str, params)

            # create interactive plot
            plot_output = full_plot(T, market_price, power, commitment, params["max_power"], run_id, date_str, date_series=load_curve_df['DateTime'])

            png_file = plot_output["png_file"]
            interactive_graph = plot_output["html"]

            extract_form = ExtractPeriodForm()

            context = {
                "graph": interactive_graph,
                "financials": financials,
                "results_csv_file": results_csv_file,
                "load_curve_csv_file": load_curve_csv_file,
                "run_id": run_id,
                "png_file": png_file,
                "extract_form": extract_form,
                "date": date,
            }

            return render(request, "plotapp/result.html", context)

    else:
        form = PlantParametersForm()

    return render(request, "plotapp/upload.html", {"form": form})


def view_result(request, run_id, extract_form=None):
    # find png if exists
    png_file = next((f for f in os.listdir(settings.DATA_OUTPUT_DIR) if f.startswith(run_id) and f.endswith("_plot.png")), None )

    # read results csv for metrics
    results_csv_file = read_results_csv(run_id)
    df_results = pd.read_csv(os.path.join(settings.DATA_OUTPUT_DIR, results_csv_file))
    financials = dict(zip(df_results["metric"], df_results["value"]))

    # read date
    date = get_date_from_filename(results_csv_file)

    # read load curve csv
    load_curve_csv_file = read_load_curve(run_id)
    load_curve_df = pd.read_csv(os.path.join(settings.DATA_OUTPUT_DIR, load_curve_csv_file), parse_dates=['DateTime'])

    # convert load curve dataframe into arrays for plotting
    T, power, commitment, startups, market_price, year, date_series = read_data_from_load_curve(load_curve_df)

    # read max_power for parameters section of results csv
    params = read_params_from_results_csv(run_id)

    fig = create_interactive_plot(
        T,
        market_price,
        power,
        commitment,
        params["max_power"],
        "Unit Commitment with Economic Dispatch",
        date_series=date_series,
    )

    # generate html for embedding interactive plot in the page
    interactive_graph = generate_interactive_html(fig)

    # initialize extract form
    if extract_form is None:
        extract_form = ExtractPeriodForm()

    context = {
        "graph": interactive_graph,
        "financials": financials,
        "results_csv_file": results_csv_file,
        "load_curve_csv_file": load_curve_csv_file,
        "run_id": run_id,
        "png_file": png_file,
        "extract_form": extract_form,
        "date": date,
    }

    return render(request, "plotapp/result.html", context)


def all_results(request):
    files = os.listdir(settings.DATA_OUTPUT_DIR)
    results = []

    for f in files:
        if f.endswith("_results.csv"):
            date_output = get_date_from_filename(f)
            run_id = f.split("_")[0]
            results.append((run_id, date_output))

    # sort descending by run_id
    results = sorted(results, key=lambda x: int(x[0]), reverse=True)

    return render(request, "plotapp/all_results.html", {"results": results})


def delete_result(request, run_id):
    files = os.listdir(settings.DATA_OUTPUT_DIR)
    deleted_files = []

    for f in files:
        if f.startswith(run_id):
            file_path = os.path.join(settings.DATA_OUTPUT_DIR, f)
            try:
                os.remove(file_path)
                deleted_files.append(f)
            except Exception as e:
                print(f"Error deleting {f}: {e}")

    if not deleted_files:
        raise Http404("No files found to delete")

    return redirect("all_results")


# DOWNLOAD VIEWS
def download_file(file_path, download_name):
    if not os.path.exists(file_path):
        raise Http404("File not found")

    with open(file_path, "rb") as f:
        file_data = f.read()

    response = HttpResponse(file_data, content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{download_name}"'
    return response


def download_curve_csv(request, filename):
    # serve CSV curve file from DATA_OUTPUT_DIR
    file_path = os.path.join(settings.DATA_OUTPUT_DIR, filename)
    return download_file(file_path, filename)


def download_png(request, filename):
    # serve PNG file from DATA_OUTPUT_DIR
    file_path = os.path.join(settings.DATA_OUTPUT_DIR, filename)
    return download_file(file_path, filename)


def download_results_csv(request, filename):
    # serve CSV results file from DATA_OUTPUT_DIR
    file_path = os.path.join(settings.DATA_OUTPUT_DIR, filename)
    return download_file(file_path, filename)


# EXTRACTED PERIOD VIEWS
def filter_load_curve_by_dates(load_curve_df, extract_form):
    start_date = extract_form.cleaned_data['start_date']
    end_date = extract_form.cleaned_data['end_date']
    year = load_curve_df['DateTime'].dt.year.iloc[0]
    start_dt = start_date.replace(year=year)
    end_dt = end_date.replace(year=year)

    mask = (load_curve_df['DateTime'] >= pd.Timestamp(start_dt)) & (load_curve_df['DateTime'] <= pd.Timestamp(end_dt))
    period_df = load_curve_df.loc[mask].copy()

    return period_df, start_date, end_date


def extracted_plot(T, market_price, power, commitment, max_power, title, date_series, return_bytes=False):
    # behaves similarly to full_plot, but does NOT save files
    fig = create_interactive_plot(
        T=T,
        market_price=market_price,
        power=power,
        commitment=commitment,
        max_power=max_power,
        title=title,
        date_series=date_series
    )

    # PNG bytes (same result as save_plot_png, but in-memory)
    png_bytes = fig.to_image(format="png")

    if return_bytes:
        return png_bytes

    # base64 for <img>
    png_base64 = base64.b64encode(png_bytes).decode("utf-8")

    # HTML for iframe interactive graph
    html_code = generate_interactive_html(fig)

    return {
        "png_base64": png_base64,
        "html": html_code,
        "fig": fig
    }


def extracted_result_view(request, run_id):
    # read params from results csv and load curve
    params = read_params_from_results_csv(run_id)
    load_curve_csv_file = read_load_curve(run_id)
    load_curve_df = pd.read_csv(os.path.join(settings.DATA_OUTPUT_DIR, load_curve_csv_file), parse_dates=['DateTime'])

    extract_form = ExtractPeriodForm(request.POST or None)
    financials = {}
    html_chart = ""
    start_date = None
    end_date = None

    if request.method == "POST":
        if extract_form.is_valid():
            period_df, start_date, end_date = filter_load_curve_by_dates(load_curve_df, extract_form)

            if period_df.empty:
                extract_form.add_error(None, "No data for selected period")

            else:
                # extract data for financials and plot
                T, power, commitment, startups, market_price, year, date_series = read_data_from_load_curve(period_df)
                degradation = calculate_degradation(len(period_df), year)

                # compute financials
                financials = compute_financials(power, commitment, startups, market_price, params, degradation)

                plot_data = extracted_plot(
                    T,
                    market_price,
                    power,
                    commitment,
                    max_power=params["max_power"],
                    title="Unit Commitment with Economic Dispatch (Extracted)",
                    date_series=date_series
                )
                html_chart = plot_data["html"]

        else:
            return view_result(request, run_id, extract_form=extract_form)

    context = {
        "interactive_html": html_chart,
        "financials": financials,
        "run_id": run_id,
        "extract_form": extract_form,
        "start_date": start_date.strftime("%d.%m") if start_date else "",
        "end_date": end_date.strftime("%d.%m") if end_date else "",
    }

    return render(request, "plotapp/extracted_result.html", context)


def download_extracted_zip(request, run_id):
    # read params from results csv and load curve
    params = read_params_from_results_csv(run_id)
    load_curve_csv_file = read_load_curve(run_id)
    load_curve_df = pd.read_csv(os.path.join(settings.DATA_OUTPUT_DIR, load_curve_csv_file), parse_dates=['DateTime'])

    extract_form = ExtractPeriodForm(request.GET)
    if not extract_form.is_valid():
        raise Http404("Invalid date range")

    period_df, _, _ = filter_load_curve_by_dates(load_curve_df, extract_form)
    if period_df.empty:
        raise Http404("No data for selected period")

    # extract data for financials and plot
    T, power, commitment, startups, market_price, year, date_series = read_data_from_load_curve(period_df)
    degradation = calculate_degradation(len(period_df), year)
    financials = compute_financials(power, commitment, startups, market_price, params, degradation)

    # create in-memory zip
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        # CSV of period
        period_csv_buffer = StringIO()
        period_df.to_csv(period_csv_buffer, index=False)
        zf.writestr(f"{run_id}_extracted_load_curve.csv", period_csv_buffer.getvalue())

        # Financials CSV
        fin_buffer = StringIO()
        writer = csv.writer(fin_buffer)
        writer.writerow(["metric", "value"])
        for k, v in financials.items():
            writer.writerow([k, v])
        zf.writestr(f"{run_id}_extracted_financials.csv", fin_buffer.getvalue().encode("utf-8"))

        png_bytes = extracted_plot(
            T=T,
            market_price=market_price,
            power=power,
            commitment=commitment,
            max_power=params["max_power"],
            title="Unit Commitment with Economic Dispatch (Extracted)",
            date_series=date_series,
            return_bytes=True
        )
        zf.writestr(f"{run_id}_extracted_plot.png", png_bytes)

    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer, content_type="application/zip")
    response['Content-Disposition'] = f'attachment; filename={run_id}_extracted.zip'
    return response

# ERRORS VIEWS
def custom_404(request, exception):
    return render(request, "plotapp/errors/404.html", status=404)

def custom_500(request):
    return render(request, "plotapp/errors/500.html", status=500)

