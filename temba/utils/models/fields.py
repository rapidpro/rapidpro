from collections import OrderedDict

from django.contrib.postgres.fields import HStoreField
from django.core import checks
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import JSONField as DjangoJSONField
from django.utils.functional import cached_property

from temba.utils import json


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
