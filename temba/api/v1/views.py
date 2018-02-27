# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six
from six.moves.urllib.parse import urlencode

from django import forms
from django.contrib.auth import authenticate, login
from django.core.cache import cache
from django.db.models import Prefetch
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework import generics, mixins, status, pagination, views
from rest_framework.response import Response
from rest_framework.reverse import reverse
from smartmin.views import SmartFormView
from temba.api.models import APIToken
from temba.contacts.models import Contact, ContactField, ContactGroup, TEL_SCHEME
from temba.flows.models import Flow, FlowRun
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.utils import splitting_getlist, str_to_bool
from temba.utils.dates import json_date_to_datetime
from ..models import APIPermission, SSLPermission
from .serializers import BoundarySerializer, AliasSerializer, ContactReadSerializer, ContactWriteSerializer
from .serializers import ContactFieldReadSerializer, ContactFieldWriteSerializer, FlowReadSerializer
from .serializers import FlowRunReadSerializer, FlowRunWriteSerializer

# caching of counts from API requests
REQUEST_COUNT_CACHE_KEY = 'org:%d:cache:api_request_counts:%s'
REQUEST_COUNT_CACHE_TTL = 5 * 60  # 5 minutes


class RootView(views.APIView):
    pass


class AuthenticateEndpoint(SmartFormView):

    class LoginForm(forms.Form):
        email = forms.CharField()
        password = forms.CharField()
        role = forms.CharField()

    form_class = LoginForm

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(AuthenticateEndpoint, self).dispatch(*args, **kwargs)

    def form_valid(self, form, *args, **kwargs):
        username = form.cleaned_data.get('email')
        password = form.cleaned_data.get('password')
        role_code = form.cleaned_data.get('role')

        user = authenticate(username=username, password=password)
        if user and user.is_active:
            login(self.request, user)

            role = APIToken.get_role_from_code(role_code)
            orgs = []

            if role:
                valid_orgs = APIToken.get_orgs_for_role(user, role)

                for org in valid_orgs:
                    token = APIToken.get_or_create(org, user, role)
                    orgs.append(dict(id=org.pk, name=org.name, token=token.key))
            else:  # pragma: needs cover
                return HttpResponse(status=403)

            return JsonResponse(orgs, safe=False)
        else:  # pragma: needs cover
            return HttpResponse(status=403)


class BaseAPIView(generics.GenericAPIView):
    """
    Base class of all our API endpoints
    """
    permission_classes = (SSLPermission, APIPermission)


class ListAPIMixin(mixins.ListModelMixin):
    """
    Mixin for any endpoint which returns a list of objects from a GET request
    """
    pagination_class = pagination.PageNumberPagination
    cache_counts = False

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        if not kwargs.get('format', None):
            # if this is just a request to browse the endpoint docs, don't make a query
            return Response([])
        else:
            return super(ListAPIMixin, self).list(request, *args, **kwargs)

    def paginate_queryset(self, queryset):
        if self.cache_counts:
            # total counts can be expensive so we let some views cache counts based on the query parameters
            query_params = self.request.query_params.copy()
            if 'page' in query_params:
                del query_params['page']

            # param values should be in UTF8
            encoded_params = [(p[0], [v.encode('utf-8') for v in p[1]]) for p in query_params.lists()]

            query_key = urlencode(sorted(encoded_params), doseq=True)
            count_key = REQUEST_COUNT_CACHE_KEY % (self.request.user.get_org().pk, query_key)

            # only try to use cached count for pages other than the first
            if int(self.request.query_params.get('page', 1)) != 1:
                cached_count = cache.get(count_key)
                if cached_count is not None:
                    queryset.count = lambda: int(cached_count)  # monkey patch the queryset count() method

            object_list = self.paginator.paginate_queryset(queryset, self.request, view=self)

            # actual count (cached or calculated) is stored on the Django paginator rather than the REST paginator
            actual_count = int(self.paginator.page.paginator.count)

            # reset the cached value
            cache.set(count_key, actual_count, REQUEST_COUNT_CACHE_TTL)
        else:
            object_list = self.paginator.paginate_queryset(queryset, self.request, view=self)

        # give views a chance to prepare objects for serialization
        self.prepare_for_serialization(object_list)

        return object_list

    def prepare_for_serialization(self, object_list):
        """
        Views can override this to do things like bulk cache initialization of result objects
        """
        pass


class CreateAPIMixin(object):
    """
    Mixin for any endpoint which can create or update objects with a write serializer. Our list and create approach
    differs slightly a bit from ListCreateAPIView in the REST framework as we use separate read and write serializers...
    and sometimes we use another serializer again for write output
    """
    write_serializer_class = None

    def post(self, request, *args, **kwargs):
        user = request.user
        context = self.get_serializer_context()
        serializer = self.write_serializer_class(user=user, data=request.data, context=context)

        if serializer.is_valid():
            output = serializer.save()
            return self.render_write_response(output, context)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def render_write_response(self, write_output, context):
        response_serializer = self.serializer_class(instance=write_output, context=context)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


class ContactEndpoint(ListAPIMixin, CreateAPIMixin, BaseAPIView):
    """
    ## Adding a Contact

    You can add a new contact to your account, or update the fields on a contact by sending a **POST** request to this
    URL with the following JSON data:

    * **uuid** - the UUID of the contact to update (string) (optional, new contact created if not present)
    * **name** - the full name of the contact (string, optional)
    * **language** - the preferred language for the contact (3 letter iso code, optional)
    * **urns** - a list of URNs you want associated with the contact (string array)
    * **group_uuids** - a list of the UUIDs of any groups this contact is part of (string array, optional)
    * **fields** - a JSON dictionary of contact fields you want to set or update on this contact (JSON, optional)

    Example:

        POST /api/v1/contacts.json
        {
            "name": "Ben Haggerty",
            "language": "eng",
            "urns": ["tel:+250788123123", "twitter:ben"],
            "group_uuids": ["6685e933-26e1-4363-a468-8f7268ab63a9", "1281f10a-d5b3-4580-a0fe-92adb97c2d1a"],
            "fields": {
              "nickname": "Macklemore",
              "side_kick": "Ryan Lewis"
            }
        }

    You will receive a contact object as a response if successful:

        {
            "uuid": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
            "name": "Ben Haggerty",
            "language": "eng",
            "urns": ["tel:+250788123123", "twitter:ben"],
            "group_uuids": ["6685e933-26e1-4363-a468-8f7268ab63a9", "1281f10a-d5b3-4580-a0fe-92adb97c2d1a"],
            "fields": {
              "nickname": "Macklemore",
              "side_kick": "Ryan Lewis"
            }
            "blocked": false,
            "failed": false
        }

    ## Updating Contacts

    You can update contacts in the same manner as adding them but we recommend you pass in the UUID for the contact
    as a way of specifying which contact to update. Note that when you pass in the contact UUID and ```urns```, all
    existing URNs will be evaluated against this new set and updated accordingly.

    ## Listing Contacts

    A **GET** returns the list of contacts for your organization, in the order of last activity date. You can return
    only deleted contacts by passing the "?deleted=true" parameter to your call.

    * **uuid** - the unique identifier for this contact (string) (filterable: ```uuid``` repeatable)
    * **name** - the name of this contact (string, optional)
    * **language** - the preferred language of this contact (string, optional)
    * **urns** - the URNs associated with this contact (string array) (filterable: ```urns```)
    * **group_uuids** - the UUIDs of any groups this contact is part of (string array, optional) (filterable: ```group_uuids``` repeatable)
    * **fields** - any contact fields on this contact (JSON, optional)
    * **after** - only contacts which have changed on this date or after (string) ex: 2012-01-28T18:00:00.000
    * **before** - only contacts which have been changed on this date or before (string) ex: 2012-01-28T18:00:00.000

    Example:

        GET /api/v1/contacts.json

    Response containing the contacts for your organization:

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
            {
                "uuid": "09d23a05-47fe-11e4-bfe9-b8f6b119e9ab",
                "name": "Ben Haggerty",
                "language": null,
                "urns": [
                  "tel:+250788123123"
                ],
                "group_uuids": ["6685e933-26e1-4363-a468-8f7268ab63a9", "1281f10a-d5b3-4580-a0fe-92adb97c2d1a"],
                "fields": {
                  "nickname": "Macklemore",
                  "side_kick": "Ryan Lewis"
                }
            }]
        }
    """
    permission = 'orgs.org_surveyor'
    model = Contact
    serializer_class = ContactReadSerializer
    write_serializer_class = ContactWriteSerializer
    cache_counts = True

    def get_base_queryset(self, request):
        queryset = self.model.objects.filter(org=request.user.get_org(), is_test=False)

        # if they pass in deleted=true then only return deleted contacts
        if str_to_bool(request.query_params.get('deleted', '')):
            return queryset.filter(is_active=False)
        else:
            return queryset.filter(is_active=True)

    def get_queryset(self):
        queryset = self.get_base_queryset(self.request)

        before = self.request.query_params.get('before', None)
        if before:
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(modified_on__lte=before)
            except Exception:  # pragma: needs cover
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(modified_on__gte=after)
            except Exception:  # pragma: needs cover
                queryset = queryset.filter(pk=-1)

        phones = splitting_getlist(self.request, 'phone')  # deprecated, use urns
        if phones:
            queryset = queryset.filter(urns__path__in=phones, urns__scheme=TEL_SCHEME)

        urns = self.request.query_params.getlist('urns', None)
        if urns:
            queryset = queryset.filter(urns__identity__in=urns)

        groups = self.request.query_params.getlist('group', None)  # deprecated, use group_uuids
        if groups:
            queryset = queryset.filter(all_groups__name__in=groups,
                                       all_groups__group_type=ContactGroup.TYPE_USER_DEFINED)

        group_uuids = self.request.query_params.getlist('group_uuids', None)
        if group_uuids:
            queryset = queryset.filter(all_groups__uuid__in=group_uuids,
                                       all_groups__group_type=ContactGroup.TYPE_USER_DEFINED)

        uuids = self.request.query_params.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        # can't prefetch a custom manager directly, so here we prefetch user groups as new attribute
        user_groups_prefetch = Prefetch('all_groups', queryset=ContactGroup.user_groups.all(), to_attr='prefetched_user_groups')

        return queryset.select_related('org').prefetch_related(user_groups_prefetch).order_by('-modified_on', 'pk')

    def prepare_for_serialization(self, object_list):
        # initialize caches of all contact fields and URNs
        org = self.request.user.get_org()
        Contact.bulk_cache_initialize(org, object_list)

    def get_serializer_context(self):
        """
        So that we only fetch active contact fields once for all contacts
        """
        context = super(BaseAPIView, self).get_serializer_context()
        context['contact_fields'] = ContactField.objects.filter(org=self.request.user.get_org(), is_active=True)
        return context


class FieldEndpoint(ListAPIMixin, CreateAPIMixin, BaseAPIView):
    """
    ## Listing Fields

    A **GET** returns the list of fields for your organization.

    * **key** - the unique key of this field (string) (filterable: ```key```)
    * **label** - the display label of this field (string)
    * **value_type** - one of the following strings:

             T - Text
             N - Decimal Number
             D - Datetime
             S - State
             I - District

    Example:

        GET /api/v1/fields.json

    Response containing the groups for your organization:

        {
            "count": 1,
            "next": null,
            "previous": null,
            "results": [
                {
                    "key": "nick_name",
                    "label": "Nick name",
                    "value_type": "T"
                },
                ...
            ]
        }

    ## Adding a Field

    A **POST** can be used to create a new contact field. Don't specify a key as this will be generated for you.

    * **label** - the display label (string)
    * **value_type** - one of the value type codes (string)

    Example:

        POST /api/v1/fields.json
        {
            "label": "Nick name",
            "value_type": "T"
        }

    You will receive a field object (with the new field key) as a response if successful:

        {
            "key": "nick_name",
            "label": "Nick name",
            "value_type": "T"
        }

    ## Updating a Field

    A **POST** can also be used to update an existing field if you do specify it's key.

    * **key** - the unique field key
    * **label** - the display label (string)
    * **value_type** - one of the value type codes (string)

    Example:

        POST /api/v1/fields.json
        {
            "key": "nick_name",
            "label": "New label",
            "value_type": "T"
        }

    You will receive the updated field object as a response if successful:

        {
            "key": "nick_name",
            "label": "New label",
            "value_type": "T"
        }
    """
    permission = 'orgs.org_surveyor'
    model = ContactField
    serializer_class = ContactFieldReadSerializer
    write_serializer_class = ContactFieldWriteSerializer

    def get_queryset(self):
        queryset = self.model.objects.filter(org=self.request.user.get_org(), is_active=True)

        key = self.request.query_params.get('key', None)
        if key:  # pragma: needs cover
            queryset = queryset.filter(key__icontains=key)

        return queryset


class BoundaryEndpoint(ListAPIMixin, BaseAPIView):
    """
    This endpoint allows you to list the administrative boundaries for the country associated with your organization
    along with the simplified gps geometry for those boundaries in GEOJSON format.

    ## Listing Boundaries

    Returns the boundaries for your organization. You can return just the names of the boundaries and their aliases,
    without any coordinate information by passing "?aliases=true".

    **Note that this may be a very large dataset as it includes the simplified coordinates for each administrative boundary.
     It is recommended to cache the results on the client side.**

      * **name** - the name of the administrative boundary (string)
      * **boundary** - the internal id for this administrative boundary, this is a variation on the OSM id (string)
      * **parent** - the id of the containing parent of this administrative boundary, null if this boundary is a country (string)
      * **level** - the level of the boundary, 0 for country levels, 1 for state levels, 2 for district levels (int)
      * **geometry** - the geojson geometry for this boundary, this will usually be a MultiPolygon (geojson)

    Example:

        GET /api/v1/boundaries.json

    Response is a list of the boundaries on your account

        {
            "count": 809,
            "next": "/api/v1/boundaries/?page=2",
            "previous": null,
            "results": [
            {
                "name": "Aba North",
                "boundary": "R3713502",
                "parent": "R3713501",
                "level": 1,
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [
                            [
                                [
                                    7.5251021,
                                    5.0504713
                                ],
                                [
                                    7.5330272,
                                    5.0423498
                                ]
                            ]
                        ]
                    ]
                }
            },
            ...
        }

    """
    permission = 'orgs.org_surveyor'
    model = AdminBoundary

    def get_queryset(self):

        org = self.request.user.get_org()
        if not org.country:  # pragma: needs cover
            return []

        queryset = org.country.get_descendants(include_self=True).order_by('level', 'name')

        if self.request.GET.get('aliases'):
            queryset = queryset.prefetch_related(
                Prefetch('aliases', queryset=BoundaryAlias.objects.filter(org=org).order_by('name')),
            )

        return queryset.select_related('parent')

    def get_serializer_class(self):
        if self.request.GET.get('aliases'):
            return AliasSerializer
        else:
            return BoundarySerializer


class FlowDefinitionEndpoint(BaseAPIView):
    """
    This endpoint returns a flow definition given a flow uuid.

    ## Getting a flow definition

    Returns the flow definition for the given flow.

      * **uuid** - the UUID of the flow (string)

    Example:

        GET /api/v1/flow_definition.json?uuid=f14e4ff0-724d-43fe-a953-1d16aefd1c0b

    Response is a flow definition

        {
          metadata: {
            "name": "Water Point Survey",
            "uuid": "f14e4ff0-724d-43fe-a953-1d16aefd1c0b",
            "saved_on": "2015-09-23T00:25:50.709164Z",
            "revision":28,
            "expires":7880,
            "id":12712,
          },
          "version": 7,
          "flow_type": "S",
          "base_language": "eng",
          "entry": "87929095-7d13-4003-8ee7-4c668b736419",
          "action_sets": [
            {
              "y": 0,
              "x": 100,
              "destination": "32d415f8-6d31-4b82-922e-a93416d5aa0a",
              "uuid": "87929095-7d13-4003-8ee7-4c668b736419",
              "actions": [
                {
                  "msg": {
                    "eng": "What is your name?"
                  },
                  "type": "reply"
                }
              ]
            },
            ...
          ],
          "rule_sets": [
            {
              "uuid": "32d415f8-6d31-4b82-922e-a93416d5aa0a",
              "webhook_action": null,
              "rules": [
                {
                  "test": {
                    "test": "true",
                    "type": "true"
                  },
                  "category": {
                    "eng": "All Responses"
                  },
                  "destination": null,
                  "uuid": "5fa6e9ae-e78e-4e38-9c66-3acf5e32fcd2",
                  "destination_type": null
                }
              ],
              "webhook": null,
              "ruleset_type": "wait_message",
              "label": "Name",
              "operand": "@step.value",
              "finished_key": null,
              "y": 162,
              "x": 62,
              "config": {}
            },
            ...
          ]
          }
        }

    """
    permission = 'orgs.org_surveyor'
    model = Flow

    def get(self, request, *args, **kwargs):

        uuid = request.GET.get('uuid')
        flow = Flow.objects.filter(org=self.request.user.get_org(), is_active=True, uuid=uuid).first()

        if flow:
            # make sure we have the latest format
            flow.ensure_current_version()
            return Response(flow.as_json(), status=status.HTTP_200_OK)
        else:  # pragma: needs cover
            return Response(dict(error="Invalid flow uuid"), status=status.HTTP_400_BAD_REQUEST)


class FlowEndpoint(ListAPIMixin, BaseAPIView):
    """
    This endpoint allows you to list all the active flows on your account using the ```GET``` method.

    ## Listing Flows

    Returns the flows for your organization, listing the most recent flows first.

      * **uuid** - the UUID of the flow (string) (filterable: ```uuid``` repeatable)
      * **name** - the name of the flow (string)
      * **archived** - whether this flow is archived (boolean) (filterable: ```archived```)
      * **labels** - the labels for this flow (string array) (filterable: ```label``` repeatable)
      * **created_on** - the datetime when this flow was created (datetime) (filterable: ```before``` and ```after```)
      * **expires** - the time (in minutes) when this flow's inactive contacts will expire (integer)
      * **runs** - the total number of runs for this flow (integer)
      * **completed_runs** - the number of completed runs for this flow (integer)
      * **rulesets** - the rulesets on this flow, including their node UUID, ruleset type, and label

    Example:

        GET /api/v1/flows.json

    Response is a list of the flows on your account

        {
            "count": 12,
            "next": "/api/v1/flows/?page=2",
            "previous": null,
            "results": [
            {
                "uuid": "cf85cb74-a4e4-455b-9544-99e5d9125cfd",
                "archived": false,
                "expires": 720,
                "name": "Thrift Shop Status",
                "labels": [ "Polls" ],
                "runs": 3,
                "completed_runs": 0,
                "rulesets": [
                   {
                    "id": 17122,
                    "node": "fe594710-68fc-4cb5-bd85-c0c77e4caa45",
                    "ruleset_type": "wait_message",
                    "label": "Age"
                   },
                   {
                    "id": 17128,
                    "node": "fe594710-68fc-4cb5-bd85-c0c77e4caa45",
                    "ruleset_type": "wait_message",
                    "label": "Gender"
                   }
                ]
            },
            ...
        }

    """
    permission = 'orgs.org_surveyor'
    model = Flow
    serializer_class = FlowReadSerializer

    def get_queryset(self):
        queryset = self.model.objects.filter(org=self.request.user.get_org(), is_active=True).order_by('-created_on')

        uuids = self.request.query_params.getlist('uuid', None)
        if uuids:
            queryset = queryset.filter(uuid__in=uuids)

        ids = self.request.query_params.getlist('flow', None)  # deprecated, use uuid
        if ids:
            queryset = queryset.filter(pk__in=ids)

        before = self.request.query_params.get('before', None)
        if before:  # pragma: needs cover
            try:
                before = json_date_to_datetime(before)
                queryset = queryset.filter(created_on__lte=before)
            except Exception:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get('after', None)
        if after:  # pragma: needs cover
            try:
                after = json_date_to_datetime(after)
                queryset = queryset.filter(created_on__gte=after)
            except Exception:
                queryset = queryset.filter(pk=-1)

        label = self.request.query_params.getlist('label', None)
        if label:
            queryset = queryset.filter(labels__name__in=label)

        archived = self.request.query_params.get('archived', None)
        if archived is not None:
            queryset = queryset.filter(is_archived=str_to_bool(archived))

        flow_type = self.request.query_params.getlist('type', None)
        if flow_type:  # pragma: needs cover
            queryset = queryset.filter(flow_type__in=flow_type)

        return queryset.prefetch_related('labels')


class OrgEndpoint(BaseAPIView):
    """
    ## Viewing Current Organization

    A **GET** returns the details of your organization. There are no parameters.

    Example:

        GET /api/v1/org.json

    Response containing your organization:

        {
            "name": "Nyaruka",
            "country": "RW",
            "languages": ["eng", "fra"],
            "primary_language": "eng",
            "timezone": "Africa/Kigali",
            "date_style": "day_first",
            "anon": false
        }
    """
    permission = 'orgs.org_surveyor'

    def get(self, request, *args, **kwargs):
        org = request.user.get_org()

        data = dict(name=org.name,
                    country=org.get_country_code(),
                    languages=[l.iso_code for l in org.languages.order_by('iso_code')],
                    primary_language=org.primary_language.iso_code if org.primary_language else None,
                    timezone=six.text_type(org.timezone),
                    date_style=('day_first' if org.get_dayfirst() else 'month_first'),
                    anon=org.is_anon)

        return Response(data, status=status.HTTP_200_OK)

    @classmethod
    def get_read_explorer(cls):  # pragma: needs cover
        return dict(method="GET", title="View Current Org", url=reverse('api.v1.org'), slug='org-read', request="")


class FlowStepEndpoint(CreateAPIMixin, BaseAPIView):
    """
    This endpoint allows you to create flow runs and steps.

    ## Creating flow steps

    By making a ```POST``` request to the endpoint you can add a new steps to a flow run.

    * **flow** - the UUID of the flow (string)
    * **revision** - the revision of the flow that was executed (integer)
    * **contact** - the UUID of the contact (string)
    * **steps** - the new step objects (array of objects)
    * **started** - the datetime when the run was started
    * **completed** - whether the run is complete

    Example:

        POST /api/v1/steps.json
        {
            "flow": "f5901b62-ba76-4003-9c62-72fdacc1b7b7",
            "revision": 2,
            "contact": "cf85cb74-a4e4-455b-9544-99e5d9125cfd",
            "completed": true,
            "started": "2015-09-23T17:59:47.572Z"
            "steps": [
                {
                    "node": "32cf414b-35e3-4c75-8a78-d5f4de925e13",
                    "arrived_on": "2015-08-25T11:59:30.088Z",
                    "actions": [{"msg":"Hi Joe","type":"reply"}],
                    "errors": []
                }
            ]
        }

    Response is the updated or created flow run.
    """
    permission = 'orgs.org_surveyor'
    model = FlowRun
    serializer_class = FlowRunReadSerializer
    write_serializer_class = FlowRunWriteSerializer

    def render_write_response(self, write_output, context):
        response_serializer = FlowRunReadSerializer(instance=write_output, context=context)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
