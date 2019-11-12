import logging
import socket

from datetime import timedelta

from django import forms
from django.conf import settings
from django.urls import reverse
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import RedirectView
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from django.http import JsonResponse, HttpResponseRedirect

from smartmin.views import SmartCRUDL, SmartCreateView, SmartListView, SmartUpdateView, SmartReadView

from temba.utils import analytics, on_transaction_commit
from temba.utils.dates import datetime_to_ms, ms_to_datetime
from temba.utils.views import BaseActionForm
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.contacts.models import Contact

from .models import Link, ExportLinksTask
from .tasks import export_link_task

logger = logging.getLogger(__name__)


class LinkActionForm(BaseActionForm):
    allowed_actions = (("archive", _("Archive Links")),
                       ("restore", _("Restore Links")))

    model = Link
    has_is_active = True

    class Meta:
        fields = ("action", "objects", "add")


class LinkActionMixin(SmartListView):

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        user = self.request.user
        org = user.get_org()

        form = LinkActionForm(self.request.POST, org=org, user=user)
        if form.is_valid():
            form.execute().get("changed")

        response = self.get(request, *args, **kwargs)

        return response


class BaseFlowForm(forms.ModelForm):

    class Meta:
        model = Link
        fields = "__all__"


class LinkCRUDL(SmartCRUDL):
    actions = ("list", "read", "history", "archived", "create", "update", "api", "export")

    model = Link

    class OrgQuerysetMixin(object):
        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(*args, **kwargs)
            if not self.request.user.is_authenticated():  # pragma: needs cover
                return queryset.exclude(pk__gt=0)
            else:
                return queryset.filter(org=self.request.user.get_org())

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):

        class LinkCreateForm(BaseFlowForm):
            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.user = user

            class Meta:
                model = Link
                fields = ("name", "destination")
                widgets = {
                    "destination": forms.URLInput(
                        attrs={"placeholder": "E.g. http://example.com, https://example.com"}),
                }

        form_class = LinkCreateForm
        success_message = ""
        field_config = dict(name=dict(help=_("Choose a name to describe this link, e.g. Luca Survey Webflow")))
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["has_links"] = Link.objects.filter(org=self.request.user.get_org(), is_active=True).count() > 0
            return context

        def save(self, obj):
            analytics.track(self.request.user.username, "temba.link_created", dict(name=obj.name))
            org = self.request.user.get_org()
            self.object = Link.create(org=org, user=self.request.user, name=obj.name, destination=obj.destination)

        def post_save(self, obj):
            return obj

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        fields = ("name",)

        def derive_title(self):
            return self.object.name

        def get_queryset(self):
            return Link.objects.filter(is_active=True)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["recent_start"] = datetime_to_ms(timezone.now() - timedelta(minutes=5))
            return context

        def get_gear_links(self):
            links = []

            if self.has_org_perm("links.link_update"):
                links.append(dict(title=_("Edit"), style="btn-primary", js_class="update-link", href="#"))

            if self.has_org_perm("links.link_export"):
                links.append(dict(title=_("Export"), style="btn-primary", js_class="posterize",
                                  href=reverse("links.link_export", args=(self.object.pk,))))

            return links

    class History(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get_queryset(self):
            return Link.objects.filter(is_active=True)

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            link = self.get_object()

            link_creation = link.created_on - timedelta(hours=1)

            search = self.request.GET.get("search", None)

            before = int(self.request.GET.get("before", 0))
            after = int(self.request.GET.get("after", 0))

            recent_only = False
            if not before:
                recent_only = True
                before = timezone.now()
            else:
                before = ms_to_datetime(before)

            if not after:
                after = before - timedelta(days=90)
            else:
                after = ms_to_datetime(after)

            # keep looking further back until we get at least 20 items
            while True:
                activity = link.get_activity(after, before, search)
                if recent_only or len(activity) >= 20 or after == link_creation:
                    break
                else:
                    after = max(after - timedelta(days=90), link_creation)

            # mark our after as the last item in our list
            from temba.links.models import MAX_HISTORY
            if len(activity) >= MAX_HISTORY:
                after = activity[-1]["time"]

            # check if there are more pages to fetch
            context["has_older"] = False
            if not recent_only and before > link.created_on:
                context["has_older"] = bool(link.get_activity(link_creation, after, search))

            context["recent_only"] = recent_only
            context["before"] = datetime_to_ms(after)
            context["after"] = datetime_to_ms(max(after - timedelta(days=90), link_creation))
            context["activity"] = activity

            return context

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class LinkUpdateForm(BaseFlowForm):

            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.user = user

            class Meta:
                model = Link
                fields = ("name", "destination")
                widgets = {
                    "destination": forms.URLInput(
                        attrs={"placeholder": "E.g. http://example.com, https://example.com"}),
                }

        success_message = ""
        success_url = "uuid@links.link_read"
        fields = ("name", "destination")
        form_class = LinkUpdateForm

        def derive_fields(self):
            return [field for field in self.fields]

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            return obj

        def post_save(self, obj):
            return obj

    class Export(OrgObjPermsMixin, SmartReadView):
        def post(self, request, *args, **kwargs):
            user = request.user
            org = user.get_org()

            redirect = request.GET.get("redirect")

            link = Link.objects.filter(org=org, pk=self.kwargs.get("pk")).first()

            # is there already an export taking place?
            existing = ExportLinksTask.get_recent_unfinished(org)
            if existing:
                messages.info(self.request,
                              _(f"There is already an export in progress, started by {existing.created_by.username}. "
                                f"You must wait for that export to complete before starting another."))

            # otherwise, off we go
            else:
                previous_export = ExportLinksTask.objects.filter(org=org, created_by=user).order_by("-modified_on").first()
                if previous_export and previous_export.created_on < timezone.now() - timedelta(hours=24):  # pragma: needs cover
                    analytics.track(self.request.user.username, "temba.link_exported")

                export = ExportLinksTask.create(org, user, link)

                on_transaction_commit(lambda: export_link_task.delay(export.pk))

                if not getattr(settings, "CELERY_ALWAYS_EAGER", False):  # pragma: no cover
                    messages.info(self.request,
                                  _(f"We are preparing your export. We will e-mail you at {self.request.user.username} when it is ready."))

                else:
                    dl_url = reverse("assets.download", kwargs=dict(type="link_export", pk=export.pk))
                    messages.info(self.request,
                                  _(f"Export complete, you can find it here: {dl_url} (production users will get an email)"))

            return HttpResponseRedirect(redirect or reverse("links.link_read", args=[link.uuid]))

    class BaseList(LinkActionMixin, OrgQuerysetMixin, OrgPermsMixin, SmartListView):
        title = _("Trackable Links")
        refresh = 10000
        fields = ("name", "modified_on")
        default_template = "links/link_list.html"
        default_order = ("-created_on")
        search_fields = ("name__icontains",)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["org_has_links"] = Link.objects.filter(org=self.request.user.get_org(), is_active=True).count()
            context["folders"] = self.get_folders()
            context["request_url"] = self.request.path
            context["actions"] = self.actions

            return context

        def derive_queryset(self, *args, **kwargs):
            qs = super().derive_queryset(*args, **kwargs)
            return qs.exclude(is_active=False)

        def get_folders(self):
            org = self.request.user.get_org()

            return [
                dict(label="Active", url=reverse("links.link_list"),
                     count=Link.objects.filter(is_active=True, is_archived=False, org=org).count()),

                dict(label="Archived", url=reverse("links.link_archived"),
                     count=Link.objects.filter(is_active=True, is_archived=True, org=org).count())
            ]

    class Archived(BaseList):
        actions = ("restore",)
        default_order = ("-created_on",)

        def derive_queryset(self, *args, **kwargs):
            return super().derive_queryset(*args, **kwargs).filter(is_active=True, is_archived=True)

    class List(BaseList):
        title = _("Trackable Links")
        actions = ("archive",)

        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(*args, **kwargs)
            queryset = queryset.filter(is_active=True, is_archived=False)
            return queryset

    class Api(OrgQuerysetMixin, OrgPermsMixin, SmartListView):
        def get(self, request, *args, **kwargs):
            org = self.request.user.get_org()
            links = Link.objects.filter(is_active=True, is_archived=False, org=org).order_by("name")
            results = [item.as_select2() for item in links]
            return JsonResponse(dict(results=results))


class LinkHandler(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        from user_agents import parse
        from .tasks import handle_link_task

        link = Link.objects.filter(uuid=self.kwargs.get("uuid")).only("id", "clicks_count").first()
        contact = Contact.objects.filter(uuid=self.request.GET.get("contact")).only("id").first()

        if link and contact:
            x_forwarded_for = self.request.META.get("HTTP_X_FORWARDED_FOR")
            ip = x_forwarded_for.split(",")[0] if x_forwarded_for else self.request.META.get("REMOTE_ADDR")

            ua_string = self.request.META.get("HTTP_USER_AGENT")
            user_agent = parse(ua_string)

            try:
                host = socket.gethostbyaddr(ip)[0]
                is_google_checking = "google" in host
            except Exception:
                is_google_checking = False

            if not is_google_checking and not user_agent.is_bot and not contact.is_test:
                on_transaction_commit(lambda: handle_link_task.delay(link.id, contact.id))

            return link.destination
        else:
            return None
