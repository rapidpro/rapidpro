from rest_framework import relations, serializers

from django.db.models import Q

from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactField as ContactFieldModel, ContactGroup, ContactURN
from temba.flows.models import Flow
from temba.msgs.models import Label, Msg

# default maximum number of items in a posted list or dict
DEFAULT_MAX_LIST_ITEMS = 100
DEFAULT_MAX_DICT_ITEMS = 100


def validate_size(value, max_size):
    if hasattr(value, "__len__") and len(value) > max_size:
        raise serializers.ValidationError("This field can only contain up to %d items." % max_size)


def validate_translations(value, base_language, max_length):
    if len(value) == 0:
        raise serializers.ValidationError("Must include at least one translation.")
    if base_language not in value:
        raise serializers.ValidationError("Must include translation for base language '%s'" % base_language)

    for lang, trans in value.items():
        if not isinstance(lang, str) or (lang != "base" and len(lang) > 3):
            raise serializers.ValidationError("Language code %s is not valid." % str(lang))
        if not isinstance(trans, str):
            raise serializers.ValidationError("Translations must be strings.")
        if len(trans) > max_length:
            raise serializers.ValidationError("Ensure translations have no more than %d characters." % max_length)


def validate_urn(value, strict=True, country_code=None):
    try:
        normalized = URN.normalize(value, country_code=country_code)

        if strict and not URN.validate(normalized, country_code=country_code):
            raise ValueError()
    except ValueError:
        raise serializers.ValidationError("Invalid URN: %s. Ensure phone numbers contain country codes." % value)
    return normalized


class TranslatableField(serializers.Field):
    """
    A field which is either a simple string or a translations dict
    """

    def __init__(self, **kwargs):
        self.max_length = kwargs.pop("max_length", None)
        super().__init__(**kwargs)

    def to_representation(self, obj):
        return obj

    def to_internal_value(self, data):
        org = self.context["org"]
        base_language = org.primary_language.iso_code if org.primary_language else "base"

        if isinstance(data, str):
            if len(data) > self.max_length:
                raise serializers.ValidationError(
                    "Ensure this field has no more than %d characters." % self.max_length
                )

            data = {base_language: data}

        elif isinstance(data, dict):
            validate_translations(data, base_language, self.max_length)
        else:
            raise serializers.ValidationError("Value must be a string or dict of strings.")

        return data, base_language


class LimitedListField(serializers.ListField):
    """
    A list field which can be only be written to with a limited number of items
    """

    def to_internal_value(self, data):
        validate_size(data, DEFAULT_MAX_LIST_ITEMS)

        return super().to_internal_value(data)


class LimitedDictField(serializers.DictField):
    """
    A dict field which can be only be written to with a limited number of items
    """

    def to_internal_value(self, data):
        validate_size(data, DEFAULT_MAX_DICT_ITEMS)

        return super().to_internal_value(data)


class URNField(serializers.CharField):
    max_length = 255

    def to_representation(self, obj):
        if self.context["org"].is_anon:
            return None
        else:
            return str(obj)

    def to_internal_value(self, data):
        country_code = self.context["org"].get_country_code()
        return validate_urn(str(data), country_code=country_code)


class URNListField(LimitedListField):
    child = URNField()


class TembaModelField(serializers.RelatedField):
    model = None
    model_manager = "objects"
    lookup_fields = ("uuid",)

    # lookup fields which should be matched case-insensitively
    ignore_case_for_fields = ()

    # throw validation exception if any object not found, otherwise returns none
    require_exists = True

    class LimitedSizeList(serializers.ManyRelatedField):
        def run_validation(self, data=serializers.empty):
            validate_size(data, DEFAULT_MAX_LIST_ITEMS)

            return super().run_validation(data)

    @classmethod
    def many_init(cls, *args, **kwargs):
        """
        Overridden to provide a custom ManyRelated which limits number of items
        """
        list_kwargs = {"child_relation": cls(*args, **kwargs)}
        for key in kwargs.keys():
            if key in relations.MANY_RELATION_KWARGS:
                list_kwargs[key] = kwargs[key]
        return TembaModelField.LimitedSizeList(**list_kwargs)

    def get_queryset(self):
        manager = getattr(self.model, self.model_manager)
        return manager.filter(org=self.context["org"], is_active=True)

    def get_object(self, value):
        query = Q()
        for lookup_field in self.lookup_fields:
            ignore_case = lookup_field in self.ignore_case_for_fields
            lookup = "%s__%s" % (lookup_field, "iexact" if ignore_case else "exact")
            query |= Q(**{lookup: value})

        return self.get_queryset().filter(query).first()

    def to_representation(self, obj):
        return {"uuid": obj.uuid, "name": obj.name}

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

    def __init__(self, **kwargs):
        self.with_urn = kwargs.pop("with_urn", False)
        super().__init__(**kwargs)

    def to_representation(self, obj):
        if self.with_urn and not self.context["org"].is_anon:
            urn = obj.urns.first()
            return {"uuid": obj.uuid, "urn": urn.identity if urn else None, "name": obj.name}

        return {"uuid": obj.uuid, "name": obj.name}

    def get_queryset(self):
        return self.model.objects.filter(org=self.context["org"], is_active=True)

    def get_object(self, value):
        # try to normalize as URN but don't blow up if it's a UUID
        try:
            as_urn = URN.identity(URN.normalize(value))
        except ValueError:
            as_urn = value

        contact_ids_with_urn = list(ContactURN.objects.filter(identity=as_urn).values_list("contact_id", flat=True))

        return self.get_queryset().filter(Q(uuid=value) | Q(id__in=contact_ids_with_urn)).first()


class ContactFieldField(TembaModelField):
    model = ContactFieldModel
    lookup_fields = ("key",)

    def to_representation(self, obj):
        return {"key": obj.key, "label": obj.label}

    def get_queryset(self):
        manager = getattr(self.model, "all_fields")
        return manager.filter(org=self.context["org"], is_active=True)


class ContactGroupField(TembaModelField):
    model = ContactGroup
    model_manager = "user_groups"
    lookup_fields = ("uuid", "name")
    ignore_case_for_fields = ("name",)

    def __init__(self, **kwargs):
        self.allow_dynamic = kwargs.pop("allow_dynamic", True)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        obj = super().to_internal_value(data)

        if not self.allow_dynamic and obj.is_dynamic:
            raise serializers.ValidationError("Contact group must not be dynamic: %s" % data)

        return obj


class FlowField(TembaModelField):
    model = Flow


class LabelField(TembaModelField):
    model = Label
    model_manager = "label_objects"
    lookup_fields = ("uuid", "name")
    ignore_case_for_fields = ("name",)


class MessageField(TembaModelField):
    model = Msg
    lookup_fields = ("id",)

    # messages get archived automatically so don't error if a message doesn't exist
    require_exists = False

    def get_queryset(self):
        return self.model.objects.filter(org=self.context["org"]).exclude(visibility=Msg.VISIBILITY_DELETED)
