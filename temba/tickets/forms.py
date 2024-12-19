from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Shortcut, Team, Topic


class ShortcutForm(forms.ModelForm):
    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org

    def clean_name(self):
        name = self.cleaned_data["name"]

        # make sure the name isn't already taken
        conflicts = self.org.shortcuts.filter(name__iexact=name)
        if self.instance:
            conflicts = conflicts.exclude(id=self.instance.id)

        if conflicts.exists():
            raise forms.ValidationError(_("Shortcut with this name already exists."))

        return name

    class Meta:
        model = Shortcut
        fields = ("name", "text")


class TeamForm(forms.ModelForm):
    topics = forms.ModelMultipleChoiceField(queryset=Topic.objects.none(), required=False)

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org
        self.fields["topics"].queryset = org.topics.filter(is_active=True)

    def clean_name(self):
        name = self.cleaned_data["name"]

        # make sure the name isn't already taken
        conflicts = self.org.teams.filter(name__iexact=name)
        if self.instance:
            conflicts = conflicts.exclude(id=self.instance.id)

        if conflicts.exists():
            raise forms.ValidationError(_("Team with this name already exists."))

        return name

    def clean_topics(self):
        topics = self.cleaned_data["topics"]
        if len(topics) > Team.max_topics:
            raise forms.ValidationError(
                _("Teams can have at most %(limit)d topics."), params={"limit": Team.max_topics}
            )
        return topics

    def clean(self):
        cleaned_data = super().clean()

        count, limit = Team.get_org_limit_progress(self.org)
        if limit is not None and count >= limit:
            raise forms.ValidationError(
                _(
                    "This workspace has reached its limit of %(limit)d teams. "
                    "You must delete existing ones before you can create new ones."
                ),
                params={"limit": limit},
            )

        return cleaned_data

    class Meta:
        model = Team
        fields = ("name", "topics")


class TopicForm(forms.ModelForm):
    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org

    def clean_name(self):
        name = self.cleaned_data["name"]

        # make sure the name isn't already taken
        conflicts = self.org.topics.filter(name__iexact=name)
        if self.instance:
            conflicts = conflicts.exclude(id=self.instance.id)

        if conflicts.exists():
            raise forms.ValidationError(_("Topic with this name already exists."))

        return name

    def clean(self):
        cleaned_data = super().clean()

        count, limit = Topic.get_org_limit_progress(self.org)
        if limit is not None and count >= limit:
            raise forms.ValidationError(
                _(
                    "This workspace has reached its limit of %(limit)d topics. "
                    "You must delete existing ones before you can create new ones."
                ),
                params={"limit": limit},
            )

        return cleaned_data

    class Meta:
        model = Topic
        fields = ("name",)
