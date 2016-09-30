from __future__ import unicode_literals

from rest_framework.validators import UniqueValidator


class UniqueForOrgValidator(UniqueValidator):
    """
    UniqueValidator requires a queryset at compile time but we always need to include org, which isn't known until
    request time. So this subclass reads org from the field's context and applies it to the queryset at runtime.
    """
    def set_context(self, serializer_field):
        super(UniqueForOrgValidator, self).set_context(serializer_field)

        self.org = serializer_field.context['org']

    def filter_queryset(self, value, queryset):
        qs = super(UniqueForOrgValidator, self).filter_queryset(value, queryset)
        return qs.filter(org=self.org)
