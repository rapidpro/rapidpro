from urllib.parse import parse_qs, urlencode

from smartmin.views import SmartCreateView, SmartCRUDL, SmartFormView, SmartListView, SmartReadView, SmartTemplateView

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import RedirectView, View

from temba.apks.models import Apk
from temba.public.models import Lead, Video
from temba.utils import analytics, get_anonymous_user, json
from temba.utils.text import random_string


class IndexView(SmartTemplateView):
    template_name = "public/public_index.haml"

    def pre_process(self, request, *args, **kwargs):
        response = super().pre_process(request, *args, **kwargs)
        redirect = self.request.branding.get("redirect")
        if redirect:
            return HttpResponseRedirect(redirect)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["thanks"] = "thanks" in self.request.GET
        context["errors"] = "errors" in self.request.GET
        if context["errors"]:
            errors = parse_qs(context["url_params"][1:]).get("errors")
            if isinstance(errors, list) and len(errors) > 0:
                context["error_msg"] = errors[0]

        return context


class WelcomeRedirect(RedirectView):
    url = "/welcome"


class Deploy(SmartTemplateView):
    template_name = "public/public_deploy.haml"


class Android(SmartTemplateView):
    def render_to_response(self, context, **response_kwargs):
        pack = int(self.request.GET.get("pack", 0))
        version = self.request.GET.get("v", "")

        if not pack and not version:
            apk = Apk.objects.filter(apk_type=Apk.TYPE_RELAYER).order_by("-created_on").first()
        else:
            latest_ids = (
                Apk.objects.filter(apk_type=Apk.TYPE_MESSAGE_PACK, version=version, pack=pack)
                .order_by("-created_on")
                .only("id")
                .values_list("id", flat=True)[:10]
            )
            apk = Apk.objects.filter(id__in=latest_ids).order_by("created_on").first()

        if not apk:
            return HttpResponse("No APK found", status=404)
        else:
            return HttpResponseRedirect(apk.apk_file.url)


class Welcome(SmartTemplateView):
    template_name = "public/public_welcome.haml"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        user = self.request.user
        org = user.get_org()
        brand = self.request.branding["slug"]

        if org:
            analytics.identify(user, brand, org=org)

        return context

    def has_permission(self, request, *args, **kwargs):
        return request.user.is_authenticated


class LeadViewer(SmartCRUDL):
    actions = ("list",)
    model = Lead
    permissions = True

    class List(SmartListView):
        default_order = ("-created_on",)
        fields = ("created_on", "email")


class VideoCRUDL(SmartCRUDL):
    actions = ("create", "read", "delete", "list", "update")
    permissions = True
    model = Video

    class List(SmartListView):
        default_order = "order"
        permission = None

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            return context

    class Read(SmartReadView):
        permission = None

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["videos"] = Video.objects.exclude(pk=self.get_object().pk).order_by("order")
            return context


class LeadCRUDL(SmartCRUDL):
    actions = ("create",)
    model = Lead
    permissions = False

    class Create(SmartFormView, SmartCreateView):
        fields = ("email",)
        title = _("Register for public beta")
        success_message = ""

        @csrf_exempt
        def dispatch(self, request, *args, **kwargs):
            return super().dispatch(request, *args, **kwargs)

        def get_success_url(self):
            return reverse("orgs.org_signup") + "?%s" % urlencode({"email": self.form.cleaned_data["email"]})

        def form_invalid(self, form):
            url = reverse("public.public_index")
            email = ", ".join(form.errors["email"])

            if "from_url" in form.data:  # pragma: needs cover
                url = reverse(form.data["from_url"])

            return HttpResponseRedirect(url + "?errors=%s" % email)

        def pre_save(self, obj):
            anon = get_anonymous_user()
            obj = super().pre_save(obj)
            obj.created_by = anon
            obj.modified_by = anon
            return obj


class Blog(RedirectView):
    # whitelabels don't have blogs, so we don't use the brand domain here
    url = "http://blog." + settings.HOSTNAME


class GenerateCoupon(View):
    def post(self, *args, **kwargs):
        # return a generated coupon
        return HttpResponse(json.dumps(dict(coupon=random_string(6))))

    def get(self, *args, **kwargs):
        return self.post(*args, **kwargs)


class OrderStatus(View):
    def post(self, request, *args, **kwargs):
        if request.method == "POST":
            request_body = json.loads(request.body)
            text = request_body.get("input", dict()).get("text", "")
        else:
            text = request.GET.get("text", "")

        if text.lower() == "cu001":
            response = dict(
                status="Shipped",
                order="CU001",
                name="Ben Haggerty",
                order_number="PLAT2012",
                ship_date="October 9th",
                delivery_date="April 3rd",
                description="Vogue White Wall x 4",
            )

        elif text.lower() == "cu002":
            response = dict(
                status="Pending",
                order="CU002",
                name="Ryan Lewis",
                username="rlewis",
                ship_date="August 14th",
                order_number="FLAG13",
                description="American Flag x 1",
            )

        elif text.lower() == "cu003":
            response = dict(
                status="Cancelled",
                order="CU003",
                name="R Kelly",
                username="rkelly",
                cancel_date="December 2nd",
                order_number="SHET51",
                description="Bed Sheets, Queen x 1",
            )
        else:
            response = dict(status="Invalid")

        return HttpResponse(json.dumps(response))

    def get(self, *args, **kwargs):
        return self.post(*args, **kwargs)
