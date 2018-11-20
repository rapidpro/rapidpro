
from django import template
from django.utils.safestring import mark_safe



register = template.Library()



@register.filter
def as_date(date_item):
    return mark_safe('<span>%s</span></br><span>%s</span>'%(date_item.strftime("%d/%m/%Y"),
                                                        date_item.strftime("%H:%M")))
