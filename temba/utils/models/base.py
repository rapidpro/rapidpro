import types
from enum import Enum

from smartmin.models import SmartModel

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from temba.utils.fields import NameValidator
from temba.utils.uuid import is_uuid, uuid4


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


def delete_in_batches(qs, *, batch_size: int = 1000, pk: str = "id", pre_delete=None, post_delete=None) -> int:
    """
    Deletes objects from the given queryset in batches returning the number deleted. Callback functions can be provided
    as `pre_delete` and `post_delete` which will be called pre and post batch deletion respectively. If `post_delete`
    returns falsey then batch processing stops.
    """

    num_deleted = 0

    while True:
        pk_batch = list(qs.values_list(pk, flat=True)[:batch_size])
        if not pk_batch:
            break

        if pre_delete:
            pre_delete(pk_batch)

        qs.model.objects.filter(**{f"{pk}__in": pk_batch}).delete()
        num_deleted += len(pk_batch)

        if post_delete and not post_delete():
            break

    return num_deleted


def update_if_changed(obj, **kwargs) -> bool:
    """
    Updates the given model instance with the given values, saving it if a change was made.
    """
    update_fields = []
    for attr, value in kwargs.items():
        if getattr(obj, attr) != value:
            setattr(obj, attr, value)
            update_fields.append(attr)

    if update_fields:
        obj.save(update_fields=update_fields)

    return bool(update_fields)


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
        return original.strip()[: cls.MAX_NAME_LEN].strip().replace('"', "'").replace("\\", "/").replace("\0", "")

    def _deleted_name(self) -> str:
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
        return cls._default_manager.filter(org=org, is_active=True)

    @classmethod
    def get_org_limit_progress(cls, org) -> tuple:
        """
        Gets a tuple of the count of non-system active objects and the limit. A limit of None means unlimited.
        """
        limit = org.get_limit(cls.org_limit_key) if cls.org_limit_key else None

        return cls.get_active_for_org(org).filter(is_system=False).count(), limit

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

        org_count, org_limit = cls.get_org_limit_progress(org)
        if org_limit is not None and org_count >= org_limit:
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
        raise NotImplementedError("importable classes must define this")

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
        return f'<{self.__class__.__name__}: id={self.id} name="{self.name}">'

    class Meta:
        abstract = True
