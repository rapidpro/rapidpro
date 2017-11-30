from __future__ import print_function, unicode_literals

import six
import time

from collections import defaultdict
from django.contrib.postgres.fields import HStoreField
from django.core.exceptions import ValidationError
from django.db import models, connection
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _
from smartmin.models import SmartModel
from uuid import uuid4


def generate_uuid():
    return six.text_type(uuid4())


class TranslatableField(HStoreField):
    """
    Model field which is a set of language code and translation pairs stored as HSTORE
    """
    class Validator(object):
        def __init__(self, max_length):
            self.max_length = max_length

        def __call__(self, value):
            for lang, translation in six.iteritems(value):
                if lang != 'base' and len(lang) != 3:
                    raise ValidationError("'%s' is not a valid language code." % lang)
                if len(translation) > self.max_length:
                    raise ValidationError("Translation for '%s' exceeds the %d character limit." % (lang, self.max_length))

    def __init__(self, max_length, **kwargs):
        super(TranslatableField, self).__init__(**kwargs)

        self.max_length = max_length

    @cached_property
    def validators(self):
        return super(TranslatableField, self).validators + [TranslatableField.Validator(self.max_length)]


class TembaModel(SmartModel):

    uuid = models.CharField(max_length=36, unique=True, db_index=True, default=generate_uuid,
                            verbose_name=_("Unique Identifier"), help_text=_("The unique identifier for this object"))

    class Meta:
        abstract = True


class RequireUpdateFieldsMixin(object):

    def save(self, *args, **kwargs):
        if self.id and 'update_fields' not in kwargs:
            raise ValueError("Updating without specifying update_fields is disabled for this model")

        return super(RequireUpdateFieldsMixin, self).save(*args, **kwargs)


class SquashableModel(models.Model):
    """
    Base class for models which track counts by delta insertions which are then periodically squashed
    """
    SQUASH_OVER = None

    id = models.BigAutoField(auto_created=True, primary_key=True, verbose_name='ID')

    is_squashed = models.BooleanField(default=False, help_text=_("Whether this row was created by squashing"))

    @classmethod
    def get_unsquashed(cls):
        return cls.objects.filter(is_squashed=False)

    @classmethod
    def squash(cls):
        start = time.time()
        num_sets = 0
        for distinct_set in cls.get_unsquashed().order_by(*cls.SQUASH_OVER).distinct(*cls.SQUASH_OVER)[:5000]:
            with connection.cursor() as cursor:
                sql, params = cls.get_squash_query(distinct_set)

                cursor.execute(sql, params)

            num_sets += 1

        time_taken = time.time() - start

        print("Squashed %d distinct sets of %s in %0.3fs" % (num_sets, cls.__name__, time_taken))

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

        for i in six.moves.xrange(0, len(self._ids), self.max_obj_num):
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
