from django.http import HttpResponse, Http404
from django.conf import settings
import os
from .views_extracted import extracted_plot
from .views_utils import read_load_curve, read_params_from_results_csv, filter_load_curve_by_dates, compute_financials
import pandas as pd
from io import BytesIO, StringIO
import zipfile
import csv


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