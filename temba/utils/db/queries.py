import functools
import operator

from django.db import models
from django.db.models import Q, Subquery


def or_list(qs: list[Q]) -> Q:
    """
    Convenience function to OR together a list of Q objects
    """
    return functools.reduce(operator.or_, qs)


class SubqueryCount(Subquery):
    # Count(..) in Django uses grouping and breaks for more than 1 annotated column.
    # See https://stackoverflow.com/a/47371514/1164966
    template = "(SELECT count(*) FROM (%(subquery)s) _count)"
    output_field = models.IntegerField()
