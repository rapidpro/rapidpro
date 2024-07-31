from collections import OrderedDict

from django import forms
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from temba import mailroom
from temba.utils import languages
from temba.utils.fields import InputWidget, SelectMultipleWidget, SelectWidget, TembaMultipleChoiceField

from .models import URN, Contact, ContactGroup, ContactURN


class CreateContactForm(forms.ModelForm):
    phone = forms.CharField(required=False, max_length=64, label=_("Phone Number"), widget=InputWidget())

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org

    def clean_phone(self):
        phone = self.cleaned_data.get("phone")
        if phone:
            resolved = mailroom.get_client().contact_urns(self.org, ["tel:" + phone])[0]

            if resolved.contact_id:
                raise forms.ValidationError(_("In use by another contact."))
            if resolved.error:
                raise forms.ValidationError(_("Invalid phone number."))
            if not resolved.e164:
                raise forms.ValidationError(_("Ensure number includes country code."))

        return phone

    class Meta:
        model = Contact
        fields = ("name", "phone")
        widgets = {"name": InputWidget(attrs={"widget_only": False})}


class UpdateContactForm(forms.ModelForm):
    language = forms.ChoiceField(required=False, label=_("Language"), choices=(), widget=SelectWidget())
    groups = TembaMultipleChoiceField(
        queryset=ContactGroup.objects.none(),
        required=False,
        label=_("Groups"),
        widget=SelectMultipleWidget(attrs={"placeholder": _("Select groups for this contact"), "searchable": True}),
    )
    new_scheme = forms.ChoiceField(required=False, choices=URN.SCHEME_CHOICES)
    new_path = forms.CharField(required=False)

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org

        lang_choices = [("", "No Preference")]

        # if they had a language that has since been removed, make sure we show it
        if self.instance.language and self.instance.language not in org.flow_languages:
            lang_name = languages.get_name(self.instance.language)
            lang_choices += [(self.instance.language, _(f"{lang_name} (Missing)"))]

        lang_choices += list(languages.choices(codes=org.flow_languages))

        self.fields["language"].initial = self.instance.language
        self.fields["language"].choices = lang_choices

        self.fields["groups"].initial = self.instance.get_groups(manual_only=True)
        self.fields["groups"].queryset = ContactGroup.get_groups(org, manual_only=True).order_by(Upper("name"))

        # add all URN scheme fields if org is not anon
        if not self.org.is_anon:
            urns = self.instance.get_urns()
            if not urns:
                urns = [ContactURN(scheme="tel")]

            urn_fields = []
            last_urn = None

            for idx, urn in enumerate(urns):
                first_urn = last_urn is None or urn.scheme != last_urn.scheme

                urn_choice = None
                for choice in URN.SCHEME_CHOICES:
                    if choice[0] == urn.scheme:
                        urn_choice = choice

                scheme = urn.scheme
                label = urn.scheme

                if urn_choice:
                    label = urn_choice[1]

                help_text = _(f"{label} for this contact")
                if first_urn:
                    help_text = _(f"{label} for this contact") + f" (@urns.{scheme})"

                # get all the urns for this scheme
                ctrl = forms.CharField(
                    required=False, label=label, initial=urn.path, help_text=help_text, widget=InputWidget()
                )
                urn_fields.append((f"urn__{scheme}__{idx}", ctrl))

                last_urn = urn

            self.fields.update(OrderedDict(urn_fields))

    def clean(self):
        # gather up all URN values that need validated
        urns_by_field = {}
        for field, value in self.data.items():
            if field.startswith("urn__") and value:
                scheme = field.split("__")[1]
                urns_by_field[field] = URN.from_parts(scheme, value)

        if self.data.get("new_path"):
            urns_by_field["new_path"] = URN.from_parts(self.data["new_scheme"], self.data["new_path"])

        # let mailroom figure out which are valid or taken
        if urns_by_field:
            urn_values = list(urns_by_field.values())
            resolved = mailroom.get_client().contact_urns(self.org, urn_values)
            resolved_by_value = dict(zip(urn_values, resolved))

            for field, urn in urns_by_field.items():
                resolved = resolved_by_value[urn]
                scheme, path, query, display = URN.to_parts(resolved.normalized)

                if resolved.contact_id and resolved.contact_id != self.instance.id:
                    self.add_error(field, _("In use by another contact."))
                elif resolved.error:
                    self.add_error(field, _("Invalid format."))
                elif scheme == URN.TEL_SCHEME and field == "new_path" and not resolved.e164:
                    # if a new phone numer is being added, it must have country code
                    self.add_error(field, _("Invalid phone number. Ensure number includes country code."))

                self.cleaned_data[field] = path

        return self.cleaned_data

    class Meta:
        model = Contact
        labels = {"name": _("Name"), "status": _("Status")}
        fields = ("name", "status", "language", "groups")


class ContactGroupForm(forms.ModelForm):
    preselected_contacts = forms.CharField(required=False, widget=forms.HiddenInput)
    group_query = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org

    def clean_name(self):
        name = self.cleaned_data["name"]

        # make sure the name isn't already taken
        existing = self.org.groups.filter(is_active=True, name__iexact=name).first()
        if existing and self.instance != existing:
            raise forms.ValidationError(_("Already used by another group."))

        count, limit = ContactGroup.get_org_limit_progress(self.org)
        if limit is not None and count >= limit:
            raise forms.ValidationError(
                _(
                    "This workspace has reached its limit of %(limit)d groups. "
                    "You must delete existing ones before you can create new ones."
                ),
                params={"limit": limit},
            )

        return name

    def clean_query(self):
        try:
            parsed = mailroom.get_client().contact_parse_query(self.org, self.cleaned_data["query"])
            if not parsed.metadata.allow_as_group:
                raise forms.ValidationError(_('You cannot create a smart group based on "id" or "group".'))

            if (
                self.instance
                and self.instance.status != ContactGroup.STATUS_READY
                and parsed.query != self.instance.query
            ):
                raise forms.ValidationError(_("You cannot update the query of a group that is evaluating."))

            return parsed.query

        except mailroom.QueryValidationException as e:
            raise forms.ValidationError(str(e))

    class Meta:
        model = ContactGroup
        fields = ("name", "query")
        labels = {"name": _("Name"), "query": _("Query")}
        help_texts = {"query": _("Only contacts matching this query will belong to this group.")}
