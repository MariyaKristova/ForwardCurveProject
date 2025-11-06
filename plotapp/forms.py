from django import forms

class PlantParametersForm(forms.Form):
    min_power = forms.FloatField(label="Minimum Power (MW)", initial=270)
    max_power = forms.FloatField(label="Maximum Power (MW)", initial=600)
    ramp_up = forms.FloatField(label="Ramp Up (MW/hour)", initial=100)
    ramp_down = forms.FloatField(label="Ramp Down (MW/hour)", initial=100)

    emissions = forms.FloatField(label="Emissions (t CO2/MWh)", initial=1.447)
    coal_price = forms.FloatField(label="Coal Price (BGN/kJ)", initial=2.98e-6)
    heat_rate = forms.FloatField(label="Heat Rate (kJ/MWh)", initial=10322e3)
    co2_price_bgn = forms.FloatField(label="CO2 Price (BGN/t)", initial=81.56 * 1.95583)

    startup_cost = forms.FloatField(label="Startup Cost (BGN)", initial=49191)
    max_startups = forms.IntegerField(label="Maximum Startups Per Year", initial=33)

    min_cumulative_power = forms.FloatField(label="Minimum Annual Generation (MWh)", initial=1858758)

    file = forms.FileField(label="Upload DAM Price Excel File")
