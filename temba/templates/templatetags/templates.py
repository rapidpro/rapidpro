import re

from django import template
from django.utils.safestring import mark_safe

register = template.Library()
handlebars_regex = re.compile(r"{{([^}]+)}}")


@register.filter
def handlebars(text):
    return mark_safe(handlebars_regex.sub(r"<code>{{\1}}</code>", text))
