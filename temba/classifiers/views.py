from django.urls import reverse
from smartmin.views import SmartCRUDL, SmartTemplateView, SmartReadView, SmartListView, SmartFormView
from temba.orgs.views import OrgObjPermsMixin, OrgPermsMixin
from .models import Classifier, ClassifierLog
from django.utils.translation import ugettext_lazy as _
from django.shortcuts import get_object_or_404


class BaseConnectView(OrgPermsMixin, SmartFormView):
    permission = "classifiers.classifier_connect"
    classifier_type = None

    def __init__(self, classifier_type):
        self.classifier_type = classifier_type
        super().__init__()

    def get_template_names(self):
        return (
            "classifiers/types/%s/connect.html" % self.classifier_type.slug,
            "classifiers/classifier_connect_form.html",
        )

    def derive_title(self):
        return _(f"Connect {self.classifier_type.name}")

    def get_success_url(self):
        return reverse("classifiers.classifier_read", args=[self.object.uuid])


class ClassifierCRUDL(SmartCRUDL):
    model = Classifier
    actions = ("read", "list", "connect")

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        exclude = ("id", "is_active", "created_by", "modified_by", "modified_on")

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)
            return queryset.filter(org=self.request.user.get_org(), is_active=True)

    class List(OrgPermsMixin, SmartListView):
        title = _("Classifiers")

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)
            return queryset.filter(org=self.request.user.get_org(), is_active=True)

    class Connect(OrgPermsMixin, SmartTemplateView):
        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["classifier_types"] = Classifier.get_types()
            return context


class ClassifierLogCRUDL(SmartCRUDL):
    model = ClassifierLog
    actions = ("list", "read")

    class List(OrgPermsMixin, SmartListView):
        fields = ("classifier", "description", "created_on")
        link_fields = ("classifier", "description", "created_on")
        paginate_by = 50

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/(?P<classifier>[^/]+)/$" % path

        def derive_classifier(self):
            return get_object_or_404(Classifier, uuid=self.kwargs["classifier"], org=self.derive_org(), is_active=True)

        def derive_org(self):
            return self.request.user.get_org()

        def derive_queryset(self, **kwargs):
            classifier = self.derive_classifier()
            return ClassifierLog.objects.filter(classifier=classifier).order_by("-created_on").prefetch_related("classifier")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["classifier"] = self.derive_classifier()
            return context

    class Read(OrgPermsMixin, SmartReadView):
        fields = ("description", "created_on")

        def derive_org(self):
            return self.request.user.get_org()

        def derive_queryset(self, **kwargs):
            queryset = super().derive_queryset(**kwargs)
            return queryset.filter(classifier__org=self.derive_org())
