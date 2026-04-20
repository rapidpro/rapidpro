from rest_framework import fields, relations, serializers

from django.contrib.auth.models import User
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactField as ContactFieldModel, ContactGroup, ContactURN
from temba.flows.models import Flow
from temba.msgs.models import Attachment, Label, Media, Msg
from temba.tickets.models import Ticket, Topic
from temba.utils import languages
from temba.utils.uuid import find_uuid, is_uuid


def serialize_urn(org, urn):
    if isinstance(urn, ContactURN):
        return URN.from_parts(urn.scheme, ContactURN.ANON_MASK if org.is_anon else urn.path)
    elif isinstance(urn, dict):
        return {
            "channel": urn["channel"],
            "scheme": urn["scheme"],
            "path": ContactURN.ANON_MASK if org.is_anon else urn["path"],
            "display": urn["display"] or None,
        }


def validate_language(value):
    if not languages.get_name(str(value)):
        raise serializers.ValidationError("Not an allowed ISO 639-3 language code.")


def validate_urn(value, country_code=None):
    try:
        normalized = URN.normalize(value, country_code=country_code)

        if not URN.validate(normalized, country_code=country_code):
            raise ValueError()
    except ValueError:
        raise serializers.ValidationError("Invalid URN: %s. Ensure phone numbers contain country codes." % value)
    return normalized


class LanguageField(serializers.CharField):
    def __init__(self, **kwargs):
        super().__init__(max_length=3, **kwargs)

        self.validators.append(validate_language)


class LimitedDictField(serializers.DictField):
    """
    Adds max length validation to the standard DRF DictField
    """

    default_error_messages = {"max_length": _("Ensure this field has no more than {max_length} elements.")}

    def __init__(self, **kwargs):
        self.max_length = kwargs.pop("max_length", None)

        super().__init__(**kwargs)

        if self.max_length is not None:
            message = fields.lazy_format(self.error_messages["max_length"], max_length=self.max_length)
            self.validators.append(fields.MaxLengthValidator(self.max_length, message=message))


class LanguageDictField(LimitedDictField):
    """
    Dict field where all the keys must be valid languages
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.validators.append(self.validate_keys_as_languages)

    @staticmethod
    def validate_keys_as_languages(value):
        errors = {}
        for key in value:
            try:
                validate_language(key)
            except serializers.ValidationError as e:
                errors[key] = e.detail
        if errors:
            raise serializers.ValidationError(errors)


class TranslatedTextField(LanguageDictField):
    """
    A field which is either a string or a language -> string translations dict
    """

    def __init__(self, max_length, **kwargs):
        super().__init__(allow_empty=False, max_length=50, child=serializers.CharField(max_length=max_length), **kwargs)

    def to_internal_value(self, data):
        if isinstance(data, str):
            data = {self.context["org"].flow_languages[0]: data}

        return super().to_internal_value(data)


class TranslatedAttachmentsField(LanguageDictField):
    """
    A field which is either a list of strings or a language -> list of strings translations dict
    """

    def __init__(self, **kwargs):
        super().__init__(allow_empty=False, max_length=50, child=MediaField(many=True, max_items=10), **kwargs)

    def to_internal_value(self, data):
        if isinstance(data, list):
            data = {self.context["org"].flow_languages[0]: data}

        return super().to_internal_value(data)


class LimitedManyRelatedField(serializers.ManyRelatedField):
    """
    Adds max_length to the standard DRF ManyRelatedField
    """

    default_error_messages = {"max_length": _("Ensure this field has no more than {max_length} elements.")}

    def __init__(self, **kwargs):
        self.max_length = kwargs.pop("max_length", None)

        super().__init__(**kwargs)

    def run_validation(self, data=serializers.empty):
        if self.max_length and hasattr(data, "__len__") and len(data) > self.max_length:
            message = fields.lazy_format(self.error_messages["max_length"], max_length=self.max_length)
            raise serializers.ValidationError(message)

        return super().run_validation(data)


class URNField(serializers.CharField):
    def __init__(self, **kwargs):
        super().__init__(max_length=255, **kwargs)

    def to_representation(self, obj):
        if self.context["org"].is_anon:
            return None
        else:
            return str(obj)

    def to_internal_value(self, data):
        country_code = self.context["org"].default_country_code
        return validate_urn(str(data), country_code=country_code)


class TembaModelField(serializers.RelatedField):
    model = None
    model_manager = "objects"
    lookup_fields = ("uuid",)

    # lookup fields which should be matched case-insensitively
    ignore_case_for_fields = ()

    # throw validation exception if any object not found, otherwise returns none
    require_exists = True

    default_max_items = 100  # when many=True this is the default max number of many

    lookup_validators = {
        "uuid": is_uuid,
        "id": lambda v: isinstance(v, int),
        "name": lambda v: isinstance(v, str) and v,
    }

    @classmethod
    def many_init(cls, max_items=None, *args, **kwargs):
        """
        Overridden to provide a custom ManyRelated which limits number of items
        """

        max_items = max_items or cls.default_max_items

        list_kwargs = {"child_relation": cls(*args, **kwargs)}
        for key in kwargs.keys():
            if key in relations.MANY_RELATION_KWARGS:
                list_kwargs[key] = kwargs[key]

        return LimitedManyRelatedField(max_length=max_items, **list_kwargs)

    def get_queryset(self):
        manager = getattr(self.model, self.model_manager)
        kwargs = {"org": self.context["org"]}
        if hasattr(self.model, "is_active"):
            kwargs["is_active"] = True
        return manager.filter(**kwargs)

    def get_object(self, value):
        # ignore lookup fields that can't be queryed with the given value
        lookup_fields = []
        for lookup_field in self.lookup_fields:
            validator = self.lookup_validators.get(lookup_field)
            if not validator or validator(value):
                lookup_fields.append(lookup_field)

        # if we have no possible lookup fields left, there's no matching object
        if not lookup_fields:
            return None  # pragma: no cover

        query = Q()
        for lookup_field in lookup_fields:
            ignore_case = lookup_field in self.ignore_case_for_fields
            lookup = "%s__%s" % (lookup_field, "iexact" if ignore_case else "exact")
            query |= Q(**{lookup: value})

        return self.get_queryset().filter(query).first()

    def to_representation(self, obj):
        return {"uuid": str(obj.uuid), "name": obj.name}

    def to_internal_value(self, data):
        if not (isinstance(data, str) or isinstance(data, int)):
            raise serializers.ValidationError("Must be a string or integer")

        obj = self.get_object(data)

        if self.require_exists and not obj:
            raise serializers.ValidationError("No such object: %s" % data)

        return obj


class CampaignField(TembaModelField):
    model = Campaign

    def get_queryset(self):
        manager = getattr(self.model, self.model_manager)
        return manager.filter(org=self.context["org"], is_active=True, is_archived=False)


class CampaignEventField(TembaModelField):
    model = CampaignEvent

    def get_queryset(self):
        return self.model.objects.filter(campaign__org=self.context["org"], is_active=True)


class ChannelField(TembaModelField):
    model = Channel


class ContactField(TembaModelField):
    model = Contact
    lookup_fields = ("uuid", "urns__urn")

    def __init__(self, as_summary=False, **kwargs):
        self.as_summary = as_summary
        super().__init__(**kwargs)

    def to_representation(self, obj):
        rep = {"uuid": str(obj.uuid), "name": obj.name}
        org = self.context["org"]

        if self.as_summary:
            urn = obj.get_urn()
            if urn:
                urn_str, urn_display = serialize_urn(org, urn), obj.get_urn_display() if not org.is_anon else None
            else:
                urn_str, urn_display = None, None

            rep.update({"urn": urn_str, "urn_display": urn_display})

            if org.is_anon:
                rep["anon_display"] = obj.anon_display

        return rep

    def get_queryset(self):
        return self.model.objects.filter(org=self.context["org"], is_active=True)

    def get_object(self, value):
        # try to normalize as URN but don't blow up if it's a UUID
        try:
            as_urn = URN.identity(URN.normalize(str(value)))
        except ValueError:
            as_urn = value

        contact_ids_with_urn = list(ContactURN.objects.filter(identity=as_urn).values_list("contact_id", flat=True))

        return self.get_queryset().filter(Q(uuid=value) | Q(id__in=contact_ids_with_urn)).first()


class ContactFieldField(TembaModelField):
    model = ContactFieldModel
    lookup_fields = ("key",)

    def to_representation(self, obj):
        return {
            "key": obj.key,
            "name": obj.name,
            "label": obj.name,  # for backwards compatibility
        }


class ContactGroupField(TembaModelField):
    model = ContactGroup
    lookup_fields = ("uuid", "name")
    ignore_case_for_fields = ("name",)

    def __init__(self, allow_dynamic=True, **kwargs):
        self.allow_dynamic = allow_dynamic
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        obj = super().to_internal_value(data)

        if not self.allow_dynamic and obj.is_smart:
            raise serializers.ValidationError("Contact group must not be query based: %s" % data)

        return obj

    def get_queryset(self):
        return ContactGroup.get_groups(org=self.context["org"])


class FlowField(TembaModelField):
    model = Flow


class LabelField(TembaModelField):
    model = Label
    lookup_fields = ("uuid", "name")
    ignore_case_for_fields = ("name",)


class MediaField(TembaModelField):
    model = Media

    def _value_to_uuid(self, value) -> str | None:
        if is_uuid(value):
            return value
        try:
            att = Attachment.parse(value)  # try as a <content-type>:<url> attachment string
            return find_uuid(att.url)
        except ValueError:
            pass
        return None

    def get_object(self, value):
        uuid = self._value_to_uuid(value)
        return self.get_queryset().filter(uuid=uuid).first() if uuid else None

    def to_representation(self, obj):
        return str(obj.uuid)


class MessageField(TembaModelField):
    model = Msg
    lookup_fields = ("id",)

    # messages get archived automatically so don't error if a message doesn't exist
    require_exists = False

    def get_queryset(self):
        return self.model.objects.filter(
            org=self.context["org"], visibility__in=(Msg.VISIBILITY_VISIBLE, Msg.VISIBILITY_ARCHIVED)
        )


class TicketField(TembaModelField):
    model = Ticket


class TopicField(TembaModelField):
    model = Topic


class UserField(TembaModelField):
    model = User
    lookup_fields = ("email",)
    ignore_case_for_fields = ("email",)

    def __init__(self, assignable_only=False, **kwargs):
        self.assignable_only = assignable_only
        super().__init__(**kwargs)

    def to_representation(self, obj):
        return {"email": obj.email, "name": obj.name}

    def get_queryset(self):
        org = self.context["org"]
        if self.assignable_only:
            qs = org.get_users(with_perm=Ticket.ASSIGNEE_PERMISSION)
        else:
            qs = org.get_users()

        return qs
