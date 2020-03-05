from gettext import gettext as _

from smartmin.views import SmartCRUDL, SmartListView, SmartReadView

from django.db.models import Sum
from django.http import HttpResponseRedirect

from temba.orgs.views import OrgObjPermsMixin, OrgPermsMixin

from .models import Archive


class ArchiveCRUDL(SmartCRUDL):

    model = Archive
    actions = ("read", "run", "message")
    permissions = True

    class List(OrgPermsMixin, SmartListView):
        title = _("Archive")
        fields = ("url", "start_date", "period", "record_count", "size")
        default_order = ("-start_date", "-period", "archive_type")
        paginate_by = 250

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)

            # filter by our archive type
            return queryset.filter(org=self.org, archive_type=self.get_archive_type()).exclude(rollup_id__isnull=False)

        def derive_title(self):
            archive_type = self.get_archive_type()
            for choice in Archive.TYPE_CHOICES:
                if archive_type == choice[0]:
                    return f"{choice[1]} {self.title}"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            if "HTTP_X_FORMAX" in self.request.META:  # no additional data needed if request is only for formax
                context["archive_count"] = Archive.objects.filter(org=self.org, rollup=None).count()
                context["record_count"] = (
                    Archive.objects.filter(org=self.org, rollup=None)
                    .aggregate(Sum("record_count"))
                    .get("record_count__sum", 0)
                )

            else:
                context["archive_types"] = Archive.TYPE_CHOICES
                context["selected"] = self.get_archive_type()

            return context

    class Run(List):
        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/$" % (path, Archive.TYPE_FLOWRUN)

        def get_archive_type(self):
            return Archive.TYPE_FLOWRUN

    class Message(List):
        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/$" % (path, Archive.TYPE_MSG)

        def get_archive_type(self):
            return Archive.TYPE_MSG

    class Read(OrgObjPermsMixin, SmartReadView):
        def render_to_response(self, context, **response_kwargs):
            return HttpResponseRedirect(self.get_object().get_download_link())
