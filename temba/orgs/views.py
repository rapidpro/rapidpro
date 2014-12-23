from __future__ import unicode_literals

from operator import attrgetter
from django.contrib.humanize.templatetags.humanize import intcomma
from django.db.models import Sum
from django.forms import Form
from django.utils.text import slugify
import pycountry
from temba.flows.models import Flow
from temba.nexmo import NexmoClient
from temba.channels.models import Channel, ANDROID, TWILIO, NEXMO, KANNEL
from temba.formax import FormaxMixin
from collections import OrderedDict

import re
import traceback
import json
from timezones.forms import TimeZoneField
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.contrib.auth import authenticate, login
from django.core.validators import validate_email
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from smartmin.views import *
from .models import Org, OrgCache, OrgEvent, TopUp, MT_SMS_EVENTS, MO_SMS_EVENTS, MT_CALL_EVENTS, MO_CALL_EVENTS, ALARM_EVENTS, Invitation, UserSettings, Language
from .bundles import BUNDLE_CHOICES, BUNDLES, WELCOME_TOPUP_SIZE
from temba.utils import analytics, build_json_response

from twilio.rest import TwilioRestClient


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
                if user.is_superuser:
                    return None
                return HttpResponseRedirect(reverse('orgs.org_choose'))
            else:
                return HttpResponseRedirect(settings.LOGIN_URL)

        return None

    def has_org_perm(self, permission):

        (app_label, codename) = permission.split(".")

        if self.get_user().is_superuser:
            return True

        if self.get_user().is_anonymous():
            return False

        if self.org:
            if self.get_user().get_org_group().permissions.filter(content_type__app_label=app_label, codename=codename):
                return True

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

        if 'HTTP_X_PJAX' in self.request.META and not 'HTTP_X_FORMAX' in self.request.META:  # pragma: no cover
            context['base_template'] = "smartmin/modal.html"
        if 'success_url' in kwargs:  # pragma: no cover
            context['success_url'] = kwargs['success_url']

        context['action_url'] = self.request.path + "?" + \
                                "&".join(urlquote(_) + "=" + urlquote(self.request.REQUEST[_]) for _ in self.request.REQUEST.keys() if _ != '_')

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
            errors = self.form._errors.setdefault(forms.forms.NON_FIELD_ERRORS, forms.util.ErrorList())
            errors.append(message)
            return self.render_to_response(self.get_context_data(form=form))


class OrgSignupForm(forms.ModelForm):
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
                org_users = org.administrators.all() | org.editors.all() | org.viewers.all()

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
            except:  # pragma: no cover
                raise forms.ValidationError(_("Invalid phone number, try again."))
            return phonenumbers.format_number(normalized, phonenumbers.PhoneNumberFormat.E164)
        return None

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
    actions = ('signup', 'home', 'webhook', 'edit', 'join', 'grant', 'create_login', 'choose', 'manage_accounts',
               'create', 'manage', 'update', 'country', 'languages', 'clear_cache',
               'twilio_connect', 'twilio_account', 'nexmo_account', 'nexmo_connect', 'export', 'import')

    model = Org

    class Import(InferOrgMixin, OrgPermsMixin, SmartFormView):

        success_message = _("Import successful")

        def get_success_url(self):
            return reverse('orgs.org_home')

        class FlowImportForm(Form):
            import_file = forms.FileField(help_text=_('The import file'))
            update = forms.BooleanField(help_text=_('Update all flows and campaigns'), required=False)

        form_class = FlowImportForm

        def form_valid(self, form):
            try:
                # block import based on number of credits
                org = self.request.user.get_org()
                if not org.has_added_credits():
                    form._errors['import_file'] = form.error_class([_("Sorry, import is a premium feature")])
                    return self.form_invalid(form)

                data = json.loads(form['import_file'].value().read())
                org.import_app(data, self.request.user, self.request.branding['link'])
            except ValueError as e:
                form._errors['import_file'] = form.error_class([_("This file is no longer valid. Please export a new version and try again.")])
                return self.form_invalid(form)
            except Exception as e:
                form._errors['import_file'] = form.error_class([_("Sorry, your import file is invalid.")])
                return self.form_invalid(form)

            return super(OrgCRUDL.Import, self).form_valid(form)

    class Export(InferOrgMixin, OrgPermsMixin, SmartFormView):

        def post(self, request, *args, **kwargs):

            # get all of the selected flows and campaigns
            from temba.flows.models import Flow
            from temba.campaigns.models import Campaign

            flows = set(Flow.objects.filter(id__in=self.request.REQUEST.getlist('flows'), org=self.get_object()))
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
                    nodes = set([node])
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

    class TwilioAccount(ModalMixin, InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        fields = ()
        success_url = '@channels.channel_claim'
        submit_button_name = "Disconnect Twilio"
        success_message = "Twilio Account successfully disconnected."

        def save(self, obj):
            obj.remove_twilio_account()

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.TwilioAccount, self).get_context_data(**kwargs)

            org = self.get_object()
            config = org.config_json()

            context['config'] = config

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
                except:
                    raise ValidationError(_("The Twilio account SID and Token seem invalid. Please check them again and retry."))

                return self.cleaned_data

        form_class = TwilioConnectForm
        submit_button_name = "Save"
        success_url = '@channels.channel_claim_number'
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
                except:
                    raise ValidationError(_("Your Nexmo API key and secret seem invalid. Please check them again and retry."))

                return self.cleaned_data

        form_class = NexmoConnectForm
        submit_button_name = "Save"
        success_url = '@channels.channel_claim_nexmo'
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

    class Manage(SmartListView):
        fields = ('credits', 'used', 'name', 'owner', 'created_on')
        default_order = ('-credits', '-created_on',)
        search_fields = ('name__icontains', 'created_by__email__iexact')
        link_fields = ('name', 'owner')
        title = "Organizations"

        def get_paid(self, obj):
            return "$%s" % (obj.paid / 100)

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
            url = reverse('users.user_mimic', args=[obj.created_by.pk])
            return "<a href='%s' class='login btn btn-default btn-tiny'>Login</a><div class='owner-name'>%s %s</div><div class='owner-email'>%s</div>" % (url, obj.created_by.first_name, obj.created_by.last_name, obj.created_by)

        def get_name(self, obj):
            return "<div class='org-name'>%s</div><div class='org-timezone'>%s</div>" % (obj.name, obj.timezone)

        def derive_queryset(self, **kwargs):
            queryset = super(OrgCRUDL.Manage, self).derive_queryset(**kwargs)
            queryset = queryset.filter(is_active=True)
            queryset = queryset.annotate(credits=Sum('topups__credits'))
            queryset = queryset.annotate(paid=Sum('topups__price'))
            return queryset

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.Manage, self).get_context_data(**kwargs)
            context['searches'] = ['Nyaruka', 'Unicef', 'Boston University', 'JH', 'University of Chicago']
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
            administrators = forms.ModelMultipleChoiceField(User.objects.all(), required=False)

            class Meta:
                model = Org

        form_class = OrgUpdateForm
        success_url = '@orgs.org_manage'

    class ManageAccounts(InferOrgMixin, OrgPermsMixin, SmartUpdateView):

        class InviteForm(forms.ModelForm):
            emails = forms.CharField(label=_("Invite people to your organization"), required=False)
            user_group = forms.ChoiceField(choices=(('A', _("Administrators")), ('E', _("Editors")), ('V', _("Viewers"))),
                                           required=True, initial='V', label=_("User group"))

            def clean_emails(self):
                emails = self.cleaned_data['emails'].lower().strip()
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
                fields = ('emails', 'user_group')


        form_class = InviteForm
        success_url = "@orgs.org_home"
        success_message = ""
        GROUP_LEVELS = ('administrators', 'editors', 'viewers')

        def derive_title(self):
            return _("Manage %(name)s Accounts") % {'name':self.get_object().name}

        def add_check_fields(self, form, objects, org_id, field_dict):
            for obj in objects:
                fields = []

                field_mapping = []
                for grp_level in self.GROUP_LEVELS:
                    check_field = forms.BooleanField(required=False)
                    field_name = "%s_%d" % (grp_level, obj.id)

                    field_mapping.append((field_name, check_field))
                    fields.append(field_name)

                # as of django 1.7 we can't insert into fields, construct a new OrderedDict
                form.fields = OrderedDict(form.fields.items() + field_mapping)
                field_dict[obj] = fields

        def derive_initial(self):
            self.org_users = self.get_object().get_org_users()

            initial = dict()
            for grp_level in self.GROUP_LEVELS:
                if grp_level == 'administrators':
                    assigned_users = self.get_object().get_org_admins()
                if grp_level == 'editors':
                    assigned_users = self.get_object().get_org_editors()
                if grp_level == 'viewers':
                    assigned_users = self.get_object().get_org_viewers()

                for obj in assigned_users:
                    key = "%s_%d" % (grp_level, obj.id)
                    initial[key] = True

            return initial

        def get_form(self, form_class):
            form = super(OrgCRUDL.ManageAccounts, self).get_form(form_class)
            self.group_fields = dict()
            self.add_check_fields(form, self.org_users, self.get_object().pk, self.group_fields)

            return form

        def post_save(self, obj):
            obj = super(OrgCRUDL.ManageAccounts, self).post_save(obj)

            cleaned_data = self.form.cleaned_data
            user = self.request.user
            org = self.get_object()

            user_group = cleaned_data['user_group']

            emails = cleaned_data['emails'].lower().strip()
            email_list = emails.split(',')

            host = self.request.branding['host']

            if emails:
                for email in email_list:

                    # if they already have an invite, update it
                    invites = Invitation.objects.filter(email=email, org=org).order_by('-pk')
                    invitation = invites.first()

                    if invitation:

                        # remove any old invites
                        invites.exclude(pk=invitation.pk).delete()

                        invitation.user_group = user_group
                        invitation.is_active = True
                        invitation.save()
                    else:
                        invitation = Invitation.objects.create(email=email,
                                                               org=org,
                                                               host=host,
                                                               user_group=user_group,
                                                               created_by=user,
                                                               modified_by=user)

                    invitation.send_invitation()

            # remove all the org users
            for user in self.get_object().get_org_admins():
                if user != self.request.user:
                    self.get_object().administrators.remove(user)
                else:
                    self.get_object().administrators.add(user)
            for user in self.get_object().get_org_editors():
                self.get_object().editors.remove(user)
            for user in self.get_object().get_org_viewers():
                self.get_object().viewers.remove(user)
        
            # now update the org accounts
            for field in self.form.fields:
                if self.form.cleaned_data[field]:
                    matcher = re.match("(\w+)_(\d+)", field)
                    if matcher:
                        user_type = matcher.group(1)
                        user_id = matcher.group(2)
                        user = User.objects.get(pk=user_id)
                        if user_type == 'administrators':
                            self.get_object().administrators.add(user)
                        if user_type == 'editors':
                            self.get_object().editors.add(user)
                        if user_type == 'viewers':
                            self.get_object().viewers.add(user)

            # update our org users after we've removed them
            self.org_users = self.get_object().get_org_users()

            return obj

        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.ManageAccounts, self).get_context_data(**kwargs)
            org = self.get_object()
            context['org'] = org
            context['org_users'] = self.org_users
            context['group_fields'] = self.group_fields
            context['invites'] = Invitation.objects.filter(org=org, is_active=True)

            return context

    class Choose(SmartFormView):
        class ChooseForm(forms.Form):
            organization = forms.ModelChoiceField(queryset=Org.objects.all() ,empty_label=None)
        
        form_class = ChooseForm
        success_url = '@msgs.msg_inbox'
        fields = ('organization',)
        title = _("Select your Organization")

        def pre_process(self, request, *args, **kwargs):
            if self.request.user.is_authenticated():
                user_orgs = self.request.user.get_user_orgs()

                if self.request.user.is_superuser:
                    return HttpResponseRedirect(reverse('orgs.org_manage'))

                elif not user_orgs:
                    messages.info(request, _("Your account is not associated to an organization. Please create an organization."))
                    return HttpResponseRedirect(reverse('orgs.org_create'))

                elif user_orgs.count() == 1:
                    org = user_orgs[0]
                    self.request.session['org_id'] = org.pk
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
            secret = self.kwargs.get('secret')
            
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

            invitation = self.get_invitation()
            
            # log the user in
            user = authenticate(username=user.username, password=self.form.cleaned_data['password'])
            login(self.request, user)
            if invitation.user_group == 'A':
                obj.administrators.add(user)
            elif invitation.user_group == 'E':
                obj.editors.add(user)
            else:
                obj.viewers.add(user)

            # make the invitation inactive
            invitation.is_active = False
            invitation.save()

            return obj

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
            return _("Join %(name)s") % {'name':org.name}

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
            return _("Join %(name)s") % {'name':org.name}

        def save(self, org):
            org = self.get_object()
            invitation = self.get_invitation()
            if org:
                if invitation.user_group == 'A':
                    org.administrators.add(self.request.user)
                elif invitation.user_group == 'E':
                    org.editors.add(self.request.user)
                else:
                    org.viewers.add(self.request.user)
                    
                # make the invitation inactive
                invitation.is_active = False
                invitation.save()

                # set the active org on this user
                self.request.user.set_org(org)
                self.request.session['org_id'] = org.pk

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
            
        def get_context_data(self, **kwargs):
            context = super(OrgCRUDL.Join, self).get_context_data(**kwargs)

            context['org'] = self.get_object()
            return context

    class Create(SmartCreateView):
        title = _("Create Your Organization")
        form_class = OrgSignupForm
        fields = ('name', 'timezone')
        success_message = ''

        def get_success_url(self):
            return "%s?start" % reverse('public.public_welcome')

        def pre_save(self, obj):
            obj = super(OrgCRUDL.Create, self).pre_save(obj)

            user = self.request.user
            obj.created_by = user
            obj.modified_by = user

            slug = Org.get_unique_slug(self.form.cleaned_data['name'])
            obj.slug = slug

            return obj

        def post_save(self, obj):
            obj = super(OrgCRUDL.Create, self).post_save(obj)
            obj.administrators.add(self.request.user)

            self.request.session['org_id'] = obj.pk

            return obj

        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated()

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

            return obj

        def get_welcome_size(self):
            return self.form.cleaned_data['credits']

        def post_save(self, obj):
            obj = super(OrgCRUDL.Grant, self).post_save(obj)
            obj.administrators.add(self.user)

            if not self.request.user.is_anonymous():
                obj.administrators.add(self.request.user.pk)

            obj.create_sample_flows()
            obj.create_welcome_topup(self.user, self.get_welcome_size())

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
            mt_sms = forms.BooleanField(required=False, label=_("Incoming SMS"))
            mo_sms = forms.BooleanField(required=False, label=_("Outgoing SMS"))
            mt_call = forms.BooleanField(required=False, label=_("Incoming Calls"))
            mo_call = forms.BooleanField(required=False, label=_("Outgoing Calls"))
            alarm = forms.BooleanField(required=False, label=_("Channel Alarms"))

            class Meta:
                model = Org

        form_class = WebhookForm
        fields = ('webhook', 'mt_sms', 'mo_sms', 'mt_call', 'mo_call', 'alarm')
        success_url = '@orgs.org_home'
        success_message = ''

        def pre_save(self, obj):
            obj = super(OrgCRUDL.Webhook, self).pre_save(obj)
            data = self.form.cleaned_data

            webhook_events = 0
            if data['mt_sms']: webhook_events = MT_SMS_EVENTS
            if data['mo_sms']: webhook_events |= MO_SMS_EVENTS
            if data['mt_call']: webhook_events |= MT_CALL_EVENTS
            if data['mo_call']: webhook_events |= MO_CALL_EVENTS
            if data['alarm']: webhook_events |= ALARM_EVENTS

            analytics.track(self.request.user.username, 'temba.org_configured_webhook')

            obj.webhook_events = webhook_events
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
            from temba.channels.views import get_channel_icon
            icon = get_channel_icon(channel.channel_type)

            if self.has_org_perm('channels.channel_read'):
                formax.add_section('channel', reverse('channels.channel_read', args=[channel.pk]), icon=icon, action='link')

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
                formax.add_section('manageaccount', reverse('orgs.org_manage_accounts'), icon='icon-users')

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
            country = forms.ModelChoiceField(Org.get_possible_countries(), required=False,
                                      label=_("The country used for location values. (optional)"),
                                      help_text="State and district names will be searched against this country.")

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
            primary_lang = forms.CharField(required=True, label=_('Primary Language'), help_text=_('The primary language will be used for contacts with no language preference.'))
            languages = forms.CharField(required=False, label=_('Additional Languages'), help_text=('Add any other languages you would like to provide translations for.'))

            def __init__(self, *args, **kwargs):
                self.org = kwargs['org']
                del kwargs['org']
                super(OrgCRUDL.Languages.LanguagesForm, self).__init__(*args, **kwargs)

            def clean(self):

                # don't allow them to remove languages which are a base_language for a flow
                old_languages = [lang.iso_code for lang in self.org.languages.all()]

                new_languages = set(self.cleaned_data.get('languages', '').split(',') + self.cleaned_data.get('primary_lang', '').split(','))

                for new_lang in new_languages:
                    if new_lang in old_languages:
                        old_languages.remove(new_lang)

                # now check if any flows are using any of the old languages to be removed
                dependency = Flow.objects.filter(org=self.org, is_active=True, base_language__in=old_languages).first()
                if dependency:
                    lang = Language.objects.filter(iso_code=dependency.base_language).first()
                    raise ValidationError(_("%s cannot be removed since it is the base language for %s" % (lang.name, dependency.name)))

                return super(OrgCRUDL.Languages.LanguagesForm, self).clean()

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
                            lang = pycountry.languages.get(bibliographic=iso_code)
                            name = lang.name.split(';')[0]
                            matches.append(dict(id=lang.bibliographic, text=name))

                if len(matches) == 0:
                    search = self.request.REQUEST.get('search', '').strip().lower()
                    for lang in pycountry.languages:
                        if len(search) == 0 or search in lang.name.lower():
                            matches.append(dict(id=lang.bibliographic, text=lang.name))

                results = dict(results=matches)
                return build_json_response(results)
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
                    lang = pycountry.languages.get(bibliographic=iso_code)
                    language = org.languages.filter(iso_code=iso_code).first()
                    if lang and not language:
                        # store up to the first semicolon as the name
                        name = lang.name.split(';')[0]

                        print "creating %s" % iso_code
                        language = org.languages.create(created_by=user, modified_by=user, iso_code=iso_code, name=name)

                    # store our primary language
                    if iso_code == primary:
                        self.object.primary_language = language
                        self.object.save(update_fields=['primary_language'])

            # remove any languages that are not in our new list
            org.languages.exclude(iso_code__in=iso_codes).delete()

            return super(OrgCRUDL.Languages, self).form_valid(form)

        def has_permission(self, request, *args, **kwargs):
            self.org = self.derive_org()
            return self.request.user.has_perm('orgs.org_country') or self.has_org_perm('orgs.org_country')

    class ClearCache(SmartUpdateView):
        fields = ('id',)
        success_message = None
        success_url = 'id@orgs.org_update'

        def pre_process(self, request, *args, **kwargs):
            cache = OrgCache(int(request.REQUEST['cache']))
            num_deleted = self.get_object().clear_caches([cache])
            self.success_message = _("Cleared %s cache for this organization (%d keys)") % (cache.name, num_deleted)


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
            context['now'] = timezone.now()
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
