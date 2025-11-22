from django import template
register = template.Library()

@register.filter
def space_thousands(value, decimal_places=None):
    try:
        if decimal_places is None:
            formatted = f"{int(round(value)):,}"
        else:
            formatted = f"{value:,.{int(decimal_places)}f}"
        return formatted.replace(",", " ")
    except (ValueError, TypeError):
        return value