import phonenumbers
from rest_framework import serializers

from temba.channels.models import Channel
from temba.contacts.models import URN, Contact
from temba.msgs.models import Broadcast
from temba.utils import json


def format_datetime(value):
    """
    Datetime fields are limited to millisecond accuracy for v1
    """
    return json.encode_datetime(value, micros=False) if value else None


def validate_bulk_fetch(fetched, uuids):
    """
    Validates a bulk fetch of objects against the provided list of UUIDs
    """
    if len(fetched) != len(uuids):  # pragma: no cover
        fetched_uuids = {c.uuid for c in fetched}
        invalid_uuids = [u for u in uuids if u not in fetched_uuids]
        if invalid_uuids:
            raise serializers.ValidationError("Some UUIDs are invalid: %s" % ", ".join(invalid_uuids))


# ------------------------------------------------------------------------------------------
# Field types
# ------------------------------------------------------------------------------------------


class StringArrayField(serializers.ListField):
    """
    List of strings or a single string
    """

    def __init__(self, **kwargs):
        super().__init__(child=serializers.CharField(allow_blank=False), **kwargs)

    def to_internal_value(self, data):
        # accept single string
        if isinstance(data, str):
            data = [data]

        # don't allow dicts. This is a bug in ListField due to be fixed in 3.3.2
        # https://github.com/tomchristie/django-rest-framework/pull/3513
        elif isinstance(data, dict):
            raise serializers.ValidationError("Should be a list")

        return super().to_internal_value(data)


class PhoneArrayField(serializers.ListField):
    """
    List of phone numbers or a single phone number
    """

    def to_internal_value(self, data):
        if isinstance(data, str):
            return [URN.from_tel(data)]

        elif isinstance(data, list):
            if len(data) > 100:
                raise serializers.ValidationError("You can only specify up to 100 numbers at a time.")

            urns = []
            for phone in data:
                if not isinstance(phone, str):  # pragma: no cover
                    raise serializers.ValidationError("Invalid phone: %s" % str(phone))
                urns.append(URN.from_tel(phone))

            return urns
        else:
            raise serializers.ValidationError("Invalid phone: %s" % data)


class ChannelField(serializers.PrimaryKeyRelatedField):
    def __init__(self, **kwargs):
        super().__init__(queryset=Channel.objects.filter(is_active=True), **kwargs)


# ------------------------------------------------------------------------------------------
# Serializers
# ------------------------------------------------------------------------------------------


class MsgCreateSerializer(serializers.Serializer):
    channel = ChannelField(required=False)
    text = serializers.CharField(required=True, max_length=480)
    urn = StringArrayField(required=False)
    contact = StringArrayField(required=False)
    phone = PhoneArrayField(required=False)

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.org = kwargs.pop("org") if "org" in kwargs else self.user.get_org()

        super().__init__(*args, **kwargs)

        self.instance = None

    def validate_channel(self, value):
        if value:
            # do they have permission to use this channel?
            if value.org != self.org:
                raise serializers.ValidationError("Invalid pk '%d' - object does not exist." % value.id)
        return value

    def validate_contact(self, value):
        if value:
            contacts = list(Contact.objects.filter(uuid__in=value, org=self.org, is_active=True))

            # check for UUIDs that didn't resolve to a valid contact
            validate_bulk_fetch(contacts, value)
            return contacts
        return []  # pragma: needs cover

    def validate_urn(self, value):
        urns = []
        if value:
            # if we have tel URNs, we may need a country to normalize by
            country = self.org.get_country_code()

            for urn in value:
                try:
                    normalized = URN.normalize(urn, country)
                except ValueError as e:  # pragma: needs cover
                    raise serializers.ValidationError(str(e))

                if not URN.validate(normalized, country):  # pragma: needs cover
                    raise serializers.ValidationError("Invalid URN: '%s'" % urn)
                urns.append(normalized)

        return urns

    def validate(self, data):
        urns = data.get("urn", [])
        phones = data.get("phone", [])
        contacts = data.get("contact", [])
        channel = data.get("channel")

        if (not urns and not phones and not contacts) or (urns and phones):  # pragma: needs cover
            raise serializers.ValidationError("Must provide either urns or phone or contact and not both")

        if not channel:
            channel = Channel.objects.filter(is_active=True, org=self.org).order_by("-last_seen").first()
            if not channel:  # pragma: no cover
                raise serializers.ValidationError("There are no channels for this organization.")
            data["channel"] = channel

        if phones:
            if self.org.is_anon:  # pragma: needs cover
                raise serializers.ValidationError("Cannot create messages for anonymous organizations")

            # check our numbers for validity
            country = channel.country
            for urn in phones:
                try:
                    tel, phone, query, display = URN.to_parts(urn)
                    normalized = phonenumbers.parse(phone, country.code)
                    if not phonenumbers.is_possible_number(normalized):  # pragma: needs cover
                        raise serializers.ValidationError("Invalid phone number: '%s'" % phone)
                except Exception:
                    raise serializers.ValidationError("Invalid phone number: '%s'" % phone)

        return data

    def run_validation(self, data=serializers.empty):
        if not isinstance(data, dict):
            raise serializers.ValidationError(
                detail={"non_field_errors": ["Request body should be a single JSON object"]}
            )

        return super().run_validation(data)

    def save(self):
        """
        Create a new broadcast to send out
        """
        if "urn" in self.validated_data and self.validated_data["urn"]:
            urns = self.validated_data.get("urn")
        else:
            urns = self.validated_data.get("phone", [])

        channel = self.validated_data.get("channel")
        contacts = list()
        for urn in urns:
            # treat each urn as a separate contact
            contact, urn_obj = Contact.get_or_create(channel.org, urn, user=self.user)
            contacts.append(contact)

        # add any contacts specified by uuids
        uuid_contacts = self.validated_data.get("contact", [])
        for contact in uuid_contacts:
            contacts.append(contact)

        # create the broadcast
        broadcast = Broadcast.create(
            self.org, self.user, self.validated_data["text"], contacts=contacts, channel=channel
        )

        # send it
        broadcast.send(expressions_context={})
        return broadcast
