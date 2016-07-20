from django.utils.translation import ugettext_lazy as _

from smartmin.views import SmartListView, SmartReadView, SmartCRUDL
from temba.airtime.models import Airtime
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin


class AirtimeCRUDL(SmartCRUDL):
    model = Airtime
    actions = ('list', 'read')

    class List(OrgPermsMixin, SmartListView):
        fields = ('contact', 'status', 'channel', 'amount', 'created_on')
        title = _("Recent Airtime Transfers")
        default_order = ('-created_on',)

        def get_status(self, obj):
            return obj.get_status_display()

        def derive_queryset(self, **kwargs):
            org = self.derive_org()
            return Airtime.objects.filter(org=org)

        def get_channel(self, obj):
            if obj.channel:
                return obj.channel
            return "--"

        def get_context_data(self, **kwargs):
            context = super(AirtimeCRUDL.List, self).get_context_data(**kwargs)
            context['org'] = self.derive_org()
            return context

    class Read(OrgObjPermsMixin, SmartReadView):
        fields = ('contact', 'status', 'channel', 'amount', 'created_on')

        def get_status(self, obj):
            return obj.get_status_display()

        def derive_queryset(self, **kwargs):
            org = self.derive_org()
            return Airtime.objects.filter(org=org)

        def get_channel(self, obj):
            if obj.channel:
                return obj.channel
            return "--"
