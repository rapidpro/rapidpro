import functools
import operator

from django.db import models
from django.db.models import Q, Sum


class ScopeCountQuerySet(models.QuerySet):
    """
    Specialized queryset for scope + count models
    """

    def prefixes(self, prefixes: list):
        """
        Filters by the given scope prefixes.
        """
        return self.filter(functools.reduce(operator.or_, [Q(scope__startswith=p) for p in prefixes]))

    def sum(self) -> int:
        """
        Sums counts over the matching rows.
        """
        return self.aggregate(count_sum=Sum("count"))["count_sum"] or 0

    def scope_totals(self) -> dict[str, int]:
        """
        Sums counts grouped by scope.
        """
        counts = self.values_list("scope").annotate(count_sum=Sum("count"))
        return {c[0]: c[1] for c in counts}
