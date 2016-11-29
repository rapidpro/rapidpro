from __future__ import unicode_literals

import json

from django.forms import forms
from .models import Contact, ContactGroup, ContactURN, URN


class OmniboxWidget(forms.TextInput):

    @classmethod
    def get_objects_spec(cls, spec, user):
        org = user.get_org()

        group_uuids = []
        contact_uuids = []
        urn_ids = []
        raw_numbers = []

        item_lists = {'g': group_uuids, 'c': contact_uuids, 'u': urn_ids, 'n': raw_numbers}

        ids = spec.split(",") if spec else []
        for item_id in ids:
            item_type, item_id = item_id.split("-", 1)
            item_lists[item_type].append(item_id)

        # turn our raw numbers into new contacts with tel URNs for orgs that aren't anonymous
        if not org.is_anon:
            for number in raw_numbers:
                urn = URN.from_tel(number)
                contact = Contact.get_or_create(org, user, urns=[urn])
                urn_obj = contact.urn_objects[urn]
                urn_ids.append(urn_obj.pk)

        groups = ContactGroup.user_groups.filter(uuid__in=group_uuids, org=org)
        contacts = Contact.objects.filter(uuid__in=contact_uuids, org=org, is_active=True)
        urns = ContactURN.objects.filter(id__in=urn_ids, org=org)

        return dict(groups=groups, contacts=contacts, urns=urns)

    def set_user(self, user):
        self.__dict__['user'] = user

    def render(self, name, value, attrs=None):
        value = self.get_json(value)
        return super(OmniboxWidget, self).render(name, value, attrs)

    def get_json(self, value):

        if 'user' not in self.__dict__:  # pragma: no cover
            raise ValueError("Omnibox requires a user, make sure you set one using field.set_user(user) in your form.__init__")

        objects = OmniboxWidget.get_objects_spec(value, self.user)

        selected = []
        for group in objects['groups']:
            selected.append(dict(text=group.name, id="g-%s" % group.uuid, contacts=group.contacts.count()))

        for contact in objects['contacts']:
            selected.append(dict(text=str(contact), id="c-%s" % contact.uuid))

        return json.dumps(selected) if selected else None


class OmniboxField(forms.Field):
    default_error_messages = {}
    widget = OmniboxWidget(attrs={"class": "omni_widget", "style": "width:85%"})

    def __init__(self, **kwargs):
        super(OmniboxField, self).__init__(**kwargs)

    def set_user(self, user):
        self.user = user
        self.widget.set_user(user)

    def to_python(self, value):
        if 'user' not in self.__dict__:  # pragma: no cover
            raise ValueError("Omnibox requires a user, make sure you set one using field.set_user(user) in your form.__init__")
        return OmniboxWidget.get_objects_spec(value, self.user)
