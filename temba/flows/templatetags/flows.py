from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

register = template.Library()

exclusion_display = {
    "in_a_flow": _("Skip contacts currently in a flow"),
    "not_seen_since_days": _("Skip inactive contacts"),
    "started_previously": _("Skip repeat contacts"),
}


@register.filter
def exclusions(exclusions):
    def render_exclusion(k, v):
        return f"<temba-label icon='filter' backgroundcolor='#fff' textcolor='#999' style='--widget-shadow:none'>{exclusion_display.get(k, k)}</temba-label>"

    return mark_safe("".join([render_exclusion(k, v) for k, v in exclusions.items()]))
