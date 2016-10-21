from __future__ import unicode_literals

from rest_framework.validators import UniqueValidator, qs_filter


class UniqueForOrgValidator(UniqueValidator):
    """
    UniqueValidator requires a queryset at compile time but we always need to include org, which isn't known until
    request time. So this subclass reads org from the field's context and applies it to the queryset at runtime.
    """
    def __init__(self, queryset, ignore_case=True, message=None):
        super(UniqueForOrgValidator, self).__init__(queryset, message=message)

        self.ignore_case = ignore_case

    def set_context(self, serializer_field):
        super(UniqueForOrgValidator, self).set_context(serializer_field)

        self.org = serializer_field.context['org']

    def filter_queryset(self, value, queryset):
        field_lookup = 'iexact' if self.ignore_case else 'exact'
        filter_kwargs = {'org': self.org, '%s__%s' % (self.field_name, field_lookup): value}
        return qs_filter(queryset, **filter_kwargs)
