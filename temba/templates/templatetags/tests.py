from temba.tests import TembaTest

from .templates import handlebars


class TemplatesTest(TembaTest):
    def test_handlebars(self):
        self.assertEqual("Hello", handlebars("Hello"))
        self.assertEqual("Hello <code>{{name}}</code>", handlebars("Hello {{name}}"))
