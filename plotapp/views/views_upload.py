from django.shortcuts import render
from .views_utils import full_plot, save_results_csv
from .views_utils import read_excel_file, calculate_degradation, build_model, solve_model, extract_results, compute_financials
from django.conf import settings
import os, re
from datetime import datetime
import pandas as pd
from ..forms import PlantParametersForm, ExtractPeriodForm


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
                "coal_price", "heat_rate", "co2_price_eur", "startup_cost",
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
                "Market_Price_EUR_per_MWh": market_price
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