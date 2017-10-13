from __future__ import unicode_literals

import json
import urlparse

from django.conf import settings
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import RedirectView, View
from random import randint
from smartmin.views import SmartCRUDL, SmartReadView, SmartFormView, SmartCreateView, SmartListView, SmartTemplateView
from temba.public.models import Lead, Video
from temba.utils import analytics, get_anonymous_user
from temba.utils.text import random_string
from urllib import urlencode
from django import forms
from temba.utils.email import send_simple_email


###################################################################
####################     Mx Abierto     ###########################
###################################################################
""" Form to send mail of suggestion, use at index template"""
class SendSuggestionForm(forms.Form):
    text = forms.CharField(widget=forms.Textarea,required=True, max_length=640)

SUBJECT_SUGGESTION = 'Sugerencia'
RECIPIENTS_SUGGESTION = 'rapidpromexico@gmail.com'

###################################################################
#################### Mx Abierto (End)   ###########################
###################################################################

class IndexView(SmartTemplateView):
    template_name = 'public/public_index.haml'

    def get_context_data(self, **kwargs):
        context = super(IndexView, self).get_context_data(**kwargs)
        context['thanks'] = 'thanks' in self.request.GET
        context['errors'] = 'errors' in self.request.GET
        context['send_suggestion_form'] = SendSuggestionForm()
        if context['errors']:
            context['error_msg'] = urlparse.parse_qs(context['url_params'][1:])['errors'][0]

        return context

    def post(self, request, *args, **kwargs):
        context = self.get_context_data()
        form = SendSuggestionForm(self.request.POST or None)
        if form and form.is_valid():
            recipients = RECIPIENTS_SUGGESTION
            subject = SUBJECT_SUGGESTION
            message = request.POST.get('text', '')
            send_simple_email(recipients, subject, message)

        return super(IndexView, self).render_to_response(context)

class WelcomeRedirect(RedirectView):
    url = "/welcome"


class Deploy(SmartTemplateView):
    template_name = 'public/public_deploy.haml'


class Welcome(SmartTemplateView):
    template_name = 'public/public_welcome.haml'

    def get_context_data(self, **kwargs):
        context = super(Welcome, self).get_context_data(**kwargs)

        user = self.request.user
        org = user.get_org()

        if org:
            user_dict = dict(email=user.email, first_name=user.first_name,
                             last_name=user.last_name, brand=self.request.branding['slug'])
            if org:
                user_dict['org'] = org.name
                user_dict['paid'] = org.account_value()

            analytics.identify(user.username, user_dict)

        return context

    def has_permission(self, request, *args, **kwargs):
        return request.user.is_authenticated()


class Privacy(SmartTemplateView):
    template_name = 'public/public_privacy.haml'


class LeadViewer(SmartCRUDL):
    actions = ('list',)
    model = Lead
    permissions = True

    class List(SmartListView):
        default_order = ('-created_on',)
        fields = ('created_on', 'email')


class VideoCRUDL(SmartCRUDL):
    actions = ('create', 'read', 'delete', 'list', 'update')
    permissions = True
    model = Video

    class List(SmartListView):
        default_order = "order"
        permission = None

        def get_context_data(self, **kwargs):
            context = super(VideoCRUDL.List, self).get_context_data(**kwargs)
            return context

    class Read(SmartReadView):
        permission = None

        def get_context_data(self, **kwargs):
            context = super(VideoCRUDL.Read, self).get_context_data(**kwargs)
            context['videos'] = Video.objects.exclude(pk=self.get_object().pk).order_by('order')
            return context

"""
View to create new account in public_index (disable)
but maybe we might need after
class LeadCRUDL(SmartCRUDL):
    actions = ('create',)
    model = Lead
    permissions = False

    class Create(SmartFormView, SmartCreateView):
        fields = ('email',)
        title = _("Register for public beta")
        success_message = ''

        @csrf_exempt
        def dispatch(self, request, *args, **kwargs):
            return super(LeadCRUDL.Create, self).dispatch(request, *args, **kwargs)

        def get_success_url(self):
            return reverse('orgs.org_signup') + "?%s" % urlencode({'email': self.form.cleaned_data['email']})

        def form_invalid(self, form):
            url = reverse('public.public_index')
            email = ', '.join(form.errors['email'])

            if 'from_url' in form.data:  # pragma: needs cover
                url = reverse(form.data['from_url'])

            return HttpResponseRedirect(url + "?errors=%s" % email)

        def pre_save(self, obj):
            anon = get_anonymous_user()
            obj = super(LeadCRUDL.Create, self).pre_save(obj)
            obj.created_by = anon
            obj.modified_by = anon

            if self.request.user.is_anonymous():
                analytics.identify(obj.email, dict(email=obj.email, plan='None', segment=randint(1, 10),
                                                   brand=self.request.branding['slug']))
                analytics.track(obj.email, 'temba.org_lead')

            return obj
"""

class Blog(RedirectView):
    url = "http://blog." + settings.HOSTNAME


class GenerateCoupon(View):

    def post(self, *args, **kwargs):
        # return a generated coupon
        return HttpResponse(json.dumps(dict(coupon=random_string(6))))

    def get(self, *args, **kwargs):
        return self.post(*args, **kwargs)


class OrderStatus(View):

    def post(self, request, *args, **kwargs):
        text = request.GET.get('text', '')

        if text.lower() == 'cu001':
            response = dict(status="Shipped",
                            order='CU001',
                            name="Ben Haggerty",
                            order_number="PLAT2012",
                            ship_date="October 9th",
                            delivery_date="April 3rd",
                            description="Vogue White Wall x 4")

        elif text.lower() == 'cu002':
            response = dict(status="Pending",
                            order='CU002',
                            name="Ryan Lewis",
                            username="rlewis",
                            ship_date="August 14th",
                            order_number="FLAG13",
                            description="American Flag x 1")

        elif text.lower() == 'cu003':
            response = dict(status="Cancelled",
                            order='CU003',
                            name="R Kelly",
                            username="rkelly",
                            cancel_date="December 2nd",
                            order_number="SHET51",
                            description="Bed Sheets, Queen x 1")
        else:
            response = dict(status="Invalid")

        return HttpResponse(json.dumps(response))

    def get(self, *args, **kwargs):
        return self.post(*args, **kwargs)
