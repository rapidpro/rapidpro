from __future__ import absolute_import, unicode_literals

import json
import logging
import plivo
import six

from collections import OrderedDict
from datetime import datetime
from decimal import Decimal
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.core.validators import validate_email
from django.db import IntegrityError
from django.db.models import Sum
from django.forms import Form
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.http import urlquote
from django.utils.text import slugify
from django.utils.translation import ugettext_lazy as _
from django.views.generic import View
from operator import attrgetter
from smartmin.views import SmartCRUDL, SmartCreateView, SmartFormView, SmartReadView, SmartUpdateView, SmartListView, SmartTemplateView
from datetime import timedelta
from temba.api.models import APIToken
from temba.assets.models import AssetType
from temba.channels.models import Channel, PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN
from temba.formax import FormaxMixin
from temba.middleware import BrandingMiddleware
from temba.nexmo import NexmoClient, NexmoValidationError
from temba.orgs.models import get_stripe_credentials, NEXMO_UUID, NEXMO_SECRET, NEXMO_KEY
from temba.utils import analytics, build_json_response, languages
from temba.utils.middleware import disable_middleware
from timezones.forms import TimeZoneField
from twilio.rest import TwilioRestClient
from .bundles import WELCOME_TOPUP_SIZE
from .models import Org, OrgCache, OrgEvent, TopUp, Invitation, UserSettings
from .models import MT_SMS_EVENTS, MO_SMS_EVENTS, MT_CALL_EVENTS, MO_CALL_EVENTS, ALARM_EVENTS
from .models import SUSPENDED, WHITELISTED, RESTORED


def check_login(request):
    """
    Simple view that checks whether we actually need to log in.  This is needed on the live site
    because we serve the main page as http:// but the logged in pages as https:// and only store
    the cookies on the SSL connection.  This view will be called in https:// land where we will
    check whether we are logged in, if so then we will redirect to the LOGIN_URL, otherwise we take
    them to the normal user login page
    """
    if request.user.is_authenticated():
        return HttpResponseRedirect(settings.LOGIN_REDIRECT_URL)
    else:
        return HttpResponseRedirect(settings.LOGIN_URL)


class OrgPermsMixin(object):
    """
    Get the organisation and the user within the inheriting view so that it be come easy to decide
    whether this user has a certain permission for that particular organization to perform the view's actions
    """
    def get_user(self):
        return self.request.user

    def derive_org(self):
        org = None
        if not self.get_user().is_anonymous():
            org = self.get_user().get_org()
        return org

    def pre_process(self, request, *args, **kwargs):
        user = self.get_user()
        org = self.derive_org()

        if not org:
            if user.is_authenticated():
                if user.is_superuser or user.is_staff:
                    return None

                return HttpResponseRedirect(reverse('orgs.org_choose'))
            else:
                return HttpResponseRedirect(settings.LOGIN_URL)

        return None

    def has_org_perm(self, permission):
        if self.org:
            return self.get_user().has_org_perm(self.org, permission)

        return False

    def has_permission(self, request, *args, **kwargs):
        """
        Figures out if the current user has permissions for this view.
        """
        self.kwargs = kwargs
        self.args = args
        self.request = request
        self.org = self.derive_org()

        if self.get_user().is_superuser:
            return True

        if self.get_user().is_anonymous():
            return False

        if self.get_user().has_perm(self.permission):
            return True

        return self.has_org_perm(self.permission)


class OrgObjPermsMixin(OrgPermsMixin):

    def get_object_org(self):
        return self.get_object().org

    def has_org_perm(self, codename):
        has_org_perm = super(OrgObjPermsMixin, self).has_org_perm(codename)

        if has_org_perm:
            user = self.get_user()
            return user.get_org() == self.get_object_org()

        return False

    def has_permission(self, request, *args, **kwargs):
        has_perm = super(OrgObjPermsMixin, self).has_permission(request, *args, **kwargs)

        if has_perm:
            user = self.get_user()

            # user has global permission
            if user.has_perm(self.permission):
                return True

            return user.get_org() == self.get_object_org()

        return False


class ModalMixin(SmartFormView):

    def get_context_data(self, **kwargs):
        context = super(ModalMixin, self).get_context_data(**kwargs)

        if 'HTTP_X_PJAX' in self.request.META and 'HTTP_X_FORMAX' not in self.request.META:  # pragma: no cover
            context['base_template'] = "smartmin/modal.html"
        if 'success_url' in kwargs:  # pragma: no cover
            context['success_url'] = kwargs['success_url']

        pairs = [urlquote(k) + "=" + urlquote(v) for k, v in six.iteritems(self.request.REQUEST) if k != '_']
        context['action_url'] = self.request.path + "?" + ("&".join(pairs))

        return context

    def form_valid(self, form):

        self.object = form.save(commit=False)

        try:
            self.object = self.pre_save(self.object)
            self.save(self.object)
            self.object = self.post_save(self.object)

            messages.success(self.request, self.derive_success_message())

            if 'HTTP_X_PJAX' not in self.request.META:
                return HttpResponseRedirect(self.get_success_url())
            else:  # pragma: no cover
                response = self.render_to_response(self.get_context_data(form=form,
                                                                         success_url=self.get_success_url(),
                                                                         success_script=getattr(self, 'success_script', None)))
                response['Temba-Success'] = self.get_success_url()
                return response

        except IntegrityError as e:  # pragma: no cover
            message = str(e).capitalize()
            errors = self.form._errors.setdefault(forms.forms.NON_FIELD_ERRORS, forms.utils.ErrorList())
            errors.append(message)
            return self.render_to_response(self.get_context_data(form=form))


class OrgSignupForm(forms.ModelForm):
    """
    Signup for new organizations
    """
    first_name = forms.CharField(help_text=_("Your first name"))
    last_name = forms.CharField(help_text=_("Your last name"))
    email = forms.EmailField(help_text=_("Your email address"))
    timezone = TimeZoneField(help_text=_("The timezone your organization is in"))
    password = forms.CharField(widget=forms.PasswordInput,
                               help_text=_("Your password, at least eight letters please"))
    name = forms.CharField(label=_("Organization"),
                           help_text=_("The name of your organization"))

    def __init__(self, *args, **kwargs):
        if 'branding' in kwargs:
            del kwargs['branding']

        super(OrgSignupForm, self).__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data['email']
        if email:
            if User.objects.filter(username__iexact=email):
                raise forms.ValidationError(_("That email address is already used"))

        return email.lower()

    def clean_password(self):
        password = self.cleaned_data['password']
        if password:
            if not len(password) >= 8:
                raise forms.ValidationError(_("Passwords must contain at least 8 letters."))
        return password

    class Meta:
        model = Org
        fields = '__all__'


class OrgGrantForm(forms.ModelForm):
    first_name = forms.CharField(help_text=_("The first name of the organization administrator"))
    last_name = forms.CharField(help_text=_("Your last name of the organization administrator"))
    email = forms.EmailField(help_text=_("Their email address"))
    timezone = TimeZoneField(help_text=_("The timezone the organization is in"))
    password = forms.CharField(widget=forms.PasswordInput, required=False,
                               help_text=_("Their password, at least eight letters please. (leave blank for existing users)"))
    name = forms.CharField(label=_("Organization"),
                           help_text=_("The name of the new organization"))
    credits = forms.ChoiceField([], help_text=_("The initial number of credits granted to this organization."))

    def __init__(self, *args, **kwargs):
        branding = kwargs['branding']
        del kwargs['branding']

        super(OrgGrantForm, self).__init__(*args, **kwargs)

        welcome_packs = branding['welcome_packs']

        choices = []
        for pack in welcome_packs:
            choices.append((str(pack['size']), "%d - %s" % (pack['size'], pack['name'])))

        self.fields['credits'].choices = choices

    def clean(self):
        data = self.cleaned_data

        email = data.get('email', None)
        password = data.get('password', None)

        # for granting new accounts, either the email maps to an existing user (and their existing password is used)
        # or both email and password must be included
        if email:
            user = User.objects.filter(username__iexact=email).first()
            if user:
                if password:
                    raise ValidationError(_("User already exists, please do not include password."))

            elif not password or len(password) < 8:
                raise ValidationError(_("Password must be at least 8 characters long"))

        return data

    class Meta:
        model = Org
        fields = '__all__'


class UserCRUDL(SmartCRUDL):
    model = User
    actions = ('edit',)

    class Edit(SmartUpdateView):
        class EditForm(forms.ModelForm):
            first_name = forms.CharField(label=_("Your First Name (required)"))
            last_name = forms.CharField(label=_("Your Last Name (required)"))
            email = forms.EmailField(required=True, label=_("Email"))
            current_password = forms.CharField(label=_("Current Password (required)"), widget=forms.PasswordInput)
            new_password = forms.CharField(required=False, label=_("New Password (optional)"), widget=forms.PasswordInput)
            language = forms.ChoiceField(choices=settings.LANGUAGES, required=True, label=_("Website Language"))

            def clean_new_password(self):
                password = self.cleaned_data['new_password']
                if password and not len(password) >= 8:
                    raise forms.ValidationError(_("Passwords must have at least 8 letters."))
                return password

            def clean_current_password(self):
                user = self.instance
                password = self.cleaned_data.get('current_password', None)

                if not user.check_password(password):
                    raise forms.ValidationError(_("Please enter your password to save changes."))

                return password

            def clean_email(self):
                user = self.instance
                email = self.cleaned_data['email'].lower()

                if User.objects.filter(username=email).exclude(pk=user.pk):
                    raise forms.ValidationError(_("Sorry, that email address is already taken."))

                return email

            class Meta:
                model = User
                fields = ('first_name', 'last_name', 'email', 'current_password', 'new_password', 'language')

        form_class = EditForm
        permission = 'orgs.org_profile'
        success_url = '@orgs.org_home'
        success_message = ''

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/$' % (path, action)

        def get_object(self, *args, **kwargs):
            return self.request.user

        def derive_initial(self):
            initial = super(UserCRUDL.Edit, self).derive_initial()
            initial['language'] = self.get_object().get_settings().language
            return initial

        def pre_save(self, obj):
            obj = super(UserCRUDL.Edit, self).pre_save(obj)

            # keep our username and email in sync
            obj.username = obj.email

            if self.form.cleaned_data['new_password']:
                obj.set_password(self.form.cleaned_data['new_password'])

            return obj

        def post_save(self, obj):
            # save the user settings as well
            obj = super(UserCRUDL.Edit, self).post_save(obj)
            user_settings = obj.get_settings()
            user_settings.language = self.form.cleaned_data['language']
            user_settings.save()
            return obj

        def has_permission(self, request, *args, **kwargs):
            user = self.request.user

            if user.is_anonymous():
                return False

            org = user.get_org()

            if org:
                org_users = org.administrators.all() | org.editors.all() | org.viewers.all() | org.surveyors.all()

                if not user.is_authenticated():
                    return False

                if user in org_users:
                    return True

            return False


class InferOrgMixin(object):
    @classmethod
    def derive_url_pattern(cls, path, action):
        return r'^%s/%s/$' % (path, action)

    def get_object(self, *args, **kwargs):
        return self.request.user.get_org()


class PhoneRequiredForm(forms.ModelForm):
    tel = forms.CharField(max_length=15, label="Phone Number", required=True)

    def clean_tel(self):
        if 'tel' in self.cleaned_data:
            tel = self.cleaned_data['tel']
            if not tel:
                return tel

            import phonenumbers
            try:
                normalized = phonenumbers.parse(tel, None)
                if not phonenumbers.is_possible_number(normalized):
                    raise forms.ValidationError(_("Invalid phone number, try again."))
            except Exception:  # pragma: no cover
                raise forms.ValidationError(_("Invalid phone number, try again."))
            return phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.E164)

    class Meta:
        model = UserSettings
        fields = ('tel',)


class UserSettingsCRUDL(SmartCRUDL):
    actions = ('update', 'phone')
    model = UserSettings

    class Phone(ModalMixin, OrgPermsMixin, SmartUpdateView):

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/$' % (path, action)

        def get_object(self, *args, **kwargs):
            return self.request.user.get_settings()

        fields = ('tel',)
        form_class = PhoneRequiredForm
        submit_button_name = _("Start Call")
        success_url = '@orgs.usersettings_phone'


class OrgCRUDL(SmartCRUDL):
    actions = ('signup', 'home', 'webhook', 'edit', 'join', 'grant', 'create_login', 'choose',
               'manage_accounts', 'manage', 'update', 'country', 'languages', 'clear_cache', 'download',
               'twilio_connect', 'twilio_account', 'nexmo_configuration', 'nexmo_account', 'nexmo_connect', 'export',
               'import', 'plivo_connect', 'service', 'surveyor')

    model = Org

    class Import(InferOrgMixin, OrgPermsMixin, SmartFormView):

        class FlowImportForm(Form):
            import_file = forms.FileField(help_text=_('The import file'))
            update = forms.BooleanField(help_text=_('Update all flows and campaigns'), required=False)

            def __init__(self, *args, **kwargs):
                self.org = kwargs['org']
                del kwargs['org']
                super(OrgCRUDL.Import.FlowImportForm, self).__init__(*args, **kwargs)

            def clean_import_file(self):
                from temba.orgs.models import EARLIEST_IMPORT_VERSION

                # make sure they have purchased credits
                if not self.org.has_added_credits():
                    raise ValidationError("Sorry, import is a premium feature")

                # check that it isn't too old
                data = self.cleaned_data['import_file'].read()
                json_data = json.loads(data)
                if json_data.get('version', 0) < EARLIEST_IMPORT_VERSION:
                    raise ValidationError('This file is no longer valid. Please export a new version and try again.')

                return data

        success_message = _("Import successful")
        form_class = FlowImportForm

        def get_success_url(self):
            return reverse('orgs.org_home')

        def get_form_kwargs(self):
            kwargs = super(OrgCRUDL.Import, self).get_form_kwargs()
            kwargs['org'] = self.request.user.get_org()
            return kwargs

        def form_valid(self, form):
            try:
                org = self.request.user.get_org()
                data = json.loads(form.cleaned_data['import_file'])
                org.import_app(data, self.request.user, self.request.branding['link'])
            except Exception as e:
                # this is an unexpected error, report it to sentry
                logger = logging.getLogger(__name__)
                logger.error('Exception on app import: %s' % unicode(e), exc_info=True)
                form._errors['import_file'] = form.error_class([_("Sorry, your import file is invalid.")])
                return self.form_invalid(form)

            return super(OrgCRUDL.Import, self).form_valid(form)

    class Export(InferOrgMixin, OrgPermsMixin, SmartTemplateView):

        def post(self, request, *args, **kwargs):

            # get all of the selected flows and campaigns
            from temba.flows.models import Flow
            from temba.campaigns.models import Campaign

            flows = set(Flow.objects.filter(id__in=self.request.REQUEST.getlist('flows'), org=self.get_object(), is_active=True))
            campaigns = Campaign.objects.filter(id__in=self.request.REQUEST.getlist('campaigns'), org=self.get_object())

            # add in all the flows our campaign depends on
            exported_campaigns = []
            for campaign in campaigns:
                # don't export single message flows, those get recreated on each import
                for flow in campaign.get_flows():
                    flows.add(flow)
                exported_campaigns.append(campaign.as_json())

            definition = Flow.export_definitions(flows, fail_on_dependencies=False)
            definition['campaigns'] = exported_campaigns
            definition['site'] = request.branding['link']

            response = HttpResponse(json.dumps(definition, indent=2), content_type='application/javascript')
            response['Content-Disposition'] = 'attachment; filename=%s.json' % slugify(self.get_object().name)
            return response

        def get_context_data(self, **kwargs):
            from collections import defaultdict

            def connected_components(lists):
                neighbors = defaultdict(set)
                seen = set()
                for each in lists:
                    for item in each:
                        neighbors[item].update(each)

                def component(node, neighbors=neighbors, seen=seen, see=seen.add):
                    nodes = {node}
                    next_node = nodes.pop
                    while nodes:
                        node = next_node()
                        see(node)
                        nodes |= neighbors[node] - seen
                        yield node
                for node in neighbors:
                    if node not in seen:
                        yield sorted(component(node))

            context = super(OrgCRUDL.Export, self).get_context_data(**kwargs)

            include_archived = self.request.REQUEST.get('archived', 0)

            # all of our user facing flows
            flows = self.get_object().get_export_flows(include_archived=include_archived)

            # now add lists of flows with their dependencies
            all_depends = []
            for flow in flows:
                depends = flow.get_dependencies()
                all_depends.append([flow] + list(depends['flows']) + list(depends['campaigns']))

            # add all campaigns
            from temba.campaigns.models import Campaign
            campaigns = Campaign.objects.filter(org=self.get_object())

            if not include_archived:
                campaigns = campaigns.filter(is_archived=False)

            for campaign in campaigns:
                all_depends.append((campaign,))

            buckets = connected_components(all_depends)

            # sort our buckets, campaigns, flows, triggers
            bucket_list = []
            singles = []
            for bucket in buckets:
                if len(bucket) > 1:
                    bucket_list.append(sorted(list(bucket), key=attrgetter('__class__', 'name')))
                else:
                    singles.append(bucket[0])

            # put the buckets with the most items first
            bucket_list = sorted(bucket_list, key=lambda s: len(s), reverse=True)

            # sort our singles by type
            singles = sorted(singles, key=attrgetter('__class__', 'name'))

            context['archived'] = include_archived
            context['buckets'] = bucket_list
            context['singles'] = singles

            return context

    class TwilioConnect(ModalMixin, InferOrgMixin, OrgPermsMixin, SmartFormView):

        class TwilioConnectForm(forms.Form):
            account_sid = forms.CharField(help_text=_("Your Twilio Account SID"))
            account_token = forms.CharField(help_text=_("Your Twilio Account Token"))

            def clean(self):
                account_sid = self.cleaned_data.get('account_sid', None)
                account_token = self.cleaned_data.get('account_token', None)

                if not account_sid:
                    raise ValidationError(_("You must enter your Twilio Account SID"))

                if not account_token:
                    raise ValidationError(_("You must enter your Twilio Account Token"))

                try:
                    client = TwilioRestClient(account_sid, account_token)

                    # get the actual primary auth tokens from twilio and use them
                    account = client.accounts.get(account_sid)
                    self.cleaned_data['account_sid'] = account.sid
                    self.cleaned_data['account_token'] = account.auth_token
                except Exception:
                    raise ValidationError(_("The Twilio account SID and Token seem invalid. Please check them again and retry."))

                return self.cleaned_data

        form_class = TwilioConnectForm
        submit_button_name = "Save"
        success_url = '@channels.channel_claim_twilio'
        field_config = dict(account_sid=dict(label=""), account_token=dict(label=""))
        success_message = "Twilio Account successfully connected."

        def form_valid(self, form):
            account_sid = form.cleaned_data['account_sid']
            account_token = form.cleaned_data['account_token']

            org = self.get_object()
            org.connect_twilio(account_sid, account_token)
            org.save()

            response = self.render_to_response(self.get_context_data(form=form,
                                               success_url=self.get_success_url(),
                                               success_script=getattr(self, 'success_script', None)))

            response['Temba-Success'] = self.get_success_url()
            return response

    class NexmoConfiguration(InferOrgMixin, OrgPermsMixin, SmartReadView):

        def get(self, request, *args, **kwargs):
            org = self.get_object()

            nexmo_client = org.get_nexmo_client()
            if not nexmo_client:
                return HttpResponseRedirect(reverse("orgs.org_nexmo_connect"))

            nexmo_uuid = org.nexmo_uuid()
            mo_path = reverse('handlers.nexmo_handler', args=['receive', nexmo_uuid])
            dl_path = reverse('handlers.nexmo_handler', args=['status', nexmo_uuid])
            try:
                from temba.settings import TEMBA_HOST
                nexmo_client.update_account('http://%s%s' % (TEMBA_HOST, mo_path),
                                            'http://%s%s' % (TEMBA_HOST, dl_path))

                return HttpResponseRedirect(reverse("channels.channel_claim_nexmo"))

            except NexmoValidationError:
                return super(OrgCRUDL.NexmoConfiguration, self).get(request, *args, **kwargs)

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.NexmoConfiguration, self).get_context_data(**kwargs)

            from temba.settings import TEMBA_HOST
            org = self.get_object()
            config = org.config_json()
            context['nexmo_api_key'] = config[NEXMO_KEY]
            context['nexmo_api_secret'] = config[NEXMO_SECRET]

            nexmo_uuid = config.get(NEXMO_UUID, None)
            mo_path = reverse('handlers.nexmo_handler', args=['receive', nexmo_uuid])
            dl_path = reverse('handlers.nexmo_handler', args=['status', nexmo_uuid])
            context['mo_path'] = 'https://%s%s' % (TEMBA_HOST, mo_path)
            context['dl_path'] = 'https://%s%s' % (TEMBA_HOST, dl_path)

            return context

    class NexmoAccount(ModalMixin, InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        fields = ()
        submit_button_name = "Disconnect Nexmo"
        success_message = "Nexmo Account successfully disconnected."

        def get_success_url(self):
            return reverse("orgs.org_home")

        def save(self, obj):
            obj.remove_nexmo_account()

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.NexmoAccount, self).get_context_data(**kwargs)

            org = self.get_object()
            config = org.config_json()
            context['config'] = config

            return context

    class NexmoConnect(ModalMixin, InferOrgMixin, OrgPermsMixin, SmartFormView):

        class NexmoConnectForm(forms.Form):
            api_key = forms.CharField(help_text=_("Your Nexmo API key"))
            api_secret = forms.CharField(help_text=_("Your Nexmo API secret"))

            def clean(self):
                super(OrgCRUDL.NexmoConnect.NexmoConnectForm, self).clean()

                api_key = self.cleaned_data.get('api_key', None)
                api_secret = self.cleaned_data.get('api_secret', None)

                try:
                    client = NexmoClient(api_key, api_secret)
                    client.get_numbers()
                except Exception:
                    raise ValidationError(_("Your Nexmo API key and secret seem invalid. Please check them again and retry."))

                return self.cleaned_data

        form_class = NexmoConnectForm
        submit_button_name = "Save"
        success_url = '@orgs.org_nexmo_configuration'
        field_config = dict(api_key=dict(label=""), api_secret=dict(label=""))
        success_message = "Nexmo Account successfully connected."

        def form_valid(self, form):
            api_key = form.cleaned_data['api_key']
            api_secret = form.cleaned_data['api_secret']

            org = self.get_object()

            org.connect_nexmo(api_key, api_secret)

            org.save()

            response = self.render_to_response(self.get_context_data(form=form,
                                               success_url=self.get_success_url(),
                                               success_script=getattr(self, 'success_script', None)))

            response['Temba-Success'] = self.get_success_url()
            return response

    class PlivoConnect(ModalMixin, InferOrgMixin, OrgPermsMixin, SmartFormView):

        class PlivoConnectForm(forms.Form):
            auth_id = forms.CharField(help_text=_("Your Plivo AUTH ID"))
            auth_token = forms.CharField(help_text=_("Your Plivo AUTH TOKEN"))

            def clean(self):
                super(OrgCRUDL.PlivoConnect.PlivoConnectForm, self).clean()

                auth_id = self.cleaned_data.get('auth_id', None)
                auth_token = self.cleaned_data.get('auth_token', None)

                try:
                    client = plivo.RestAPI(auth_id, auth_token)
                    validation_response = client.get_account()
                except Exception:
                    raise ValidationError(_("Your Plivo AUTH ID and AUTH TOKEN seem invalid. Please check them again and retry."))

                if validation_response[0] != 200:
                    raise ValidationError(_("Your Plivo AUTH ID and AUTH TOKEN seem invalid. Please check them again and retry."))

                return self.cleaned_data

        form_class = PlivoConnectForm
        submit_button_name = "Save"
        success_url = '@channels.channel_claim_plivo'
        field_config = dict(auth_id=dict(label=""), auth_token=dict(label=""))
        success_message = "Plivo credentials verified. You can now add a Plivo channel."

        def form_valid(self, form):

            auth_id = form.cleaned_data['auth_id']
            auth_token = form.cleaned_data['auth_token']

            # add the credentials to the session
            self.request.session[PLIVO_AUTH_ID] = auth_id
            self.request.session[PLIVO_AUTH_TOKEN] = auth_token

            response = self.render_to_response(self.get_context_data(form=form,
                                               success_url=self.get_success_url(),
                                               success_script=getattr(self, 'success_script', None)))

            response['Temba-Success'] = self.get_success_url()
            return response

    class Manage(SmartListView):
        fields = ('credits', 'used', 'name', 'owner', 'created_on')
        default_order = ('-credits', '-created_on',)
        search_fields = ('name__icontains', 'created_by__email__iexact', 'config__icontains')
        link_fields = ('name', 'owner')
        title = "Organizations"

        def get_used(self, obj):
            if not obj.credits:
                used_pct = 0
            else:
                used_pct = round(100 * float(obj.get_credits_used()) / float(obj.credits))

            used_class = 'used-normal'
            if used_pct >= 75:
                used_class = 'used-warning'
            if used_pct >= 90:
                used_class = 'used-alert'
            return "<div class='used-pct %s'>%d%%</div>" % (used_class, used_pct)

        def get_credits(self, obj):
            if not obj.credits:
                obj.credits = 0
            return "<div class='num-credits'>%d</div>" % obj.credits

        def get_owner(self, obj):
            owner = obj.latest_admin()

            # default to the created by if there are no admins
            if not owner:
                owner = obj.created_by

            url = reverse('orgs.org_service')
            return "<a href='%s?organization=%d' class='service posterize btn btn-tiny'>Service</a><div class='owner-name'>%s %s</div>" \
                   "<div class='owner-email'>%s</div>" % (url, obj.id, owner.first_name, owner.last_name, owner)

        def get_name(self, obj):
            suspended = ''
            if obj.is_suspended():
                suspended = '<span class="suspended">(Suspended)</span>'

            return "<div class='org-name'>%s %s</div><div class='org-timezone'>%s</div>" % (suspended, obj.name, obj.timezone)

        def derive_queryset(self, **kwargs):
            queryset = super(OrgCRUDL.Manage, self).derive_queryset(**kwargs)
            queryset = queryset.filter(is_active=True)
            queryset = queryset.annotate(credits=Sum('topups__credits'))
            queryset = queryset.annotate(paid=Sum('topups__price'))
            return queryset

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.Manage, self).get_context_data(**kwargs)
            context['searches'] = ['Nyaruka', ]
            return context

        def lookup_field_link(self, context, field, obj):
            if field == 'owner':
                return reverse('users.user_update', args=[obj.created_by.pk])
            return super(OrgCRUDL.Manage, self).lookup_field_link(context, field, obj)

        def get_created_by(self, obj):
            return "%s %s - %s" % (obj.created_by.first_name, obj.created_by.last_name, obj.created_by.email)

    class Update(SmartUpdateView):
        class OrgUpdateForm(forms.ModelForm):
            viewers = forms.ModelMultipleChoiceField(User.objects.all(), required=False)
            editors = forms.ModelMultipleChoiceField(User.objects.all(), required=False)
            surveyors = forms.ModelMultipleChoiceField(User.objects.all(), required=False)
            administrators = forms.ModelMultipleChoiceField(User.objects.all(), required=False)

            class Meta:
                model = Org
                fields = '__all__'

        form_class = OrgUpdateForm

        def get_success_url(self):
            return reverse('orgs.org_update', args=[self.get_object().pk])

        def get_gear_links(self):
            links = []

            org = self.get_object()

            links.append(dict(title=_('Topups'),
                              style='btn-primary',
                              href='%s?org=%d' % (reverse("orgs.topup_manage"), org.pk)))

            if org.is_suspended():
                links.append(dict(title=_('Restore'),
                                  style='btn-secondary',
                                  posterize=True,
                                  href='%s?status=restored' % reverse("orgs.org_update", args=[org.pk])))
            else:
                links.append(dict(title=_('Suspend'),
                                  style='btn-secondary',
                                  posterize=True,
                                  href='%s?status=suspended' % reverse("orgs.org_update", args=[org.pk])))

            if not org.is_whitelisted():
                links.append(dict(title=_('Whitelist'),
                                  style='btn-secondary',
                                  posterize=True,
                                  href='%s?status=whitelisted' % reverse("orgs.org_update", args=[org.pk])))

            return links

        def post(self, request, *args, **kwargs):
            if 'status' in request.REQUEST:
                if request.REQUEST.get('status', None) == SUSPENDED:
                    self.get_object().set_suspended()
                elif request.REQUEST.get('status', None) == WHITELISTED:
                    self.get_object().set_whitelisted()
                elif request.REQUEST.get('status', None) == RESTORED:
                    self.get_object().set_restored()
                return HttpResponseRedirect(self.get_success_url())
            return super(OrgCRUDL.Update, self).post(request, *args, **kwargs)

    class ManageAccounts(InferOrgMixin, OrgPermsMixin, SmartUpdateView):

        class AccountsForm(forms.ModelForm):
            invite_emails = forms.CharField(label=_("Invite people to your organization"), required=False)
            invite_group = forms.ChoiceField(choices=(('A', _("Administrators")),
                                                      ('E', _("Editors")),
                                                      ('V', _("Viewers")),
                                                      ('S', _("Surveyors"))),
                                             required=True, initial='V', label=_("User group"))

            def add_user_group_fields(self, groups, users):
                fields_by_user = {}

                for user in users:
                    fields = []
                    field_mapping = []

                    for group in groups:
                        check_field = forms.BooleanField(required=False)
                        field_name = "%s_%d" % (group.lower(), user.pk)

                        field_mapping.append((field_name, check_field))
                        fields.append(field_name)

                    self.fields = OrderedDict(self.fields.items() + field_mapping)
                    fields_by_user[user] = fields
                return fields_by_user

            def clean_invite_emails(self):
                emails = self.cleaned_data['invite_emails'].lower().strip()
                if emails:
                    email_list = emails.split(',')
                    for email in email_list:
                        try:
                            validate_email(email)
                        except ValidationError:
                            raise forms.ValidationError(_("One of the emails you entered is invalid."))
                return emails

            class Meta:
                model = Invitation
                fields = ('invite_emails', 'invite_group')

        form_class = AccountsForm
        success_url = "@orgs.org_home"
        success_message = ""
        ORG_GROUPS = ('Administrators', 'Editors', 'Viewers', 'Surveyors')

        @staticmethod
        def org_group_set(org, group_name):
            return getattr(org, group_name.lower())

        def derive_title(self):
            return _("Manage %(name)s Accounts") % {'name': self.get_object().name}

        def derive_initial(self):
            initial = super(OrgCRUDL.ManageAccounts, self).derive_initial()

            org = self.get_object()
            for group in self.ORG_GROUPS:
                users_in_group = self.org_group_set(org, group).all()

                for user in users_in_group:
                    initial['%s_%d' % (group.lower(), user.pk)] = True

            return initial

        def get_form(self, form_class):
            form = super(OrgCRUDL.ManageAccounts, self).get_form(form_class)

            self.org_users = self.get_object().get_org_users()
            self.fields_by_users = form.add_user_group_fields(self.ORG_GROUPS, self.org_users)

            return form

        def post_save(self, obj):
            obj = super(OrgCRUDL.ManageAccounts, self).post_save(obj)

            cleaned_data = self.form.cleaned_data
            org = self.get_object()

            invite_emails = cleaned_data['invite_emails'].lower().strip()
            invite_group = cleaned_data['invite_group']
            invite_host = self.request.branding['host']

            if invite_emails:
                for email in invite_emails.split(','):
                    # if they already have an invite, update it
                    invites = Invitation.objects.filter(email=email, org=org).order_by('-pk')
                    invitation = invites.first()

                    if invitation:
                        invites.exclude(pk=invitation.pk).delete()  # remove any old invites

                        invitation.user_group = invite_group
                        invitation.is_active = True
                        invitation.save()
                    else:
                        invitation = Invitation.create(org, self.request.user, email, invite_group, invite_host)

                    invitation.send_invitation()

            current_groups = {}
            new_groups = {}

            for group in self.ORG_GROUPS:
                # gather up existing users with their groups
                for user in self.org_group_set(org, group).all():
                    current_groups[user] = group

                # parse form fields to get new roles
                for field in self.form.cleaned_data:
                    if field.startswith(group.lower() + '_') and self.form.cleaned_data[field]:
                        user = User.objects.get(pk=field.split('_')[1])
                        new_groups[user] = group

            for user in current_groups.keys():
                current_group = current_groups.get(user)
                new_group = new_groups.get(user)

                if current_group != new_group:
                    if current_group:
                        self.org_group_set(org, current_group).remove(user)
                    if new_group:
                        self.org_group_set(org, new_group).add(user)

                    # when a user's role changes, delete any API tokens they're no longer allowed to have
                    api_roles = APIToken.get_allowed_roles(org, user)
                    APIToken.objects.filter(org=org, user=user).exclude(role__in=api_roles).delete()

            return obj

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.ManageAccounts, self).get_context_data(**kwargs)
            org = self.get_object()
            context['org'] = org
            context['org_users'] = self.org_users
            context['group_fields'] = self.fields_by_users
            context['invites'] = Invitation.objects.filter(org=org, is_active=True).order_by('email')

            return context

        def get_success_url(self):
            still_in_org = self.request.user in self.get_object().get_org_users()

            # if current user no longer belongs to this org, redirect to org chooser
            return reverse('orgs.org_home') if still_in_org else reverse('orgs.org_choose')

    class Service(SmartFormView):
        class ServiceForm(forms.Form):
            organization = forms.ModelChoiceField(queryset=Org.objects.all(), empty_label=None)

        form_class = ServiceForm
        success_url = '@msgs.msg_inbox'
        fields = ('organization',)

        # valid form means we set our org and redirect to their inbox
        def form_valid(self, form):
            org = form.cleaned_data['organization']
            self.request.session['org_id'] = org.pk
            return HttpResponseRedirect(self.get_success_url())

        # invalid form login 'logs out' the user from the org and takes them to the org manage page
        def form_invalid(self, form):
            self.request.session['org_id'] = None
            return HttpResponseRedirect(reverse('orgs.org_manage'))

    class Choose(SmartFormView):
        class ChooseForm(forms.Form):
            organization = forms.ModelChoiceField(queryset=Org.objects.all(), empty_label=None)

        form_class = ChooseForm
        success_url = '@msgs.msg_inbox'
        fields = ('organization',)
        title = _("Select your Organization")

        def pre_process(self, request, *args, **kwargs):
            if self.request.user.is_authenticated():
                user_orgs = self.request.user.get_user_orgs()

                if self.request.user.is_superuser or self.request.user.is_staff:
                    return HttpResponseRedirect(reverse('orgs.org_manage'))

                elif user_orgs.count() == 1:
                    org = user_orgs[0]
                    self.request.session['org_id'] = org.pk
                    if org.get_org_surveyors().filter(username=self.request.user.username):
                        return HttpResponseRedirect(reverse('orgs.org_surveyor'))

                    return HttpResponseRedirect(self.get_success_url())

            return None

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.Choose, self).get_context_data(**kwargs)

            context['orgs'] = self.request.user.get_user_orgs()
            return context

        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated()

        def customize_form_field(self, name, field):
            if name == 'organization':
                user_orgs = self.request.user.get_user_orgs()
                field.widget.choices.queryset = user_orgs
            return field

        def form_valid(self, form):
            org = form.cleaned_data['organization']

            if org in self.request.user.get_user_orgs():
                self.request.session['org_id'] = org.pk
            else:
                return HttpResponseRedirect(reverse('orgs.org_choose'))

            if org.get_org_surveyors().filter(username=self.request.user.username):
                return HttpResponseRedirect(reverse('orgs.org_surveyor'))

            return HttpResponseRedirect(self.get_success_url())

    class CreateLogin(SmartUpdateView):
        title = ""
        form_class = OrgSignupForm
        permission = None
        fields = ('first_name', 'last_name', 'email', 'password')
        success_message = ''
        success_url = '@msgs.msg_inbox'
        submit_button_name = _("Create")
        permission = False

        def pre_process(self, request, *args, **kwargs):
            org = self.get_object()
            if not org:
                messages.info(request, _("Your invitation link is invalid. Please contact your organization administrator."))
                return HttpResponseRedirect(reverse('public.public_index'))
            return None

        def pre_save(self, obj):
            obj = super(OrgCRUDL.CreateLogin, self).pre_save(obj)

            user = Org.create_user(self.form.cleaned_data['email'],
                                   self.form.cleaned_data['password'])

            user.first_name = self.form.cleaned_data['first_name']
            user.last_name = self.form.cleaned_data['last_name']
            user.save()

            self.invitation = self.get_invitation()

            # log the user in
            user = authenticate(username=user.username, password=self.form.cleaned_data['password'])
            login(self.request, user)
            if self.invitation.user_group == 'A':
                obj.administrators.add(user)
            elif self.invitation.user_group == 'E':
                obj.editors.add(user)
            elif self.invitation.user_group == 'S':
                obj.surveyors.add(user)
            else:
                obj.viewers.add(user)

            # make the invitation inactive
            self.invitation.is_active = False
            self.invitation.save()

            return obj

        def get_success_url(self):
            if self.invitation.user_group == 'S':
                return reverse('orgs.org_surveyor')
            return super(OrgCRUDL.CreateLogin, self).get_success_url()

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<secret>\w+)/$' % (path, action)

        def get_invitation(self, **kwargs):
            invitation = None
            secret = self.kwargs.get('secret')
            invitations = Invitation.objects.filter(secret=secret, is_active=True)
            if invitations:
                invitation = invitations[0]
            return invitation

        def get_object(self, **kwargs):
            invitation = self.get_invitation()
            if invitation:
                return invitation.org
            return None

        def derive_title(self):
            org = self.get_object()
            return _("Join %(name)s") % {'name': org.name}

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.CreateLogin, self).get_context_data(**kwargs)

            context['secret'] = self.kwargs.get('secret')
            context['org'] = self.get_object()

            return context

    class Join(SmartUpdateView):
        class JoinForm(forms.ModelForm):

            class Meta:
                model = Org
                fields = ()

        success_message = ''
        form_class = JoinForm
        success_url = "@msgs.msg_inbox"
        submit_button_name = _("Join")
        permission = False

        def pre_process(self, request, *args, **kwargs):
            secret = self.kwargs.get('secret')

            org = self.get_object()
            if not org:
                messages.info(request, _("Your invitation link has expired. Please contact your organization administrator."))
                return HttpResponseRedirect(reverse('public.public_index'))

            if not request.user.is_authenticated():
                return HttpResponseRedirect(reverse('orgs.org_create_login', args=[secret]))
            return None

        def derive_title(self):
            org = self.get_object()
            return _("Join %(name)s") % {'name': org.name}

        def save(self, org):
            org = self.get_object()
            self.invitation = self.get_invitation()
            if org:
                if self.invitation.user_group == 'A':
                    org.administrators.add(self.request.user)
                elif self.invitation.user_group == 'E':
                    org.editors.add(self.request.user)
                elif self.invitation.user_group == 'S':
                    org.surveyors.add(self.request.user)
                else:
                    org.viewers.add(self.request.user)

                # make the invitation inactive
                self.invitation.is_active = False
                self.invitation.save()

                # set the active org on this user
                self.request.user.set_org(org)
                self.request.session['org_id'] = org.pk

        def get_success_url(self):
            if self.invitation.user_group == 'S':
                return reverse('orgs.org_surveyor')

            return super(OrgCRUDL.Join, self).get_success_url()

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<secret>\w+)/$' % (path, action)

        def get_invitation(self, **kwargs):
            invitation = None
            secret = self.kwargs.get('secret')
            invitations = Invitation.objects.filter(secret=secret, is_active=True)
            if invitations:
                invitation = invitations[0]
            return invitation

        def get_object(self, **kwargs):
            invitation = self.get_invitation()
            if invitation:
                return invitation.org

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.Join, self).get_context_data(**kwargs)

            context['org'] = self.get_object()
            return context

    class Surveyor(InferOrgMixin, OrgPermsMixin, SmartReadView):
        def derive_title(self):
            return _('Welcome!')

    class Grant(SmartCreateView):
        title = _("Create Organization Account")
        form_class = OrgGrantForm
        fields = ('first_name', 'last_name', 'email', 'password', 'name', 'timezone', 'credits')
        success_message = 'Organization successfully created.'
        submit_button_name = _("Create")
        permission = 'orgs.org_grant'
        success_url = '@orgs.org_grant'

        def create_user(self):
            user = User.objects.filter(username__iexact=self.form.cleaned_data['email']).first()
            if not user:
                user = Org.create_user(self.form.cleaned_data['email'],
                                       self.form.cleaned_data['password'])

            user.first_name = self.form.cleaned_data['first_name']
            user.last_name = self.form.cleaned_data['last_name']
            user.save()

            # set our language to the default for the site
            language = self.request.branding.get('language', settings.DEFAULT_LANGUAGE)
            user_settings = user.get_settings()
            user_settings.language = language
            user_settings.save()

            return user

        def get_form_kwargs(self):
            kwargs = super(OrgCRUDL.Grant, self).get_form_kwargs()
            kwargs['branding'] = self.request.branding
            return kwargs

        def pre_save(self, obj):
            obj = super(OrgCRUDL.Grant, self).pre_save(obj)

            self.user = self.create_user()

            obj.created_by = self.user
            obj.modified_by = self.user

            slug = Org.get_unique_slug(self.form.cleaned_data['name'])
            obj.slug = slug
            obj.brand = self.request.get_host()

            return obj

        def get_welcome_size(self):
            return self.form.cleaned_data['credits']

        def post_save(self, obj):
            obj = super(OrgCRUDL.Grant, self).post_save(obj)
            obj.administrators.add(self.user)

            if not self.request.user.is_anonymous() and self.request.user.has_perm('orgs.org_grant'):
                obj.administrators.add(self.request.user.pk)

            brand = BrandingMiddleware.get_branding_for_host(obj.brand)
            obj.initialize(brand=brand, topup_size=self.get_welcome_size())

            return obj

    class Signup(Grant):
        title = _("Sign Up")
        form_class = OrgSignupForm
        permission = None
        success_message = ''
        submit_button_name = _("Save")

        def get_success_url(self):
            return "%s?start" % reverse('public.public_welcome')

        def pre_process(self, request, *args, **kwargs):
            # if our brand doesn't allow signups, then redirect to the homepage
            if not request.branding.get('allow_signups', False):
                return HttpResponseRedirect(reverse('public.public_index'))

            else:
                return super(OrgCRUDL.Signup, self).pre_process(request, *args, **kwargs)

        def derive_initial(self):
            initial = super(OrgCRUDL.Signup, self).get_initial()
            initial['email'] = self.request.REQUEST.get('email', None)
            return initial

        def get_welcome_size(self):
            welcome_topup_size = self.request.branding.get('welcome_topup', WELCOME_TOPUP_SIZE)
            return welcome_topup_size

        def post_save(self, obj):
            obj = super(OrgCRUDL.Signup, self).post_save(obj)
            self.request.session['org_id'] = obj.pk

            user = authenticate(username=self.user.username, password=self.form.cleaned_data['password'])
            login(self.request, user)
            analytics.track(self.request.user.username, 'temba.org_signup', dict(org=obj.name))

            return obj

    class Webhook(InferOrgMixin, OrgPermsMixin, SmartUpdateView):

        class WebhookForm(forms.ModelForm):
            webhook = forms.URLField(required=False, label=_("Webhook URL"), help_text="")
            headers = forms.CharField(required=False)
            mt_sms = forms.BooleanField(required=False, label=_("Incoming SMS"))
            mo_sms = forms.BooleanField(required=False, label=_("Outgoing SMS"))
            mt_call = forms.BooleanField(required=False, label=_("Incoming Calls"))
            mo_call = forms.BooleanField(required=False, label=_("Outgoing Calls"))
            alarm = forms.BooleanField(required=False, label=_("Channel Alarms"))

            class Meta:
                model = Org
                fields = ('webhook', 'headers', 'mt_sms', 'mo_sms', 'mt_call', 'mo_call', 'alarm')

            def clean_headers(self):
                idx = 1
                headers = dict()
                key = 'header_%d_key' % idx
                value = 'header_%d_value' % idx

                while key in self.data:
                    if self.data.get(value, ''):
                        headers[self.data[key]] = self.data[value]

                    idx += 1
                    key = 'header_%d_key' % idx
                    value = 'header_%d_value' % idx

                return headers

        form_class = WebhookForm
        success_url = '@orgs.org_home'
        success_message = ''

        def pre_save(self, obj):
            obj = super(OrgCRUDL.Webhook, self).pre_save(obj)

            data = self.form.cleaned_data

            webhook_events = 0
            if data['mt_sms']:
                webhook_events = MT_SMS_EVENTS
            if data['mo_sms']:
                webhook_events |= MO_SMS_EVENTS
            if data['mt_call']:
                webhook_events |= MT_CALL_EVENTS
            if data['mo_call']:
                webhook_events |= MO_CALL_EVENTS
            if data['alarm']:
                webhook_events |= ALARM_EVENTS

            analytics.track(self.request.user.username, 'temba.org_configured_webhook')

            obj.webhook_events = webhook_events

            webhook_data = dict()
            if data['webhook']:
                webhook_data.update({'url': data['webhook']})
                webhook_data.update({'method': 'POST'})

            if data['headers']:
                webhook_data.update({'headers': data['headers']})

            obj.webhook = json.dumps(webhook_data)

            return obj

    class Home(FormaxMixin, InferOrgMixin, OrgPermsMixin, SmartReadView):
        title = _("Your Account")

        def get_gear_links(self):
            links = []

            links.append(dict(title=_('Logout'),
                              style='btn-primary',
                              href=reverse("users.user_logout")))

            if self.has_org_perm("channels.channel_claim"):
                links.append(dict(title=_('Add Channel'),
                                  href=reverse('channels.channel_claim')))

            if self.has_org_perm("orgs.org_export"):
                links.append(dict(title=_('Export'), href=reverse('orgs.org_export')))

            if self.has_org_perm("orgs.org_import"):
                links.append(dict(title=_('Import'), href=reverse('orgs.org_import')))

            return links

        def add_channel_section(self, formax, channel):

            if self.has_org_perm('channels.channel_read'):
                from temba.channels.views import get_channel_icon
                icon = get_channel_icon(channel.channel_type)
                formax.add_section('channel', reverse('channels.channel_read', args=[channel.uuid]), icon=icon, action='link')

        def derive_formax_sections(self, formax, context):

            if self.has_org_perm('orgs.topup_list'):
                formax.add_section('topups', reverse('orgs.topup_list'), icon='icon-coins', action='link')

            # add the channel option if we have one
            user = self.request.user
            org = user.get_org()

            if self.has_org_perm("channels.channel_update"):
                # get any channel thats not a delegate
                channels = Channel.objects.filter(org=org, is_active=True, parent=None).order_by('-role')
                for channel in channels:
                    self.add_channel_section(formax, channel)

                client = org.get_twilio_client()
                if client:
                    formax.add_section('twilio', reverse('orgs.org_twilio_account'), icon='icon-channel-twilio')

            if self.has_org_perm('orgs.org_profile'):
                formax.add_section('user', reverse('orgs.user_edit'), icon='icon-user', action='redirect')

            if self.has_org_perm('orgs.org_edit'):
                formax.add_section('org', reverse('orgs.org_edit'), icon='icon-office')

            if self.has_org_perm('orgs.org_languages'):
                formax.add_section('languages', reverse('orgs.org_languages'), icon='icon-language')

            if self.has_org_perm('orgs.org_country'):
                formax.add_section('country', reverse('orgs.org_country'), icon='icon-location2')

            if self.has_org_perm('orgs.org_webhook'):
                formax.add_section('webhook', reverse('orgs.org_webhook'), icon='icon-cloud-upload')

            # only pro orgs get multiple users
            if self.has_org_perm("orgs.org_manage_accounts") and org.is_pro():
                formax.add_section('manageaccount', reverse('orgs.org_manage_accounts'), icon='icon-users', action='redirect')

    class TwilioAccount(InferOrgMixin, OrgPermsMixin, SmartUpdateView):

        success_message = ''

        class TwilioKeys(forms.ModelForm):
            account_sid = forms.CharField(max_length=128, label=_("Account SID"), required=False)
            account_token = forms.CharField(max_length=128, label=_("Account Token"), required=False)
            disconnect = forms.CharField(widget=forms.HiddenInput, max_length=6, required=True)

            def clean(self):
                super(OrgCRUDL.TwilioAccount.TwilioKeys, self).clean()
                if self.cleaned_data.get('disconnect', 'false') == 'false':
                    account_sid = self.cleaned_data.get('account_sid', None)
                    account_token = self.cleaned_data.get('account_token', None)

                    if not account_sid:
                        raise ValidationError(_("You must enter your Twilio Account SID"))

                    if not account_token:
                        raise ValidationError(_("You must enter your Twilio Account Token"))

                    try:
                        client = TwilioRestClient(account_sid, account_token)

                        # get the actual primary auth tokens from twilio and use them
                        account = client.accounts.get(account_sid)
                        self.cleaned_data['account_sid'] = account.sid
                        self.cleaned_data['account_token'] = account.auth_token
                    except Exception:
                        raise ValidationError(_("The Twilio account SID and Token seem invalid. Please check them again and retry."))

                return self.cleaned_data

            class Meta:
                model = Org
                fields = ('account_sid', 'account_token', 'disconnect')

        form_class = TwilioKeys

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.TwilioAccount, self).get_context_data(**kwargs)
            client = self.object.get_twilio_client()
            if client:
                account_sid = client.auth[0]
                sid_length = len(account_sid)
                context['account_sid'] = '%s%s' % ('\u066D' * (sid_length - 16), account_sid[-16:])
            return context

        def derive_initial(self):
            initial = super(OrgCRUDL.TwilioAccount, self).derive_initial()
            config = json.loads(self.object.config)
            initial['account_sid'] = config['ACCOUNT_SID']
            initial['account_token'] = config['ACCOUNT_TOKEN']
            initial['disconnect'] = 'false'
            return initial

        def form_valid(self, form):
            disconnect = form.cleaned_data.get('disconnect', 'false') == 'true'
            if disconnect:
                user = self.request.user
                org = user.get_org()
                org.remove_twilio_account()
                return HttpResponseRedirect(reverse('orgs.org_home'))
            else:

                user = self.request.user
                org = user.get_org()
                account_sid = form.cleaned_data['account_sid']
                account_token = form.cleaned_data['account_token']

                org.connect_twilio(account_sid, account_token)
                return super(OrgCRUDL.TwilioAccount, self).form_valid(form)

    class Edit(InferOrgMixin, OrgPermsMixin, SmartUpdateView):

        class OrgForm(forms.ModelForm):
            name = forms.CharField(max_length=128, label=_("The name of your organization"), help_text="")
            timezone = TimeZoneField(label=_("Your organization's timezone"), help_text="")
            slug = forms.SlugField(max_length=255, label=_("The slug, or short name for your organization"), help_text="")

            class Meta:
                model = Org
                fields = ('name', 'slug', 'timezone', 'date_format')

        success_message = ''
        form_class = OrgForm

        def has_permission(self, request, *args, **kwargs):
            self.org = self.derive_org()
            return self.has_org_perm('orgs.org_edit')

    class Country(InferOrgMixin, OrgPermsMixin, SmartUpdateView):

        class CountryForm(forms.ModelForm):
            country = forms.ModelChoiceField(
                Org.get_possible_countries(), required=False,
                label=_("The country used for location values. (optional)"),
                help_text="State and district names will be searched against this country."
            )

            class Meta:
                model = Org
                fields = ('country',)

        success_message = ''
        form_class = CountryForm

        def has_permission(self, request, *args, **kwargs):
            self.org = self.derive_org()
            return self.request.user.has_perm('orgs.org_country') or self.has_org_perm('orgs.org_country')

    class Languages(InferOrgMixin, OrgPermsMixin, SmartUpdateView):

        class LanguagesForm(forms.ModelForm):
            primary_lang = forms.CharField(
                required=False, label=_('Primary Language'),
                help_text=_('The primary language will be used for contacts with no language preference.')
            )
            languages = forms.CharField(
                required=False, label=_('Additional Languages'),
                help_text=_('Add any other languages you would like to provide translations for.')
            )

            def __init__(self, *args, **kwargs):
                self.org = kwargs['org']
                del kwargs['org']
                super(OrgCRUDL.Languages.LanguagesForm, self).__init__(*args, **kwargs)

            class Meta:
                model = Org
                fields = ('primary_lang', 'languages')

        success_message = ''
        form_class = LanguagesForm

        def get_form_kwargs(self):
            kwargs = super(OrgCRUDL.Languages, self).get_form_kwargs()
            kwargs['org'] = self.request.user.get_org()
            return kwargs

        def derive_initial(self):

            initial = super(OrgCRUDL.Languages, self).derive_initial()
            langs = ','.join([lang.iso_code for lang in self.get_object().languages.filter(orgs=None).order_by('name')])
            initial['languages'] = langs

            if self.object.primary_language:
                initial['primary_lang'] = self.object.primary_language.iso_code

            return initial

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.Languages, self).get_context_data(**kwargs)
            languages = [lang.name for lang in self.request.user.get_org().languages.filter(orgs=None).order_by('name')]
            lang_count = len(languages)

            if lang_count == 2:
                context['languages'] = _(' and ').join(languages)
            elif lang_count > 2:
                context['languages'] = _('%s and %s') % (', '.join(languages[:-1]), languages[-1])
            elif lang_count == 1:
                context['languages'] = languages[0]
            return context

        def get(self, request, *args, **kwargs):

            if 'search' in self.request.REQUEST or 'initial' in self.request.REQUEST:
                initial = self.request.REQUEST.get('initial', '').split(',')
                matches = []

                if len(initial) > 0:
                    for iso_code in initial:
                        if iso_code:
                            lang = languages.get_language_name(iso_code)
                            matches.append(dict(id=iso_code, text=lang))

                if len(matches) == 0:
                    search = self.request.REQUEST.get('search', '').strip().lower()
                    matches += languages.search_language_names(search)
                return build_json_response(dict(results=matches))

            return super(OrgCRUDL.Languages, self).get(request, *args, **kwargs)

        def form_valid(self, form):

            user = self.request.user
            org = user.get_org()

            primary = form.cleaned_data['primary_lang']
            iso_codes = form.cleaned_data['languages'].split(',')
            if primary not in iso_codes:
                iso_codes.append(primary)

            # create new languages
            for iso_code in iso_codes:
                if iso_code:
                    name = languages.get_language_name(iso_code)
                    language = org.languages.filter(iso_code=iso_code).first()

                    # if it doesn't exist yet, create it
                    if name and not language:
                        language = org.languages.create(created_by=user, modified_by=user, iso_code=iso_code, name=name)

                    # store our primary language
                    if iso_code == primary:
                        self.object.primary_language = language
                        self.object.save(update_fields=['primary_language'])

            # remove our primary language if necessary
            if org.primary_language and org.primary_language.iso_code not in iso_codes:
                org.primary_language = None
                org.save()

            # remove any languages that are not in our new list
            org.languages.exclude(iso_code__in=iso_codes).delete()

            return super(OrgCRUDL.Languages, self).form_valid(form)

        def has_permission(self, request, *args, **kwargs):
            self.org = self.derive_org()
            return self.request.user.has_perm('orgs.org_country') or self.has_org_perm('orgs.org_country')

    class ClearCache(SmartUpdateView):  # pragma: no cover
        fields = ('id',)
        success_message = None
        success_url = 'id@orgs.org_update'

        def pre_process(self, request, *args, **kwargs):
            cache = OrgCache(int(request.REQUEST['cache']))
            num_deleted = self.get_object().clear_caches([cache])
            self.success_message = _("Cleared %s cache for this organization (%d keys)") % (cache.name, num_deleted)

    class Download(SmartTemplateView):
        """
        For backwards compatibility, redirect old org/download style requests to the assets app
        """
        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'%s/%s/(?P<task_type>\w+)/(?P<pk>\d+)/$' % (path, action)

        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated()

        def get(self, request, *args, **kwargs):
            types_to_assets = {'contacts': AssetType.contact_export,
                               'flows': AssetType.results_export,
                               'messages': AssetType.message_export}

            task_type = self.kwargs.get('task_type')
            asset_type = types_to_assets[task_type]
            identifier = self.kwargs.get('pk')
            return HttpResponseRedirect(reverse('assets.download',
                                                kwargs=dict(type=asset_type.name, pk=identifier)))


class TopUpCRUDL(SmartCRUDL):
    actions = ('list', 'create', 'read', 'manage', 'update')
    model = TopUp

    class Read(OrgPermsMixin, SmartReadView):
        def derive_queryset(self, **kwargs):
            return TopUp.objects.filter(is_active=True, org=self.request.user.get_org()).order_by('-expires_on')

    class List(OrgPermsMixin, SmartListView):
        def derive_queryset(self, **kwargs):
            return TopUp.objects.filter(is_active=True, org=self.request.user.get_org()).order_by('-expires_on')

        def get_context_data(self, **kwargs):
            context = super(TopUpCRUDL.List, self).get_context_data(**kwargs)
            context['org'] = self.request.user.get_org()

            now = timezone.now()
            context['now'] = now
            context['expiration_period'] = now + timedelta(days=30)
            return context

        def get_template_names(self):
            if 'HTTP_X_FORMAX' in self.request.META:
                return ['orgs/topup_list_summary.haml']
            else:
                return super(TopUpCRUDL.List, self).get_template_names()

    class Create(SmartCreateView):
        """
        This is only for root to be able to credit accounts.
        """
        fields = ('credits', 'price', 'comment')

        def get_success_url(self):
            return reverse('orgs.topup_manage') + ('?org=%d' % self.object.org.id)

        def save(self, obj):
            obj.org = Org.objects.get(pk=self.request.REQUEST['org'])
            return TopUp.create(self.request.user, price=obj.price, credits=obj.credits, org=obj.org)

        def post_save(self, obj):
            obj = super(TopUpCRUDL.Create, self).post_save(obj)
            obj.org.apply_topups()
            return obj

    class Update(SmartUpdateView):
        fields = ('is_active', 'price', 'credits', 'expires_on')

        def get_success_url(self):
            return reverse('orgs.topup_manage') + ('?org=%d' % self.object.org.id)

        def post_save(self, obj):
            obj = super(TopUpCRUDL.Update, self).post_save(obj)
            obj.org.update_caches(OrgEvent.topup_updated, obj)
            obj.org.apply_topups()
            return obj

    class Manage(SmartListView):
        """
        This is only for root to be able to manage topups on an account
        """
        fields = ('credits', 'price', 'comment', 'created_on', 'expires_on')
        success_url = '@orgs.org_manage'
        default_order = '-expires_on'

        def lookup_field_link(self, context, field, obj):
            return reverse('orgs.topup_update', args=[obj.id])

        def get_price(self, obj):
            if obj.price:
                return "$%.2f" % (obj.price / 100.0)
            else:
                return "-"

        def get_context_data(self, **kwargs):
            context = super(TopUpCRUDL.Manage, self).get_context_data(**kwargs)
            context['org'] = self.org
            return context

        def derive_queryset(self):
            self.org = Org.objects.get(pk=self.request.REQUEST['org'])
            return self.org.topups.all()


class StripeHandler(View):  # pragma: no cover
    """
    Handles WebHook events from Stripe.  We are interested as to when invoices are
    charged by Stripe so we can send the user an invoice email.
    """
    @disable_middleware
    def dispatch(self, *args, **kwargs):
        return super(StripeHandler, self).dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        return HttpResponse("ILLEGAL METHOD")

    def post(self, request, *args, **kwargs):
        import stripe
        from temba.orgs.models import Org, TopUp

        # stripe delivers a JSON payload
        stripe_data = json.loads(request.body)

        # but we can't trust just any response, so lets go look up this event
        stripe.api_key = get_stripe_credentials()[1]
        event = stripe.Event.retrieve(stripe_data['id'])

        if not event:
            return HttpResponse("Ignored, no event")

        if not event.livemode:
            return HttpResponse("Ignored, test event")

        # we only care about invoices being paid or failing
        if event.type == 'charge.succeeded' or event.type == 'charge.failed':
            charge = event.data.object
            charge_date = datetime.fromtimestamp(charge.created)
            description = charge.description
            amount = "$%s" % (Decimal(charge.amount) / Decimal(100)).quantize(Decimal(".01"))

            # look up our customer
            customer = stripe.Customer.retrieve(charge.customer)

            # and our org
            org = Org.objects.filter(stripe_customer=customer.id).first()
            if not org:
                return HttpResponse("Ignored, no org for customer")

            # look up the topup that matches this charge
            topup = TopUp.objects.filter(stripe_charge=charge.id).first()
            if topup and event.type == 'charge.failed':
                topup.rollback()
                topup.save()

            # we know this org, trigger an event for a payment succeeding
            if org.administrators.all():
                if event.type == 'charge_succeeded':
                    track = "temba.charge_succeeded"
                else:
                    track = "temba.charge_failed"

                context = dict(description=description,
                               invoice_id=charge.id,
                               invoice_date=charge_date.strftime("%b %e, %Y"),
                               amount=amount,
                               org=org.name,
                               cc_last4=charge.card.last4,
                               cc_type=charge.card.type,
                               cc_name=charge.card.name)

                admin_email = org.administrators.all().first().email

                analytics.track(admin_email, track, context)
                return HttpResponse("Event '%s': %s" % (track, context))

        # empty response, 200 lets Stripe know we handled it
        return HttpResponse("Ignored, uninteresting event")
