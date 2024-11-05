from django.db import models
from django.db.models import Subquery


class SubqueryCount(Subquery):
    # Count(..) in Django uses grouping and breaks for more than 1 annotated column.
    # See https://stackoverflow.com/a/47371514/1164966
    template = "(SELECT count(*) FROM (%(subquery)s) _count)"
    output_field = models.IntegerField()
