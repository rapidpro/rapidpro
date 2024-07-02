from smartmin.views import SmartCRUDL, SmartListView, SmartReadView, smart_url

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.classifiers.models import Classifier
from temba.orgs.views import OrgObjPermsMixin, OrgPermsMixin
from temba.utils import str_to_bool
from temba.utils.views import ContentMenuMixin, SpaMixin

from .models import HTTPLog


class BaseObjLogsView(SpaMixin, OrgObjPermsMixin, SmartListView):
    """
    Base list view for logs associated with an object, e.g. classifier
    """

    paginate_by = 50
    permission = "request_logs.httplog_list"
    default_order = ("-created_on",)
    template_name = "request_logs/httplog_list.html"
    source_field = None
    source_url = None

    @classmethod
    def derive_url_pattern(cls, path, action):
        return r"^%s/%s/(?P<uuid>[^/]+)/$" % (path, action)

    def get_object_org(self):
        return self.source.org

    @cached_property
    def source(self):
        return get_object_or_404(self.get_source(self.kwargs["uuid"]))

    def get_source(self, uuid):  # pragma: no cover
        pass

    def get_queryset(self, **kwargs):
        return super().get_queryset(**kwargs).filter(**{self.source_field: self.source})

    def derive_select_related(self):
        return (self.source_field,)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["source"] = self.source
        context["source_url"] = smart_url(self.source_url, self.source)
        return context


class HTTPLogCRUDL(SmartCRUDL):
    model = HTTPLog
    actions = ("webhooks", "channel", "classifier", "read")

    class Webhooks(SpaMixin, ContentMenuMixin, OrgPermsMixin, SmartListView):
        default_order = ("-created_on",)
        select_related = ("flow",)
        fields = ("flow", "url", "status_code", "request_time", "created_on")
        menu_path = "/flow/history/webhooks"

        def derive_title(self):
            if str_to_bool(self.request.GET.get("error")):
                return _("Failed Webhooks")
            return _("Webhooks")

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs).filter(org=self.request.org, flow__isnull=False)
            if str_to_bool(self.request.GET.get("error")):
                qs = qs.filter(is_error=True)
            return qs

        def build_content_menu(self, menu):
            if str_to_bool(self.request.GET.get("error")):
                menu.add_link(_("All logs"), reverse("request_logs.httplog_webhooks"))
            else:
                menu.add_link(_("Errors"), f'{reverse("request_logs.httplog_webhooks")}?error=1')

    class Channel(ContentMenuMixin, BaseObjLogsView):
        source_field = "channel"
        source_url = "uuid@channels.channel_read"
        title = _("Template Fetch Logs")

        def derive_menu_path(self):
            return f"/settings/channels/{self.source.uuid}"

        def get_source(self, uuid):
            return Channel.objects.filter(uuid=uuid, is_active=True)

    class Classifier(BaseObjLogsView):
        source_field = "classifier"
        source_url = "uuid@classifiers.classifier_read"
        title = _("Classifier History")

        def derive_menu_path(self):
            return f"/settings/classifiers/{self.source.uuid}"

        def get_source(self, uuid):
            return Classifier.objects.filter(uuid=uuid, is_active=True)

    class Read(SpaMixin, OrgObjPermsMixin, SmartReadView):
        fields = ("description", "created_on")

        @property
        def permission(self):
            return "request_logs.httplog_webhooks" if self.get_object().flow else "request_logs.httplog_read"

        def derive_menu_path(self):
            log = self.get_object()
            if log.classifier:
                return f"/settings/classifiers/{log.classifier.uuid}"
            elif log.log_type == HTTPLog.WEBHOOK_CALLED:
                return "/flow/history/webhooks"
