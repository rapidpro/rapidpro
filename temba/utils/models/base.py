import logging
import types
from collections import OrderedDict
from enum import Enum

from smartmin.models import SmartModel

from django.contrib.postgres.fields import HStoreField
from django.core import checks
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import JSONField as DjangoJSONField
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from temba.utils import json
from temba.utils.fields import NameValidator
from temba.utils.uuid import is_uuid, uuid4

logger = logging.getLogger(__name__)


def generate_uuid():
    """
    Returns a random stringified UUID for use with older models that use char fields instead of UUID fields
    """
    return str(uuid4())


def patch_queryset_count(qs, function):
    """
    As of Django 2.2 a patched .count on querysets has to look like a real method
    """
    qs.count = types.MethodType(lambda s: function(), qs)


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


def mapEStoDB(model, es_queryset, only_ids=False):  # pragma: no cover
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
        else:  # pragma: no cover
            return model.objects.none()


class TranslatableField(HStoreField):
    """
    Model field which is a set of language code and translation pairs stored as HSTORE
    """

    class Validator:
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


class CheckFieldDefaultMixin:
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
            raise ValueError('Unexpected type "%s" for JSONAsTextField' % (type(value),))

    def get_db_prep_value(self, value, *args, **kwargs):
        # if the value is falsy we will save is as null
        if self.null and value in (None, {}, []) and not kwargs.get("force"):
            return None

        if value is None:
            return None

        if type(value) not in (list, dict, OrderedDict):
            raise ValueError("JSONAsTextField should be a dict or a list, got %s => %s" % (type(value), value))

        serialized = json.dumps(value)

        # strip out unicode sequences which aren't valid in JSONB
        return serialized.replace("\\u0000", "")

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
        kwargs["decoder"] = json.TembaDecoder
        super().__init__(*args, **kwargs)


class LegacyUUIDMixin(SmartModel):
    """
    Model mixin for things with an old-style VARCHAR(36) UUID
    """

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


class TembaUUIDMixin(models.Model):
    """
    Model mixin for things with a UUID
    """

    uuid = models.UUIDField(unique=True, default=uuid4)

    class Meta:
        abstract = True


class TembaNameMixin(models.Model):
    """
    Model mixin for things with a name
    """

    MAX_NAME_LEN = 64

    name = models.CharField(max_length=MAX_NAME_LEN, validators=[NameValidator(MAX_NAME_LEN)])

    @classmethod
    def get_unique_name(cls, org, base_name: str, ignore=None) -> str:
        """
        Generates a unique name from the given base name that won't conflict with any named object in the given queryset
        """
        qs = cls.objects.filter(org=org, is_active=True)
        if ignore:
            qs = qs.exclude(id=ignore.id)

        count = 1
        while True:
            count_str = f" {count}"
            name = f"{base_name[:cls.MAX_NAME_LEN - len(count_str)]}{count_str}" if count > 1 else base_name
            if not qs.filter(name__iexact=name).exists():
                return name
            count += 1

    @classmethod
    def is_valid_name(cls, value: str) -> bool:
        try:
            NameValidator(max_length=cls.MAX_NAME_LEN)(value)
            return True
        except ValidationError:
            return False

    @classmethod
    def clean_name(cls, original: str) -> str:
        """
        Cleans a name from an import to try to make it valid
        """
        return original.strip()[: cls.MAX_NAME_LEN].replace('"', "'").replace("\\", "/").replace("\0", "")

    def deleted_name(self) -> str:
        return f"deleted-{uuid4()}-{self.name}"[: self.MAX_NAME_LEN]

    class Meta:
        abstract = True


class TembaModel(TembaUUIDMixin, TembaNameMixin, SmartModel):
    """
    Base for models which have UUID, name and smartmin auditing fields
    """

    class ImportResult(Enum):
        MATCHED = 1  # import matches an existing object
        UPDATED = 2  # import matches an existing object which was updated
        CREATED = 3  # import created a new object
        IGNORED_INVALID = 4  # import ignored because it's invalid
        IGNORED_LIMIT_REACHED = 5  # import ignored because workspace has reached limit

    org_limit_key = None

    is_system = models.BooleanField(default=False)  # not user created, doesn't count against limits

    @classmethod
    def get_active_for_org(cls, org):
        return cls.objects.filter(org=org, is_active=True)

    @classmethod
    def get_org_limit_progress(cls, org) -> tuple:
        """
        Gets a tuple of the count of non-system active objects and the limit.
        """
        assert cls.org_limit_key, "org limit key not set for this class"

        return cls.get_active_for_org(org).filter(is_system=False).count(), org.get_limit(cls.org_limit_key)

    @classmethod
    def import_def(cls, org, user, definition: dict, preview: bool = False) -> tuple:
        """
        Imports an exported definition returning the new or matching object and the import result.
        """
        is_valid = cls.clean_import_def(definition)

        # an invalid definition can still match against an existing object
        match = cls.get_import_match(org, definition)

        if match:
            if match.is_system:  # we never update system objects
                return match, cls.ImportResult.MATCHED

            updates = cls.get_import_match_updates(match, definition)

            if not preview and updates:
                for attr, value in updates.items():
                    setattr(match, attr, value)
                match.save(update_fields=updates.keys())

            return match, cls.ImportResult.UPDATED if len(updates) > 0 else cls.ImportResult.MATCHED

        if not is_valid:
            return None, cls.ImportResult.IGNORED_INVALID

        if cls.org_limit_key:
            org_count, org_limit = cls.get_org_limit_progress(org)
            if org_count >= org_limit:
                return None, cls.ImportResult.IGNORED_LIMIT_REACHED

        if preview:
            return None, cls.ImportResult.CREATED

        return cls.create_from_import_def(org, user, definition), cls.ImportResult.CREATED

    @classmethod
    def clean_import_def(cls, definition: dict) -> bool:
        """
        Cleans an import definition and returns whether it was made valid or not.
        """
        definition["uuid"] = definition["uuid"] if is_uuid(definition.get("uuid", "")) else None
        definition["name"] = cls.clean_name(definition.get("name", ""))

        return bool(definition["name"])  # if we have a name we are valid

    @classmethod
    def get_import_match(cls, org, definition: dict):
        """
        Gets the existing object (if any) that matches the import definition.
        """
        if definition["uuid"]:
            existing = cls.get_active_for_org(org).filter(uuid=definition["uuid"]).first()
            if existing:
                return existing

        if definition["name"]:
            return cls.get_active_for_org(org).filter(name__iexact=definition["name"]).first()

        return None

    @classmethod
    def get_import_match_updates(cls, match, definition: dict) -> dict:
        """
        Gets the field updates we need to make to the existing matching object that matches the import definition.
        """
        updates = {}
        if definition["name"] and definition["name"] != match.name:
            updates = {"name": cls.get_unique_name(match.org, definition["name"], ignore=match)}
        return updates

    @classmethod
    def create_from_import_def(cls, org, user, definition: dict):  # pragma: no cover
        return NotImplementedError("importable classes must define this")

    def as_export_ref(self) -> dict:
        return {"uuid": str(self.uuid), "name": self.name}

    def __str__(self):
        """
        How widgets will render this object
        """
        return self.name

    def __repr__(self):
        """
        How the shell will render this object
        """
        return f'<{self.__class__.__name__}: uuid={self.uuid} name="{self.name}">'

    class Meta:
        abstract = True
