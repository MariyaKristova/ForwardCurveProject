import base64
from django.shortcuts import render
from .views_results import view_result
from .views_utils import create_interactive_plot, generate_interactive_html, compute_financials, calculate_degradation, \
    read_data_from_load_curve
from .views_utils import read_params_from_results_csv, read_load_curve
from ..forms import ExtractPeriodForm
import pandas as pd


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