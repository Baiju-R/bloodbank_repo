from django import template

register = template.Library()


@register.simple_tag
def star_range():
    return range(1, 6)

@register.filter
def percentage(value, total):
    """Calculates percentage."""
    try:
        if float(total) == 0:
            return 0
        return round((float(value) * 100) / float(total), 1)
    except (ValueError, TypeError, ZeroDivisionError):
        return 0

@register.filter
def mul(value, arg):
    """Multiplies the value by the argument."""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0

@register.filter
def div(value, arg):
    """Divides the value by the argument."""
    try:
        if float(arg) == 0:
            return 0
        return float(value) / float(arg)
    except (ValueError, TypeError):
        return 0
