from django.shortcuts import render, redirect
from django.http import Http404
from .views_utils import read_results_csv, read_load_curve, read_params_from_results_csv, get_date_from_filename, \
    create_interactive_plot, generate_interactive_html, read_data_from_load_curve
from ..forms import ExtractPeriodForm
from django.conf import settings
import os
import pandas as pd


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