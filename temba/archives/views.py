from django.http import HttpResponseRedirect
from gettext import gettext as _
from smartmin.views import SmartCRUDL, SmartListView, SmartReadView
from temba.orgs.views import OrgPermsMixin
from .models import Archive


class ArchiveCRUDL(SmartCRUDL):

    model = Archive
    actions = ('list', 'read')
    permissions = True

    class List(OrgPermsMixin, SmartListView):
        title = _("Archive")
        fields = ('archive_type', 'archive_url', 'start_date', 'end_date', 'record_count', 'archive_size')
        default_order = ('-end_date', 'archive_type')
        search_fields = ('archive_type',)
        paginate_by = 250

        @classmethod
        def derive_url_pattern(cls, path, action):
            archive_types = (choice[0] for choice in Archive.TYPE_CHOICES)
            return r'^%s/(%s)/$' % (path, '|'.join(archive_types))

        def get_archive_type(self):
            return self.request.path.split('/')[-2]

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)

            # filter by our archive type
            return queryset.filter(archive_type=self.get_archive_type())

        def derive_title(self):
            archive_type = self.get_archive_type()
            for choice in Archive.TYPE_CHOICES:
                if (archive_type == choice[0]):
                    return f'{choice[1]} {self.title}'
            return self.title

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context['archive_types'] = Archive.TYPE_CHOICES
            context['selected'] = self.get_archive_type()
            return context

    class Read(OrgPermsMixin, SmartReadView):
        def render_to_response(self, context, **response_kwargs):
            return HttpResponseRedirect(self.get_object().get_download_link())
