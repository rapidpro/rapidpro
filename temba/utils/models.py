from __future__ import unicode_literals

from django.db import models
from django.utils.translation import ugettext_lazy as _
from smartmin.models import SmartModel
from uuid import uuid4


def generate_uuid():
    return unicode(uuid4())


class TembaModel(SmartModel):

    uuid = models.CharField(max_length=36, unique=True, db_index=True, default=generate_uuid,
                            verbose_name=_("Unique Identifier"), help_text=_("The unique identifier for this object"))

    class Meta:
        abstract = True


class ChunkIterator(object):
    """
    Queryset wrapper to chunk queries and reduce in-memory footprint
    """
    def __init__(self, model, ids, order_by=None, select_related=None, prefetch_related=None, max_obj_num=1000):
        self._model = model
        self._ids = ids
        self._order_by = order_by
        self._select_related = select_related
        self._prefetch_related = prefetch_related
        self._generator = self._setup()
        self.max_obj_num = max_obj_num

    def _setup(self):
        for i in xrange(0, len(self._ids), self.max_obj_num):
            chunk_queryset = self._model.objects.filter(id__in=self._ids[i:i+self.max_obj_num])

            if self._order_by:
                chunk_queryset = chunk_queryset.order_by(*self._order_by)

            if self._select_related:
                chunk_queryset = chunk_queryset.select_related(*self._select_related)

            if self._prefetch_related:
                chunk_queryset = chunk_queryset.prefetch_related(*self._prefetch_related)

            for obj in chunk_queryset:
                yield obj

    def __iter__(self):
        return self

    def next(self):
        return self._generator.next()
