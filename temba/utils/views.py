# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms
from django.http import HttpResponse
from django.views import View
from django.utils.translation import ugettext_lazy as _


class PostOnlyMixin(View):
    """
    Utility mixin to make a class based view be POST only
    """
    def get(self, *args, **kwargs):
        return HttpResponse("Method Not Allowed", status=405)


class BaseActionForm(forms.Form):
    """
    Base form class for bulk actions against domain models, typically initiated from list views
    """
    model = None
    model_manager = 'objects'
    label_model = None
    label_model_manager = 'objects'
    has_is_active = False
    allowed_actions = ()

    def __init__(self, *args, **kwargs):
        org = kwargs.pop('org')
        self.user = kwargs.pop('user')

        super(BaseActionForm, self).__init__(*args, **kwargs)

        objects_qs = getattr(self.model, self.model_manager).filter(org=org)
        if self.has_is_active:
            objects_qs = objects_qs.filter(is_active=True)

        self.fields['action'] = forms.ChoiceField(choices=self.allowed_actions)
        self.fields['objects'] = forms.ModelMultipleChoiceField(objects_qs)
        self.fields['add'] = forms.BooleanField(required=False)
        self.fields['number'] = forms.BooleanField(required=False)

        if self.label_model:
            label_qs = getattr(self.label_model, self.label_model_manager).filter(org=org)
            self.fields['label'] = forms.ModelChoiceField(label_qs, required=False)

    def clean(self):
        data = self.cleaned_data
        action = data['action']
        user_permissions = self.user.get_org_group().permissions

        update_perm_codename = self.model.__name__.lower() + "_update"

        update_allowed = user_permissions.filter(codename=update_perm_codename)
        delete_allowed = user_permissions.filter(codename="msg_update")
        resend_allowed = user_permissions.filter(codename="broadcast_send")

        if action in ('label', 'unlabel', 'archive', 'restore', 'block', 'unblock', 'unstop') and not update_allowed:
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == 'delete' and not delete_allowed:  # pragma: needs cover
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == 'resend' and not resend_allowed:  # pragma: needs cover
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == 'label' and 'label' not in self.cleaned_data:  # pragma: needs cover
            raise forms.ValidationError(_("Must specify a label"))

        if action == 'unlabel' and 'label' not in self.cleaned_data:  # pragma: needs cover
            raise forms.ValidationError(_("Must specify a label"))

        return data

    def execute(self):
        data = self.cleaned_data
        action = data['action']
        objects = data['objects']

        if action == 'label':
            label = data['label']
            add = data['add']

            if not label:
                return dict(error=_("Missing label"))

            changed = self.model.apply_action_label(self.user, objects, label, add)
            return dict(changed=changed, added=add, label_id=label.id, label=label.name)

        elif action == 'unlabel':
            label = data['label']
            add = data['add']

            if not label:
                return dict(error=_("Missing label"))

            changed = self.model.apply_action_label(self.user, objects, label, False)
            return dict(changed=changed, added=add, label_id=label.id, label=label.name)

        elif action == 'archive':
            changed = self.model.apply_action_archive(self.user, objects)
            return dict(changed=changed)

        elif action == 'block':
            changed = self.model.apply_action_block(self.user, objects)
            return dict(changed=changed)

        elif action == 'unblock':
            changed = self.model.apply_action_unblock(self.user, objects)
            return dict(changed=changed)

        elif action == 'restore':
            changed = self.model.apply_action_restore(self.user, objects)
            return dict(changed=changed)

        elif action == 'delete':
            changed = self.model.apply_action_delete(self.user, objects)
            return dict(changed=changed)

        elif action == 'unstop':
            changed = self.model.apply_action_unstop(self.user, objects)
            return dict(changed=changed)

        elif action == 'resend':
            changed = self.model.apply_action_resend(self.user, objects)
            return dict(changed=changed)

        else:  # pragma: no cover
            return dict(error=_("Oops, so sorry. Something went wrong!"))
