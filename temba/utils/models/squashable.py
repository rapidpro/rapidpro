from abc import abstractmethod

from django.db import connection, models
from django.db.models import Sum


class SquashableModel(models.Model):
    """
    Base class for models which track counts by delta insertions which are then periodically squashed
    """

    squash_over = ()
    squash_max_distinct = 5000

    id = models.BigAutoField(auto_created=True, primary_key=True)
    is_squashed = models.BooleanField(default=False)

    @classmethod
    def get_squash_over(cls) -> tuple:
        return cls.squash_over

    @classmethod
    def get_unsquashed(cls):
        return cls.objects.filter(is_squashed=False)

    @classmethod
    def squash(cls) -> int:
        """
        Squashes all distinct sets of counts with unsquashed rows into a single row if they sum to non-zero or just
        deletes them if they sum to zero. Returns the number of sets squashed.
        """

        num_sets = 0
        squash_over = cls.get_squash_over()
        distinct_sets = (
            cls.get_unsquashed()
            .values(*squash_over)
            .order_by(*squash_over)
            .distinct(*squash_over)[: cls.squash_max_distinct]
        )

        for distinct_set in distinct_sets:
            with connection.cursor() as cursor:
                sql, params = cls.get_squash_query(distinct_set)

                cursor.execute(sql, params)

            num_sets += 1

        return num_sets

    @classmethod
    @abstractmethod
    def get_squash_query(cls, distinct_set: dict) -> tuple:  # pragma: no cover
        pass

    @classmethod
    def sum(cls, instances) -> int:
        count_sum = instances.aggregate(count_sum=Sum("count"))["count_sum"]
        return count_sum if count_sum else 0

    class Meta:
        abstract = True


class DailyCountModel(SquashableModel):
    """
    Base for daily scoped count squashable models
    """

    squash_over = ("count_type", "scope", "day")

    count_type = models.CharField(max_length=1)
    scope = models.CharField(max_length=32)
    day = models.DateField()
    count = models.IntegerField()

    @classmethod
    def get_squash_query(cls, distinct_set: dict) -> tuple:
        sql = f"""
        WITH removed as (
            DELETE FROM {cls._meta.db_table} WHERE count_type = %s AND scope = %s AND day = %s RETURNING count
        )
        INSERT INTO {cls._meta.db_table}(count_type, scope, day, count, is_squashed)
        VALUES (%s, %s, %s, GREATEST(0, (SELECT SUM(count) FROM removed)), TRUE);
        """

        return sql, (distinct_set["count_type"], distinct_set["scope"], distinct_set["day"]) * 2

    @classmethod
    def _get_counts(cls, count_type: str, scopes: dict, since, until):
        counts = cls.objects.filter(count_type=count_type, scope__in=scopes.keys())
        if since:
            counts = counts.filter(day__gte=since)
        if until:
            counts = counts.filter(day__lt=until)
        return counts

    @classmethod
    def _get_count_set(cls, count_type: str, scopes: dict, since, until):
        return DailyCountModel.CountSet(cls._get_counts(count_type, scopes, since, until), scopes)

    class CountSet:
        """
        A queryset of counts which can be aggregated in different ways
        """

        def __init__(self, counts, scopes):
            self.counts = counts
            self.scopes = scopes

        def total(self):
            """
            Calculates the overall total over a set of counts
            """
            total = self.counts.aggregate(total=Sum("count"))
            return total["total"] if total["total"] is not None else 0

        def scope_totals(self):
            """
            Calculates per-scope totals over a set of counts
            """
            totals = list(self.counts.values_list("scope").annotate(replies=Sum("count")))
            total_by_encoded_scope = {t[0]: t[1] for t in totals}

            total_by_scope = {}
            for encoded_scope, scope in self.scopes.items():
                total_by_scope[scope] = total_by_encoded_scope.get(encoded_scope, 0)

            return total_by_scope

        def day_totals(self):
            """
            Calculates per-day totals over a set of counts
            """
            return list(self.counts.values_list("day").annotate(total=Sum("count")).order_by("day"))

        def month_totals(self):
            """
            Calculates per-month totals over a set of counts
            """
            counts = self.counts.extra(select={"month": 'EXTRACT(month FROM "day")'})
            return list(counts.values_list("month").annotate(replies=Sum("count")).order_by("month"))

    class Meta:
        abstract = True


class DailyTimingModel(DailyCountModel):
    """
    Base for daily scoped count+seconds squashable models
    """

    seconds = models.BigIntegerField()

    @classmethod
    def get_squash_query(cls, distinct_set: dict) -> tuple:
        sql = f"""
        WITH removed as (
            DELETE FROM {cls._meta.db_table} WHERE count_type = %s AND scope = %s AND day = %s RETURNING count, seconds
        )
        INSERT INTO {cls._meta.db_table}(count_type, scope, day, count, seconds, is_squashed)
        VALUES (%s, %s, %s, GREATEST(0, (SELECT SUM(count) FROM removed)), GREATEST(0, (SELECT SUM(seconds) FROM removed)), TRUE);
        """

        return sql, (distinct_set["count_type"], distinct_set["scope"], distinct_set["day"]) * 2

    @classmethod
    def _get_count_set(cls, count_type: str, scopes: dict, since, until):
        return DailyTimingModel.CountSet(cls._get_counts(count_type, scopes, since, until), scopes)

    class CountSet(DailyCountModel.CountSet):
        """
        A queryset of counts which can be aggregated in different ways
        """

        def day_averages(self, rounded=False):
            """
            Calculates per-day seconds averages over a set of counts
            """
            totals = (
                self.counts.values_list("day")
                .annotate(total_count=Sum("count"), total_seconds=Sum("seconds"))
                .order_by("day")
            )

            return [(t[0], round(t[2] / t[1]) if rounded else t[2] / t[1]) for t in totals]

    class Meta:
        abstract = True
