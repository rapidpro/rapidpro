from __future__ import unicode_literals

from django import template


register = template.Library()


@register.filter
def get_ruleset_value(ruleset, values):
    for value in values:
        if value.ruleset == ruleset:
            return value.category
    return None
