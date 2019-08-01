import time
import types
from collections import OrderedDict
from uuid import uuid4

from smartmin.models import SmartModel

from django.contrib.postgres.fields import HStoreField, JSONField as DjangoJSONField
from django.core import checks
from django.core.exceptions import ValidationError
from django.db import connection, models
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _

from temba.utils import json


def generate_uuid():
    return str(uuid4())


def patch_queryset_count(qs, function):
    """
    As of Django 2.2 a patched .count on querysets has to look like a real method
    """
    qs.count = types.MethodType(lambda s: function(), qs)


class ProxyQuerySet(object):
    """
    Helper class that mimics the behavior of a Django QuerySet

    The result is cached so we can't chain it as a normal QuerySet, but because we defined special methods that are
    expected by templates and tests we can use it as an evaluated QuerySet
    """

    def __init__(self, object_list):
        self.object_list = object_list

    def count(self):
        return len(self)

    def __iter__(self):
        return iter(self.object_list)

    def __len__(self):
        return len(self.object_list)

    def __getitem__(self, item):
        return self.object_list[item]


def mapEStoDB(model, es_queryset, only_ids=False):
    """
    Map ElasticSearch results to Django Model objects
    We use object PKs from ElasticSearch result set and select those objects in the database
    """
    pks = (result.id for result in es_queryset)

    if only_ids:
        return pks
    else:
        # prepare the data set
        pairs = ",".join(str((seq, model_id)) for seq, model_id in enumerate(pks, start=1))

        if pairs:
            return model.objects.raw(
                f"""SELECT model.*
                from {model._meta.db_table} AS model JOIN (VALUES {pairs}) tmp_resultset (seq, model_id)
                    ON model.id = tmp_resultset.model_id
                ORDER BY tmp_resultset.seq
                """
            )
        else:
            return model.objects.none()


class TranslatableField(HStoreField):
    """
    Model field which is a set of language code and translation pairs stored as HSTORE
    """

    class Validator(object):
        def __init__(self, max_length):
            self.max_length = max_length

        def __call__(self, value):
            for lang, translation in value.items():
                if lang != "base" and len(lang) != 3:
                    raise ValidationError("'%s' is not a valid language code." % lang)
                if len(translation) > self.max_length:
                    raise ValidationError(
                        "Translation for '%s' exceeds the %d character limit." % (lang, self.max_length)
                    )

    def __init__(self, max_length, **kwargs):
        super().__init__(**kwargs)

        self.max_length = max_length

    @cached_property
    def validators(self):
        return super().validators + [TranslatableField.Validator(self.max_length)]


class CheckFieldDefaultMixin(object):
    """
    This was copied from https://github.com/django/django/commit/f6e1789654e82bac08cead5a2d2a9132f6403f52

    More info: https://code.djangoproject.com/ticket/28577
    """

    _default_hint = ("<valid default>", "<invalid default>")

    def _check_default(self):
        if self.has_default() and self.default is not None and not callable(self.default):
            return [
                checks.Warning(
                    "%s default should be a callable instead of an instance so that it's not shared between all field "
                    "instances." % (self.__class__.__name__,),
                    hint="Use a callable instead, e.g., use `%s` instead of `%s`." % self._default_hint,
                    obj=self,
                    id="postgres.E003",
                )
            ]
        else:
            return []

    def check(self, **kwargs):
        errors = super().check(**kwargs)
        errors.extend(self._check_default())
        return errors


class JSONAsTextField(CheckFieldDefaultMixin, models.Field):
    """
    Custom JSON field that is stored as Text in the database

    Notes:
        * uses standard JSON serializers so it expects that all data is a valid JSON data
        * be careful with default values, it must be a callable returning a dict because using `default={}` will create
          a mutable default that is share between all instances of the JSONAsTextField
          https://docs.djangoproject.com/en/1.11/ref/contrib/postgres/fields/#jsonfield
    """

    description = "Custom JSON field that is stored as Text in the database"
    _default_hint = ("dict", "{}")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, *args, **kwargs):
        if self.has_default() and value is None:
            return self.get_default()

        if value is None:
            return value

        if isinstance(value, str):
            data = json.loads(value)

            if type(data) not in (list, dict, OrderedDict):
                raise ValueError("JSONAsTextField should be a dict or a list, got %s => %s" % (type(data), data))
            else:
                return data
        else:
            raise ValueError('Unexpected type "%s" for JSONAsTextField' % (type(value),))  # pragma: no cover

    def get_db_prep_value(self, value, *args, **kwargs):
        # if the value is falsy we will save is as null
        if self.null and value in (None, {}, []) and not kwargs.get("force"):
            return None

        if value is None:
            return None

        if type(value) not in (list, dict, OrderedDict):
            raise ValueError("JSONAsTextField should be a dict or a list, got %s => %s" % (type(value), value))

        return json.dumps(value)

    def to_python(self, value):
        if isinstance(value, str):
            value = json.loads(value)
        return value

    def db_type(self, connection):
        return "text"

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        return name, path, args, kwargs


class JSONField(DjangoJSONField):
    """
    Convenience subclass of the regular JSONField that uses our custom JSON encoder
    """

    def __init__(self, *args, **kwargs):
        kwargs["encoder"] = json.TembaEncoder
        super().__init__(*args, **kwargs)


class TembaModel(SmartModel):

    uuid = models.CharField(
        max_length=36,
        unique=True,
        db_index=True,
        default=generate_uuid,
        verbose_name=_("Unique Identifier"),
        help_text=_("The unique identifier for this object"),
    )

    class Meta:
        abstract = True


class RequireUpdateFieldsMixin(object):
    def save(self, *args, **kwargs):
        if self.id and "update_fields" not in kwargs:
            raise ValueError("Updating without specifying update_fields is disabled for this model")

        super().save(*args, **kwargs)


class SquashableModel(models.Model):
    """
    Base class for models which track counts by delta insertions which are then periodically squashed
    """

    SQUASH_OVER = None

    id = models.BigAutoField(auto_created=True, primary_key=True, verbose_name="ID")

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
