from abc import abstractmethod

from django.forms import model_to_dict


class CRUDLTestMixin:
    def get_test_users(self):
        return self.user, self.editor, self.admin, self.admin2

    def requestView(self, url, user, *, post_data=None, checks=()):
        """
        Requests the given URL as a specific user and runs a set of checks
        """

        method = "POST" if post_data is not None else "GET"
        user_name = user.username if user else "anonymous"
        msg_prefix = f"{method} {url} as {user_name}"
        pre_msg_prefix = f"before {msg_prefix}"

        self.client.logout()
        if user:
            self.login(user)

        for check in checks:
            check.pre_check(self, pre_msg_prefix)

        response = self.client.post(url, post_data) if method == "POST" else self.client.get(url)

        for check in checks:
            check.check(self, response, msg_prefix)

        return response

    def assertReadFetch(self, url, *, allow_viewers, allow_editors, context_object=None, status=200):
        """
        Fetches a read view as different users
        """
        viewer, editor, admin, org2_admin = self.get_test_users()

        def as_user(user, allowed):
            if allowed:
                checks = [StatusCode(status)]
                if context_object:
                    checks.append(ContextObject(context_object))
            else:
                checks = [LoginRedirectOr404()]

            return self.requestView(url, user, checks=checks)

        as_user(None, allowed=False)
        as_user(viewer, allowed=allow_viewers)
        as_user(editor, allowed=allow_editors)
        as_user(org2_admin, allowed=False)
        return as_user(admin, allowed=True)

    def assertListFetch(
        self, url, *, allow_viewers, allow_editors, context_objects=None, context_object_count=None, status=200
    ):
        assert (
            context_objects is not None or context_object_count is not None
        ), "must specify context_objects or context_object_count"

        viewer, editor, admin, org2_admin = self.get_test_users()

        def as_user(user, allowed):
            if allowed:
                checks = [StatusCode(status)]
                if user != org2_admin:
                    if context_objects is not None:
                        checks.append(ContextObjectList(context_objects))
                    else:
                        checks.append(ContextObjectCount(context_object_count))
            else:
                checks = [LoginRedirect()]

            return self.requestView(url, user, checks=checks)

        as_user(None, allowed=False)
        as_user(viewer, allowed=allow_viewers)
        as_user(editor, allowed=allow_editors)
        as_user(org2_admin, allowed=True)
        return as_user(admin, allowed=True)

    def assertCreateFetch(self, url, *, allow_viewers, allow_editors, form_fields, status=200):
        viewer, editor, admin, org2_admin = self.get_test_users()

        def as_user(user, allowed):
            if allowed:
                checks = [StatusCode(status), FormFields(form_fields)]
            else:
                checks = [LoginRedirect()]

            return self.requestView(url, user, checks=checks)

        as_user(None, allowed=False)
        as_user(viewer, allowed=allow_viewers)
        as_user(editor, allowed=allow_editors)
        as_user(org2_admin, allowed=True)
        return as_user(admin, allowed=True)

    def assertCreateSubmit(self, url, data, *, form_errors=None, new_obj_query=None, success_status=302):
        assert form_errors or new_obj_query is not None, "must specify form_errors or new_obj_query"

        viewer, editor, admin, org2_admin = self.get_test_users()

        def as_user(user, allowed):
            if allowed:
                if form_errors:
                    checks = [StatusCode(200), FormErrors(form_errors)]
                    if new_obj_query:
                        checks.append(ObjectNotCreated(new_obj_query))
                else:
                    checks = [StatusCode(success_status), NoFormErrors(), ObjectCreated(new_obj_query)]
            else:
                checks = [LoginRedirect()]

            return self.requestView(url, user, post_data=data, checks=checks)

        as_user(None, allowed=False)
        return as_user(admin, allowed=True)

    def assertUpdateFetch(self, url, *, allow_viewers, allow_editors, form_fields, status=200):
        viewer, editor, admin, org2_admin = self.get_test_users()

        def as_user(user, allowed):
            if allowed:
                checks = [StatusCode(status), FormFields(form_fields)]
            else:
                checks = [LoginRedirect()]

            return self.requestView(url, user, checks=checks)

        as_user(None, allowed=False)
        as_user(viewer, allowed=allow_viewers)
        as_user(editor, allowed=allow_editors)
        as_user(org2_admin, allowed=False)
        return as_user(admin, allowed=True)

    def assertUpdateSubmit(self, url, data, *, form_errors=None, object_unchanged=None, success_status=302):
        assert not form_errors or object_unchanged, "if form_errors specified, must also specify object_unchanged"

        viewer, editor, admin, org2_admin = self.get_test_users()

        def as_user(user, allowed):
            if allowed:
                if form_errors:
                    checks = [StatusCode(200), FormErrors(form_errors), ObjectUnchanged(object_unchanged)]
                else:
                    checks = [StatusCode(success_status), NoFormErrors()]
            else:
                checks = [LoginRedirect()]

            return self.requestView(url, user, post_data=data, checks=checks)

        as_user(None, allowed=False)
        as_user(org2_admin, allowed=False)
        return as_user(admin, allowed=True)

    def assertDeleteFetch(self, url, *, allow_viewers=False, allow_editors=False, status=200):
        viewer, editor, admin, org2_admin = self.get_test_users()

        def as_user(user, allowed):
            if allowed:
                checks = [StatusCode(status)]
            else:
                checks = [LoginRedirect()]

            return self.requestView(url, user, checks=checks)

        as_user(None, allowed=False)
        as_user(viewer, allowed=allow_viewers)
        as_user(editor, allowed=allow_editors)
        as_user(org2_admin, allowed=False)
        return as_user(admin, allowed=True)

    def assertDeleteSubmit(self, url, *, object_unchanged=None, object_deleted=None, success_status=302):
        assert object_unchanged or object_deleted is not None, "must specify object_unchanged or object_deleted"

        viewer, editor, admin, org2_admin = self.get_test_users()

        def as_user(user, allowed):
            if allowed:
                if object_unchanged:
                    checks = [ObjectUnchanged(object_unchanged)]
                else:
                    checks = [StatusCode(success_status), ObjectDeleted(object_deleted)]
            else:
                checks = [LoginRedirect()]

            return self.requestView(url, user, post_data={}, checks=checks)

        as_user(None, allowed=False)
        as_user(org2_admin, allowed=False)
        return as_user(admin, allowed=True)


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
        self.obj_state = model_to_dict(obj)

    def check(self, test_cls, response, msg_prefix):
        self.obj.refresh_from_db()

        test_cls.assertEqual(self.obj_state, model_to_dict(self.obj), msg=f"{msg_prefix}: object state changed")


class ObjectDeleted(BaseCheck):
    def __init__(self, obj):
        self.obj = obj

    def check(self, test_cls, response, msg_prefix):
        try:
            self.obj.refresh_from_db()
        except Exception:
            return

        test_cls.fail(msg=f"{msg_prefix}: object not deleted")


class FormFields(BaseCheck):
    def __init__(self, fields):
        self.fields = fields

    def check(self, test_cls, response, msg_prefix):
        form = self.get_context_item(test_cls, response, "form", msg_prefix)

        fields = list(form.fields.keys())
        fields.remove("loc")

        test_cls.assertEqual(list(self.fields), fields, msg=f"{msg_prefix}: form fields mismatch")


class FormErrors(BaseCheck):
    def __init__(self, form_errors):
        self.form_errors = form_errors

    def check(self, test_cls, response, msg_prefix):
        for field_key, message in self.form_errors.items():
            test_cls.assertFormError(response, "form", field_key, message, msg_prefix=msg_prefix)


class NoFormErrors(BaseCheck):
    def check(self, test_cls, response, msg_prefix):
        test_cls.assertNoFormErrors(response)


class LoginRedirect(BaseCheck):
    def __init__(self, *values):
        self.values = values

    def check(self, test_cls, response, msg_prefix):
        test_cls.assertLoginRedirect(response, msg=f"{msg_prefix}: expected login redirect")


class StatusCode(BaseCheck):
    def __init__(self, status):
        self.status = status

    def check(self, test_cls, response, msg_prefix):
        test_cls.assertEqual(self.status, response.status_code, msg=f"{msg_prefix}: status code mismatch")


class LoginRedirectOr404(BaseCheck):
    def check(self, test_cls, response, msg_prefix):
        if response.status_code == 302:
            test_cls.assertLoginRedirect(response, msg=f"{msg_prefix}: expected login redirect")
        else:
            test_cls.assertEqual(404, response.status_code, msg=f"{msg_prefix}: expected login redirect or 404")
