from django import template

register = template.Library()

@register.filter
def flow_responses_since(flow, time):
    return flow.get_responses_since(time)
