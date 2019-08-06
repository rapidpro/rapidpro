import logging
from math import ceil

from django import forms
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponse
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from temba.contacts.models import ContactGroupCount
from temba.utils.es import ModelESSearch
from temba.utils.models import ProxyQuerySet, mapEStoDB

logger = logging.getLogger(__name__)


class PostOnlyMixin(View):
    """
    Utility mixin to make a class based view be POST only
    """

    def get(self, *args, **kwargs):
        return HttpResponse("Method Not Allowed", status=405)


class NonAtomicMixin(View):
    """
    Utility mixin to disable automatic transaction wrapping of a class based view
    """

    @transaction.non_atomic_requests
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)


class BaseActionForm(forms.Form):
    """
    Base form class for bulk actions against domain models, typically initiated from list views
    """

    model = None
    model_manager = "objects"
    label_model = None
    label_model_manager = "objects"
    has_is_active = False
    allowed_actions = ()

    def __init__(self, *args, **kwargs):
        org = kwargs.pop("org")
        self.user = kwargs.pop("user")

        super().__init__(*args, **kwargs)

        objects_qs = getattr(self.model, self.model_manager).filter(org=org)
        if self.has_is_active:
            objects_qs = objects_qs.filter(is_active=True)

        self.fields["action"] = forms.ChoiceField(choices=self.allowed_actions)
        self.fields["objects"] = forms.ModelMultipleChoiceField(objects_qs)
        self.fields["add"] = forms.BooleanField(required=False)
        self.fields["number"] = forms.BooleanField(required=False)

        if self.label_model:
            label_qs = getattr(self.label_model, self.label_model_manager).filter(org=org)
            self.fields["label"] = forms.ModelChoiceField(label_qs, required=False)

    def clean(self):
        data = self.cleaned_data
        action = data["action"]
        user_permissions = self.user.get_org_group().permissions

        update_perm_codename = self.model.__name__.lower() + "_update"

        update_allowed = user_permissions.filter(codename=update_perm_codename)
        delete_allowed = user_permissions.filter(codename="msg_update")
        resend_allowed = user_permissions.filter(codename="broadcast_send")

        if action in ("label", "unlabel", "archive", "restore", "block", "unblock", "unstop") and not update_allowed:
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == "delete" and not delete_allowed:  # pragma: needs cover
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == "resend" and not resend_allowed:  # pragma: needs cover
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == "label" and "label" not in self.cleaned_data:  # pragma: needs cover
            raise forms.ValidationError(_("Must specify a label"))

        if action == "unlabel" and "label" not in self.cleaned_data:  # pragma: needs cover
            raise forms.ValidationError(_("Must specify a label"))

        return data

    def execute(self):
        data = self.cleaned_data
        action = data["action"]
        objects = data["objects"]

        if action == "label":
            label = data["label"]
            add = data["add"]

            if not label:
                return dict(error=_("Missing label"))

            changed = self.model.apply_action_label(self.user, objects, label, add)
            return dict(changed=changed, added=add, label_id=label.id, label=label.name)

        elif action == "unlabel":
            label = data["label"]
            add = data["add"]

            if not label:
                return dict(error=_("Missing label"))

            changed = self.model.apply_action_label(self.user, objects, label, False)
            return dict(changed=changed, added=add, label_id=label.id, label=label.name)

        elif action == "archive":
            changed = self.model.apply_action_archive(self.user, objects)
            return dict(changed=changed)

        elif action == "block":
            changed = self.model.apply_action_block(self.user, objects)
            return dict(changed=changed)

        elif action == "unblock":
            changed = self.model.apply_action_unblock(self.user, objects)
            return dict(changed=changed)

        elif action == "restore":
            changed = self.model.apply_action_restore(self.user, objects)
            return dict(changed=changed)

        elif action == "delete":
            changed = self.model.apply_action_delete(self.user, objects)
            return dict(changed=changed)

        elif action == "unstop":
            changed = self.model.apply_action_unstop(self.user, objects)
            return dict(changed=changed)

        elif action == "resend":
            changed = self.model.apply_action_resend(self.user, objects)
            return dict(changed=changed)

        else:  # pragma: no cover
            return dict(error=_("Oops, so sorry. Something went wrong!"))


class ContactListPaginator(Paginator):
    """
    Paginator that knows how to work with ES dsl Search objects
    """

    ES_SEARCH_BUFFER_SIZE = 10000

    @cached_property
    def num_pages(self):

        if self.is_es_search():
            # limit maximum number of pages when searching contacts on ES
            # https://github.com/rapidpro/rapidpro/issues/876

            record_count = self.count if self.count < self.ES_SEARCH_BUFFER_SIZE else self.ES_SEARCH_BUFFER_SIZE

            return ceil(record_count / self.per_page)
        else:
            return super().num_pages

    @cached_property
    def count(self):
        if self.is_es_search():
            # execute search on the ElasticSearch to get the count
            return self.object_list.count()
        else:
            # get the group count from the ContactGroupCount squashed model
            group_instance = self.object_list._hints.get("instance")
            if group_instance:
                return ContactGroupCount.get_totals([group_instance]).get(group_instance)
            else:
                return 0

    def _get_page(self, *args, **kwargs):
        new_args = list(args)

        es_search = args[0]

        if self.is_es_search(es_search):
            # we need to execute the ES search again, to get the actual page of records
            new_object_list = args[0].execute()

            new_args[0] = new_object_list

        return super()._get_page(*new_args, **kwargs)

    def page(self, number):
        """Return a Page object for the given 1-based page number."""
        number = self.validate_number(number)
        bottom = (number - 1) * self.per_page
        top = bottom + self.per_page

        if top + self.orphans >= self.count:
            top = self.count

        # make sure to not request more than we can return, set the upper limit to the ES search buffer size
        if top >= self.ES_SEARCH_BUFFER_SIZE and self.is_es_search():
            top = self.ES_SEARCH_BUFFER_SIZE

        return self._get_page(self.object_list[bottom:top], number, self)

    def is_es_search(self, obj=None):
        if obj is None:
            return isinstance(self.object_list, ModelESSearch)
        else:
            return isinstance(obj, ModelESSearch)


class ContactListPaginationMixin(object):
    paginator_class = ContactListPaginator

    def paginate_queryset(self, queryset, page_size):
        paginator, page, new_queryset, is_paginated = super().paginate_queryset(queryset, page_size)

        if isinstance(queryset, ModelESSearch):
            model_queryset = ProxyQuerySet([obj for obj in mapEStoDB(self.model, new_queryset)])
            return paginator, page, model_queryset, is_paginated

        else:
            model_queryset = ProxyQuerySet([obj for obj in new_queryset])
            return paginator, page, model_queryset, is_paginated


class ExternalURLHandler(View):
    """
    It's useful to register Courier and Mailroom URLs in RapidPro so they can be used in templates, and if they are hit
    here, we can provide the user with a error message about
    """

    service = None

    @csrf_exempt
    def dispatch(self, request, *args, **kwargs):
        logger.error(f"URL intended for {self.service} reached RapidPro", extra={"URL": request.get_full_path()})
        return HttpResponse(f"this URL should be mapped to a {self.service} instance", status=404)


class CourierURLHandler(ExternalURLHandler):
    service = "Courier"


class MailroomURLHandler(ExternalURLHandler):
    service = "Mailroom"
