from abc import abstractmethod


class BaseCheck:
    @abstractmethod
    def check(self, test_cls, response, desc):
        pass

    @staticmethod
    def get_context_item(test_cls, response, key, msg_prefix):
        test_cls.assertIn(key, response.context, msg=f"{msg_prefix}: expected {key} in context")
        return response.context[key]


class Contains(BaseCheck):
    def __init__(self, *values):
        self.values = values

    def check(self, test_cls, response, msg_prefix):
        for value in self.values:
            test_cls.assertContains(response, value, msg_prefix=msg_prefix)


class NotContains(BaseCheck):
    def __init__(self, *values):
        self.values = values

    def check(self, test_cls, response, msg_prefix):
        for value in self.values:
            test_cls.assertNotContains(response, value, msg_prefix=msg_prefix)


class Context(BaseCheck):
    def __init__(self, key, value):
        self.key = key
        self.value = value

    def check(self, test_cls, response, msg_prefix):
        value = self.get_context_item(test_cls, response, self.key, msg_prefix)
        test_cls.assertEqual(self.value, value, msg=f"{msg_prefix}: context['{self.key}'] mismatch")


class Object(BaseCheck):
    def __init__(self, obj):
        self.object = obj

    def check(self, test_cls, response, msg_prefix):
        obj = self.get_context_item(test_cls, response, "object", msg_prefix)
        test_cls.assertEqual(self.object, obj, msg=f"{msg_prefix}: object mismatch")


class ObjectList(BaseCheck):
    def __init__(self, *objects):
        self.objects = objects

    def check(self, test_cls, response, msg_prefix):
        object_list = self.get_context_item(test_cls, response, "object_list", msg_prefix)
        test_cls.assertEqual(self.objects, tuple(object_list), msg=f"{msg_prefix}: object list mismatch")


class ObjectCount(BaseCheck):
    def __init__(self, count):
        self.count = count

    def check(self, test_cls, response, msg_prefix):
        object_list = self.get_context_item(test_cls, response, "object_list", msg_prefix)
        test_cls.assertEqual(self.count, len(object_list), msg=f"{msg_prefix}: object count mismatch")


class FormFields(BaseCheck):
    def __init__(self, *fields):
        self.fields = fields

    def check(self, test_cls, response, msg_prefix):
        form = self.get_context_item(test_cls, response, "form", msg_prefix)

        fields = list(form.fields.keys())
        fields.remove("loc")

        test_cls.assertEqual(self.fields, tuple(fields), msg=f"{msg_prefix}: form fields mismatch")


class Redirect(BaseCheck):
    def __init__(self, to, partial=False, status=302):
        self.to = to
        self.partial = partial
        self.status = status

    def check(self, test_cls, response, msg_prefix):
        test_cls.assertEqual(self.status, response.status_code, msg=f"{msg_prefix}: status code mismatch")

        to = response.get("Location")
        if self.partial:
            test_cls.assertIn(self.to, to, msg=f"{msg_prefix}: redirect URL mismatch")
        else:
            test_cls.assertEqual(self.to, to, msg=f"{msg_prefix}: redirect URL mismatch")


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
