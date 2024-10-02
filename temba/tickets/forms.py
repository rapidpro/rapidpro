from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Shortcut, Topic


class ShortcutForm(forms.ModelForm):
    def __init__(self, org, *args, **kwargs) -> None:
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


class TopicForm(forms.ModelForm):
    def __init__(self, org, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        assert not self.instance or not self.instance.is_system, "cannot edit system topic"

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

    class Meta:
        model = Topic
        fields = ("name",)
