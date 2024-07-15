import contextlib
from uuid import UUID

import iso8601
from rest_framework import generics, mixins, status
from rest_framework.response import Response

from django.db import transaction

from temba import mailroom
from temba.api.support import InvalidQueryError
from temba.contacts.models import URN
from temba.utils.models import TembaModel
from temba.utils.views import NonAtomicMixin

from .models import BulkActionFailure


class BaseAPIView(NonAtomicMixin, generics.GenericAPIView):
    """
    Base class of all our API endpoints
    """

    model = None
    model_manager = "objects"
    lookup_params = {"uuid": "uuid"}

    def derive_queryset(self):
        return getattr(self.model, self.model_manager).filter(org=self.request.org)

    def get_queryset(self):
        qs = self.derive_queryset()

        # if this is a get request, fetch from readonly database
        if self.request.method == "GET":
            qs = qs.using("readonly")

        return qs

    def get_lookup_values(self):
        """
        Extracts lookup_params from the request URL, e.g. {"uuid": "123..."}
        """
        lookup_values = {}
        for param, field in self.lookup_params.items():
            if param in self.request.query_params:
                param_value = self.request.query_params[param]

                # try to normalize URN lookup values
                if param == "urn":
                    param_value = self.normalize_urn(param_value)

                lookup_values[field] = param_value

        if len(lookup_values) > 1:
            raise InvalidQueryError(
                "URL can only contain one of the following parameters: " + ", ".join(sorted(self.lookup_params.keys()))
            )

        return lookup_values

    def get_object(self):
        queryset = self.get_queryset().filter(**self.lookup_values)

        return generics.get_object_or_404(queryset)

    def get_int_param(self, name):
        param = self.request.query_params.get(name)
        try:
            return int(param) if param is not None else None
        except ValueError:
            raise InvalidQueryError("Value for %s must be an integer" % name)

    def get_uuid_param(self, name):
        param = self.request.query_params.get(name)
        try:
            return UUID(param) if param is not None else None
        except ValueError:
            raise InvalidQueryError("Value for %s must be a valid UUID" % name)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["org"] = self.request.org
        context["user"] = self.request.user
        return context

    def normalize_urn(self, value):
        org = self.request.org

        if org.is_anon:
            raise InvalidQueryError("URN lookups not allowed for anonymous organizations")

        try:
            return URN.identity(URN.normalize(value, country_code=org.default_country_code))
        except ValueError:
            raise InvalidQueryError("Invalid URN: %s" % value)

    def is_docs(self):
        return "format" not in self.kwargs


class ListAPIMixin(mixins.ListModelMixin):
    """
    Mixin for any endpoint which returns a list of objects from a GET request
    """

    exclusive_params = ()

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        self.check_query(self.request.query_params)

        if self.is_docs():
            # if this is just a request to browse the endpoint docs, don't make a query
            return Response([])
        else:
            return super().list(request, *args, **kwargs)

    def check_query(self, params):
        # check user hasn't provided values for more than one of any exclusive params
        if sum([(1 if params.get(p) else 0) for p in self.exclusive_params]) > 1:
            raise InvalidQueryError("You may only specify one of the %s parameters" % ", ".join(self.exclusive_params))

    def filter_before_after(self, queryset, field):
        """
        Filters the queryset by the before/after params if are provided
        """
        before = self.request.query_params.get("before")
        if before:
            try:
                before = iso8601.parse_date(before)
                queryset = queryset.filter(**{field + "__lte": before})
            except ValueError:
                queryset = queryset.filter(pk=-1)

        after = self.request.query_params.get("after")
        if after:
            try:
                after = iso8601.parse_date(after)
                queryset = queryset.filter(**{field + "__gte": after})
            except ValueError:
                queryset = queryset.filter(pk=-1)

        return queryset

    def paginate_queryset(self, queryset):
        page = super().paginate_queryset(queryset)

        # give views a chance to prepare objects for serialization
        self.prepare_for_serialization(page, using=queryset.db)

        return page

    def prepare_for_serialization(self, page, using: str):
        """
        Views can override this to do things like bulk cache initialization of result objects
        """
        pass


class WriteAPIMixin:
    """
    Mixin for any endpoint which can create or update objects with a write serializer. Our approach differs a bit from
    the REST framework default way as we use POST requests for both create and update operations, and use separate
    serializers for reading and writing.
    """

    write_serializer_class = None
    write_with_transaction = True

    def post_save(self, instance):
        """
        Can be overridden to add custom handling after object creation
        """
        pass

    def post(self, request, *args, **kwargs):
        self.lookup_values = self.get_lookup_values()

        # determine if this is an update of an existing object or a create of a new object
        if self.lookup_values:
            instance = self.get_object()
            if self.is_system_instance(instance):
                return Response({"detail": "Cannot modify system object."}, status=status.HTTP_403_FORBIDDEN)
        else:
            instance = None

            if issubclass(self.model, TembaModel):
                org_count, org_limit = self.model.get_org_limit_progress(request.org)
                if org_limit is not None and org_count >= org_limit:
                    return Response(
                        {"detail": f"Cannot create object because workspace has reached limit of {org_limit}."},
                        status=status.HTTP_409_CONFLICT,
                    )

        context = self.get_serializer_context()
        context["lookup_values"] = self.lookup_values
        context["instance"] = instance

        serializer = self.write_serializer_class(instance=instance, data=request.data, context=context)

        if serializer.is_valid():
            mgr = transaction.atomic() if self.write_with_transaction else contextlib.suppress()
            with mgr:
                try:
                    output = serializer.save()
                except mailroom.URNValidationException as e:
                    return Response(serializer.urn_exception(e), status=status.HTTP_400_BAD_REQUEST)

                self.post_save(output)
                return self.render_write_response(output, context)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def is_system_instance(self, obj):
        return obj.is_system if isinstance(obj, TembaModel) else False

    def render_write_response(self, write_output, context):
        response_serializer = self.serializer_class(instance=write_output, context=context)

        # if we're also a list view, we can re-use any serialization preparation it uses
        if hasattr(self, "prepare_for_serialization"):
            self.prepare_for_serialization([write_output], using="default")

        # if we created a new object, notify caller by returning 201
        status_code = status.HTTP_200_OK if context["instance"] else status.HTTP_201_CREATED

        return Response(response_serializer.data, status=status_code)


class BulkWriteAPIMixin:
    """
    Mixin for a bulk action endpoint which writes multiple objects in response to a POST but returns nothing.
    """

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context=self.get_serializer_context())

        if serializer.is_valid():
            result = serializer.save()
            if isinstance(result, BulkActionFailure):
                return Response(result.as_json(), status.HTTP_200_OK)
            else:
                return Response("", status=status.HTTP_204_NO_CONTENT)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class DeleteAPIMixin(mixins.DestroyModelMixin):
    """
    Mixin for any endpoint that can delete objects with a DELETE request
    """

    def delete(self, request, *args, **kwargs):
        self.lookup_values = self.get_lookup_values()

        if not self.lookup_values:
            raise InvalidQueryError(
                "URL must contain one of the following parameters: " + ", ".join(sorted(self.lookup_params.keys()))
            )

        instance = self.get_object()
        if self.is_system_instance(instance):
            return Response({"detail": "Cannot delete system object."}, status=status.HTTP_403_FORBIDDEN)

        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def perform_destroy(self, instance):
        instance.release(self.request.user)
