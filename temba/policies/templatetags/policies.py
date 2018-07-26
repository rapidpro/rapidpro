from django import template

register = template.Library()


@register.filter
def has_consent(policy, user):
    if policy.requires_consent:
        return policy.has_consent(user)
    return True
