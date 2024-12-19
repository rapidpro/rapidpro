from gettext import gettext as _

from smartmin.views import SmartCRUDL

from django.http import HttpResponseRedirect

from temba.orgs.views.base import BaseListView, BaseReadView
from temba.utils.views.mixins import SpaMixin

from .models import Archive


class ArchiveCRUDL(SmartCRUDL):
    model = Archive
    actions = ("read", "run", "message")

    class BaseList(SpaMixin, BaseListView):
        fields = ("url", "start_date", "period", "record_count", "size")
        default_order = ("-start_date", "-period", "archive_type")
        default_template = "archives/archive_list.html"

        def derive_queryset(self, **kwargs):
            # filter by our archive type and exclude archives included in rollups
            return (
                super()
                .derive_queryset(**kwargs)
                .filter(archive_type=self.get_archive_type())
                .exclude(rollup_id__isnull=False)
            )

    class Run(BaseList):
        title = _("Run Archives")
        menu_path = "/settings/archives/run"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/$" % (path, Archive.TYPE_FLOWRUN)

        def get_archive_type(self):
            return Archive.TYPE_FLOWRUN

    class Message(BaseList):
        title = _("Message Archives")
        menu_path = "/settings/archives/message"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/$" % (path, Archive.TYPE_MSG)

        def get_archive_type(self):
            return Archive.TYPE_MSG

    class Read(BaseReadView):
        def render_to_response(self, context, **response_kwargs):
            return HttpResponseRedirect(self.object.get_download_link())
