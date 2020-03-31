from smartmin.views import SmartCRUDL, SmartListView, SmartReadView

from django.shortcuts import get_object_or_404

from temba.classifiers.models import Classifier
from temba.orgs.views import OrgObjPermsMixin, OrgPermsMixin

from .models import HTTPLog


class HTTPLogCRUDL(SmartCRUDL):
    model = HTTPLog
    actions = ("list", "read")

    class List(OrgPermsMixin, SmartListView):
        paginate_by = 50

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/(?P<log_type>classifier)/(?P<uuid>[^/]+)/$" % path

        def derive_classifier(self):
            return get_object_or_404(Classifier, uuid=self.kwargs["uuid"], org=self.derive_org(), is_active=True)

        def derive_queryset(self, **kwargs):
            # will need to be customized for other types once we support them
            classifier = self.derive_classifier()
            return HTTPLog.objects.filter(classifier=classifier).order_by("-created_on").prefetch_related("classifier")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["classifier"] = self.derive_classifier()
            return context

    class Read(OrgObjPermsMixin, SmartReadView):
        fields = ("description", "created_on")
