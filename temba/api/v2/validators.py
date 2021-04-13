from rest_framework.validators import UniqueValidator, qs_filter


class UniqueForOrgValidator(UniqueValidator):
    """
    UniqueValidator requires a queryset at compile time but we always need to include org, which isn't known until
    request time. So this subclass reads org from the field's context and applies it to the queryset at runtime.
    """

    requires_context = True

    def __init__(self, queryset, ignore_case=True, message=None):
        lookup = "iexact" if ignore_case else "exact"

        super().__init__(queryset, message=message, lookup=lookup)

    def filter_queryset(self, value, queryset, field_name):
        queryset = super().filter_queryset(value, queryset, field_name)
        return qs_filter(queryset, **{"org": self.org})

    def __call__(self, value, serializer_field):
        self.org = serializer_field.context["org"]

        super().__call__(value, serializer_field)
