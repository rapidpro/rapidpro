from __future__ import unicode_literals

from collections import defaultdict

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
    def __init__(self, model, ids, order_by=None, select_related=None, prefetch_related=None,
                 contact_fields=None, max_obj_num=1000):
        self._model = model
        self._ids = ids
        self._order_by = order_by
        self._select_related = select_related
        self._prefetch_related = prefetch_related
        self._contact_fields = contact_fields
        self._generator = self._setup()
        self.max_obj_num = max_obj_num

    def _setup(self):
        from temba.values.models import Value

        for i in xrange(0, len(self._ids), self.max_obj_num):
            chunk_queryset = self._model.objects.filter(id__in=self._ids[i:i + self.max_obj_num])

            if self._order_by:
                chunk_queryset = chunk_queryset.order_by(*self._order_by)

            if self._select_related:
                chunk_queryset = chunk_queryset.select_related(*self._select_related)

            if self._prefetch_related:
                chunk_queryset = chunk_queryset.prefetch_related(*self._prefetch_related)

            if self._contact_fields:
                # get all our contact ids
                contact_ids = chunk_queryset.values_list('contact_id', flat=True)

                # fetch the contact field values
                values = Value.objects.filter(contact_field__in=self._contact_fields, contact_id__in=contact_ids)\
                                      .order_by('contact_id').prefetch_related('contact_field', 'location_value')

                # map these by contact id
                cid_to_values = defaultdict(list)
                for value in values:
                    cid_to_values[value.contact_id].append(value)

            for obj in chunk_queryset:
                # cache our contact field values on our contact object if we have any
                if self._contact_fields:
                    empty_values = set([cf.key.lower() for cf in self._contact_fields])
                    for value in cid_to_values[obj.contact_id]:
                        obj.contact.set_cached_field_value(value.contact_field.key.lower(), value)
                        empty_values.discard(value.contact_field.key.lower())

                    # set empty values for anything remaining
                    for empty in empty_values:
                        obj.contact.set_cached_field_value(empty, None)

                yield obj

    def __iter__(self):
        return self

    def next(self):
        return self._generator.next()
