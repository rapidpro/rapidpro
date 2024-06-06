from collections import OrderedDict

from django import forms
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from temba import mailroom
from temba.utils import languages
from temba.utils.fields import InputWidget, SelectMultipleWidget, SelectWidget, TembaMultipleChoiceField

from .models import URN, Contact, ContactGroup, ContactURN
from .search import parse_query


class UpdateContactForm(forms.ModelForm):
    language = forms.ChoiceField(required=False, label=_("Language"), choices=(), widget=SelectWidget())
    groups = TembaMultipleChoiceField(
        queryset=ContactGroup.objects.none(),
        required=False,
        label=_("Groups"),
        widget=SelectMultipleWidget(attrs={"placeholder": _("Select groups for this contact"), "searchable": True}),
    )

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
        country = self.org.default_country_code

        def validate_urn(key, scheme, path):
            try:
                normalized = URN.normalize(URN.from_parts(scheme, path), country)
                existing_urn = ContactURN.lookup(self.org, normalized, normalize=False)

                if existing_urn and existing_urn.contact and existing_urn.contact != self.instance:
                    self._errors[key] = self.error_class([_("Used by another contact")])
                    return False
                # validate but not with country as users are allowed to enter numbers before adding a channel
                elif not URN.validate(normalized):
                    if scheme == URN.TEL_SCHEME:  # pragma: needs cover
                        self._errors[key] = self.error_class(
                            [_("Invalid number. Ensure number includes country code, e.g. +1-541-754-3010")]
                        )
                    else:
                        self._errors[key] = self.error_class([_("Invalid format")])
                    return False
                return True
            except ValueError:
                self._errors[key] = self.error_class([_("Invalid input")])
                return False

        # validate URN fields
        for field_key, value in self.data.items():
            if field_key.startswith("urn__") and value:
                scheme = field_key.split("__")[1]
                validate_urn(field_key, scheme, value)

        # validate new URN if provided
        if self.data.get("new_path", None):
            if validate_urn("new_path", self.data["new_scheme"], self.data["new_path"]):
                self.cleaned_data["new_scheme"] = self.data["new_scheme"]
                self.cleaned_data["new_path"] = self.data["new_path"]

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
            parsed = parse_query(self.org, self.cleaned_data["query"])
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
