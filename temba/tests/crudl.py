from abc import abstractmethod

from django.db.models import QuerySet
from django.forms import model_to_dict
from django.urls import reverse

from temba.orgs.models import User


class CRUDLTestMixin:
    def requestView(self, url, user, *, post_data=None, checks=(), choose_org=None, **kwargs):
        """
        Requests the given URL as a specific user and runs a set of checks
        """

        method = "POST" if post_data is not None else "GET"
        user_name = user.username if user else "anonymous"
        msg_prefix = f"{method} {url} as {user_name}"
        pre_msg_prefix = f"before {msg_prefix}"

        self.client.logout()
        if user:
            self.login(user, True, choose_org)

        for check in checks:
            check.pre_check(self, pre_msg_prefix)

        response = self.client.post(url, post_data, **kwargs) if method == "POST" else self.client.get(url, **kwargs)

        for check in checks:
            check.check(self, response, msg_prefix)

        return response

    def process_wizard(self, view_name, url, form_data):
        for step, data in form_data.items():
            if not data:
                break

            # prepends each field name with the step name
            data = {f"{step}-{key}": value for key, value in data.items()}
            response = self.client.post(url, {f"{view_name}-current_step": step, **data})
            if response.status_code == 200 and "form" in response.context and response.context["form"].errors:
                return response

            if response.status_code == 302:
                return response
        return response

    def assertRequestDisallowed(self, url, users: list):
        """
        Asserts that the given users cannot fetch the given URL
        """

        for user in users:
            self.requestView(url, user, checks=[LoginRedirectOr404()])

    def assertReadFetch(self, url, users: list, *, context_object=None, status=200, choose_org=None):
        """
        Asserts that the given users can fetch the given read page
        """

        checks = [StatusCode(status)]
        if context_object:
            checks.append(ContextObject(context_object))

        response = None
        for user in users:
            response = self.requestView(url, user, checks=checks, choose_org=choose_org)

        return response

    def assertListFetch(
        self, url, users, *, context_objects=None, context_object_count=None, status=200, choose_org=None
    ):
        checks = [StatusCode(status)]
        if context_objects is not None:
            checks.append(ContextObjectList(context_objects))
        elif context_object_count is not None:
            checks.append(ContextObjectCount(context_object_count))

        response = None
        for user in users:
            response = self.requestView(url, user, checks=checks, choose_org=choose_org)

        return response

    def assertCreateFetch(self, url, users, *, form_fields=(), status=200, choose_org=None):
        checks = [StatusCode(status), FormFields(form_fields)]
        if isinstance(form_fields, dict):
            checks.append(FormInitialValues(form_fields))

        response = None
        for user in users:
            response = self.requestView(url, user, checks=checks, choose_org=choose_org)

        return response

    def assertCreateSubmit(self, url, user, data, *, form_errors=None, new_obj_query=None, success_status=302):
        assert form_errors or new_obj_query is not None, "must specify form_errors or new_obj_query"

        if form_errors:
            checks = [StatusCode(200), FormErrors(form_errors)]
            if new_obj_query:
                checks.append(ObjectNotCreated(new_obj_query))
        else:
            checks = [StatusCode(success_status), NoFormErrors(), ObjectCreated(new_obj_query)]

        return self.requestView(url, user, post_data=data, checks=checks, choose_org=self.org)

    def assertUpdateFetch(self, url, users, *, form_fields=(), status=200, choose_org=None):
        checks = [StatusCode(status), FormFields(form_fields)]
        if isinstance(form_fields, dict):
            checks.append(FormInitialValues(form_fields))

        response = None
        for user in users:
            response = self.requestView(url, user, checks=checks, choose_org=choose_org)

        return response

    def assertUpdateSubmit(self, url, user, data, *, form_errors=None, object_unchanged=None, success_status=302):
        assert not form_errors or object_unchanged, "if form_errors specified, must also specify object_unchanged"

        if form_errors:
            checks = [StatusCode(200), FormErrors(form_errors), ObjectUnchanged(object_unchanged)]
        else:
            checks = [StatusCode(success_status), NoFormErrors()]

        return self.requestView(url, user, post_data=data, checks=checks, choose_org=self.org)

    def assertDeleteFetch(self, url, users, *, status=200, as_modal=False, choose_org=None):
        checks = [StatusCode(status)]
        kwargs = {"HTTP_X_PJAX": True} if as_modal else {}

        response = None
        for user in users:
            response = self.requestView(url, user, checks=checks, choose_org=choose_org, **kwargs)

        return response

    def assertDeleteSubmit(
        self, url, user, *, object_unchanged=None, object_deleted=None, object_deactivated=None, success_status=302
    ):
        assert (
            object_unchanged or object_deleted or object_deactivated
        ), "must specify object_unchanged or object_deleted or object_deactivated"

        if object_unchanged:
            checks = [ObjectUnchanged(object_unchanged)]
        elif object_deleted:
            checks = [StatusCode(success_status), ObjectDeleted(object_deleted)]
        else:
            checks = [StatusCode(success_status), ObjectDeactivated(object_deactivated)]

        return self.requestView(url, user, post_data={}, checks=checks)

    def assertStaffOnly(self, url: str, choose_org=None):
        self.requestView(url, None, checks=[LoginRedirect()], choose_org=choose_org)
        self.requestView(url, self.agent, checks=[LoginRedirect()], choose_org=choose_org)
        self.requestView(url, self.user, checks=[LoginRedirect()], choose_org=choose_org)
        self.requestView(url, self.editor, checks=[LoginRedirect()], choose_org=choose_org)
        self.requestView(url, self.admin, checks=[LoginRedirect()], choose_org=choose_org)

        return self.requestView(url, self.customer_support, checks=[StatusCode(200)], choose_org=choose_org)

    def assertPageMenu(self, url, user, items: list, *, choose_org=None):
        response = self.requestView(
            url, user, checks=[StatusCode(200), ContentType("application/json")], choose_org=choose_org
        )

        def matcher(i):
            m = i["name"]
            if "count" in i:
                m = f"{m} ({i['count']})"
            if "items" in i:
                m = (m, [matcher(c) for c in i["items"] if "name" in c])
            return m

        actual = []
        for item in response.json()["results"]:
            if "name" in item:
                actual.append(matcher(item))

        self.assertEqual(items, actual)

    def assertContentMenu(self, url: str, user, items: list, choose_org=None):
        response = self.requestView(
            url,
            user,
            checks=[StatusCode(200), ContentType("application/json")],
            choose_org=choose_org,
            HTTP_TEMBA_CONTENT_MENU=1,
            HTTP_TEMBA_SPA=1,
        )
        self.assertEqual(items, [item.get("label", "-") for item in response.json()["items"]])


class BaseCheck:
    def pre_check(self, test_cls, desc):
        pass

    @abstractmethod
    def check(self, test_cls, response, desc):
        pass

    @staticmethod
    def get_context_item(test_cls, response, key, msg_prefix):
        test_cls.assertIn(key, response.context, msg=f"{msg_prefix}: expected {key} in context")
        return response.context[key]


class ContextObject(BaseCheck):
    def __init__(self, obj):
        self.object = obj

    def check(self, test_cls, response, msg_prefix):
        obj = self.get_context_item(test_cls, response, "object", msg_prefix)
        test_cls.assertEqual(self.object, obj, msg=f"{msg_prefix}: object mismatch")


class ContextObjectList(BaseCheck):
    def __init__(self, objects):
        self.objects = objects

    def check(self, test_cls, response, msg_prefix):
        object_list = self.get_context_item(test_cls, response, "object_list", msg_prefix)
        test_cls.assertEqual(list(self.objects), list(object_list), msg=f"{msg_prefix}: object list mismatch")


class ContextObjectCount(BaseCheck):
    def __init__(self, count):
        self.count = count

    def check(self, test_cls, response, msg_prefix):
        object_list = self.get_context_item(test_cls, response, "object_list", msg_prefix)
        test_cls.assertEqual(self.count, len(object_list), msg=f"{msg_prefix}: object count mismatch")


class ObjectCreated(BaseCheck):
    def __init__(self, query):
        self.query = query

    def pre_check(self, test_cls, msg_prefix):
        created = self.query.exists()
        sql = str(self.query.query)
        test_cls.assertFalse(created, msg=f"{msg_prefix}: expected no existing object to match: {sql}")

    def check(self, test_cls, response, msg_prefix):
        count = self.query.count()
        sql = str(self.query.query)
        test_cls.assertEqual(1, count, msg=f"{msg_prefix}: expected object to be created matching: {sql}")


class ObjectNotCreated(BaseCheck):
    def __init__(self, query):
        self.query = query

    def check(self, test_cls, response, msg_prefix):
        count = self.query.count()
        sql = str(self.query.query)
        test_cls.assertEqual(0, count, msg=f"{msg_prefix}: expected no object to be created matching: {sql}")


class ObjectUnchanged(BaseCheck):
    def __init__(self, obj):
        self.obj = obj
        self.obj_state = self.obj_as_dict(obj)

    def check(self, test_cls, response, msg_prefix):
        self.obj.refresh_from_db()

        test_cls.assertEqual(self.obj_state, self.obj_as_dict(self.obj), msg=f"{msg_prefix}: object state changed")

    def obj_as_dict(self, obj) -> dict:
        d = model_to_dict(obj)
        for k, v in d.items():
            # don't consider list ordering as significant
            if isinstance(v, list):
                d[k] = list(sorted(v, key=lambda x: str(x)))

        # logging in to request the view changes a user object so ignore that
        if isinstance(obj, User) and "last_login" in d:
            del d["last_login"]

        return d


class ObjectDeleted(BaseCheck):
    def __init__(self, obj):
        self.obj = obj

    def check(self, test_cls, response, msg_prefix):
        try:
            self.obj.refresh_from_db()
        except Exception:
            return

        test_cls.fail(msg=f"{msg_prefix}: object not deleted")


class ObjectDeactivated(BaseCheck):
    def __init__(self, obj):
        self.obj = obj

    def check(self, test_cls, response, msg_prefix):
        self.obj.refresh_from_db()
        test_cls.assertFalse(self.obj.is_active, msg=f"{msg_prefix}: expected object.is_active to be false")


class FormFields(BaseCheck):
    def __init__(self, fields):
        self.fields = fields

    def check(self, test_cls, response, msg_prefix):
        form = self.get_context_item(test_cls, response, "form", msg_prefix)
        fields = list(form.fields.keys())
        if "loc" in fields:
            fields.remove("loc")

        test_cls.assertEqual(list(self.fields), list(fields), msg=f"{msg_prefix}: form fields mismatch")


class FormInitialValues(BaseCheck):
    def __init__(self, fields: dict):
        self.fields = fields

    def check(self, test_cls, response, msg_prefix):
        form = self.get_context_item(test_cls, response, "form", msg_prefix)
        for field_key, value in self.fields.items():
            actual = form.initial[field_key] if field_key in form.initial else form.fields[field_key].initial
            if isinstance(actual, QuerySet):
                actual = list(actual)

            test_cls.assertEqual(
                actual,
                value,
                msg=f"{msg_prefix}: form field '{field_key}' initial value mismatch",
            )


class FormErrors(BaseCheck):
    def __init__(self, form_errors):
        self.form_errors = form_errors

    def check(self, test_cls, response, msg_prefix):
        actual = {}
        for field_key, errors in response.context["form"].errors.items():
            actual[field_key] = errors[0] if len(errors) == 1 else errors

        test_cls.assertEqual(actual, self.form_errors, msg=f"{msg_prefix}: form errors mismatch")


class NoFormErrors(BaseCheck):
    def check(self, test_cls, response, msg_prefix):
        test_cls.assertNoFormErrors(response)


class LoginRedirect(BaseCheck):
    def __init__(self, *values):
        self.values = values

    def check(self, test_cls, response, msg_prefix):
        test_cls.assertLoginRedirect(response, msg=f"{msg_prefix}: expected login redirect")


class StatusCode(BaseCheck):
    def __init__(self, status: int):
        self.status = status

    def check(self, test_cls, response, msg_prefix):
        test_cls.assertEqual(self.status, response.status_code, msg=f"{msg_prefix}: status code mismatch")


class ContentType(BaseCheck):
    def __init__(self, content_type: str):
        self.content_type = content_type

    def check(self, test_cls, response, msg_prefix):
        test_cls.assertEqual(
            self.content_type, response.headers["content-type"], msg=f"{msg_prefix}: content type mismatch"
        )


class StaffRedirect(BaseCheck):
    def check(self, test_cls, response, msg_prefix):
        test_cls.assertRedirect(response, reverse("orgs.org_service"), msg=f"{msg_prefix}: expected staff redirect")


class LoginRedirectOr404(BaseCheck):
    def check(self, test_cls, response, msg_prefix):
        if response.status_code == 302:
            test_cls.assertLoginRedirect(response, msg=f"{msg_prefix}: expected login redirect")
        else:
            test_cls.assertEqual(404, response.status_code, msg=f"{msg_prefix}: expected login redirect or 404")
