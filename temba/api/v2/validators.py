from rest_framework.validators import UniqueValidator, qs_filter


class UniqueForOrgValidator(UniqueValidator):
    """
    UniqueValidator requires a queryset at compile time but we always need to include org, which isn't known until
    request time. So this subclass reads org from the field's context and applies it to the queryset at runtime.
    """

    def __init__(self, queryset, ignore_case=True, message=None):
        lookup = "iexact" if ignore_case else "exact"

        super().__init__(queryset, message=message, lookup=lookup)

    def set_context(self, serializer_field):
        super().set_context(serializer_field)

        self.org = serializer_field.context["org"]

    def filter_queryset(self, value, queryset):
        queryset = super().filter_queryset(value, queryset)
        return qs_filter(queryset, **{"org": self.org})
