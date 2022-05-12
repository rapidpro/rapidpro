from django.db import models


class IDSliceQuerySet(models.query.RawQuerySet):
    """
    QuerySet defined by a model, set of ids, offset and total count
    """

    def __init__(self, model, ids, *, offset, total, only=None, using="default", _raw_query=None):
        if _raw_query:
            # we're being cloned so can reuse our SQL query
            raw_query = _raw_query
        else:
            cols = ", ".join([f"t.{f}" for f in only]) if only else "t.*"
            table = model._meta.db_table

            if len(ids) > 0:
                # build a list of sequence to model id, so we can sort by the sequence in our results
                pairs = ", ".join(str((seq, model_id)) for seq, model_id in enumerate(ids, start=1))

                raw_query = f"""SELECT {cols} FROM {table} t JOIN (VALUES {pairs}) tmp_resultset (seq, model_id) ON t.id = tmp_resultset.model_id ORDER BY tmp_resultset.seq"""
            else:
                raw_query = f"""SELECT {cols} FROM {table} t WHERE t.id < 0"""

        super().__init__(raw_query, model, using=using)

        self.ids = ids
        self.offset = offset
        self.total = total

    def __getitem__(self, k):
        """
        Called to slice our queryset. ID Slice Query Sets care created pre-sliced, that is the offset and counts should
        match the way any kind of paginator is going to try to slice the queryset.
        """
        if isinstance(k, int):
            # single item
            if k < self.offset or k >= self.offset + len(self.ids):
                raise IndexError("attempt to access element outside slice")

            return super().__getitem__(k - self.offset)

        elif isinstance(k, slice):
            start = k.start if k.start else 0
            if start != self.offset:
                raise IndexError(
                    f"attempt to slice ID queryset with differing offset: [{k.start}:{k.stop}] != [{self.offset}:{self.offset+len(self.ids)}]"
                )

            return list(self)[: k.stop - self.offset]

        else:
            raise TypeError(f"__getitem__ index must be int, not {type(k)}")

    def all(self):
        return self

    def none(self):
        return IDSliceQuerySet(self.model, [], offset=0, total=0, using=self._db)

    def count(self):
        return self.total

    def filter(self, **kwargs):
        ids = list(self.ids)

        for k, v in kwargs.items():
            if k == "pk":
                ids = [i for i in ids if i == int(v)]
            elif k == "pk__in":
                v = {int(j) for j in v}  # django forms like passing around pks as strings
                ids = [i for i in ids if i in v]
            else:
                raise ValueError(f"IDSliceQuerySet instances can only be filtered by pk, not {k}")

        return IDSliceQuerySet(self.model, ids, offset=0, total=len(ids), using=self._db)

    def _clone(self):
        return self.__class__(
            self.model, self.ids, offset=self.offset, total=self.total, using=self._db, _raw_query=self.raw_query
        )
