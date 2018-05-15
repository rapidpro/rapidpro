from gettext import gettext as _
from smartmin.views import SmartCRUDL, SmartListView
from temba.orgs.views import OrgPermsMixin
from .models import Archive


class ArchiveCRUDL(SmartCRUDL):

    model = Archive
    actions = ('list',)
    permissions = True

    class List(OrgPermsMixin, SmartListView):
        title = _("Archive")
        fields = ('archive_type', 'archive_url', 'start_date', 'end_date', 'record_count', 'archive_size')
        default_order = ('-end_date', 'archive_type')
        search_fields = ('archive_type',)
        paginate_by = 250

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)
            # org users see archives for their org, superuser sees all
            if not self.request.user.is_superuser:
                org = self.request.user.get_org()
                queryset = queryset.filter(org=org)
            return queryset

        def derive_title(self):
            archive_type = self.request.GET.get('search', Archive.TYPE_MSG)
            for choice in Archive.TYPE_CHOICES:
                if (archive_type == choice[0]):
                    return f'{choice[1]} {self.title}'
            return self.title

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context['archive_types'] = Archive.TYPE_CHOICES
            context['selected'] = self.request.GET.get('search', Archive.TYPE_MSG)
            return context
