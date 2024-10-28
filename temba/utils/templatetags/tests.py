from datetime import datetime, timezone as tzone
from unittest.mock import patch

from django.template import Context, Template
from django.utils import timezone, translation

from temba.campaigns.models import Campaign, CampaignEvent
from temba.flows.models import Flow
from temba.tests import TembaTest
from temba.triggers.models import Trigger
from temba.utils import json

from . import temba as tags


class TembaTagLibraryTest(TembaTest):
    def _render(self, template, context=None):
        context = context or {}
        context = Context(context)
        return Template("{% load temba %}" + template).render(context)

    def test_format_datetime(self):
        with patch.object(timezone, "now", return_value=datetime(2015, 9, 15, 0, 0, 0, 0, tzone.utc)):
            self.org.date_format = "D"
            self.org.save()

            # date without timezone and no user org in context
            test_date = datetime(2012, 7, 20, 17, 5, 30, 0)
            self.assertEqual("20-07-2012 17:05", tags.format_datetime(dict(), test_date))
            self.assertEqual("20-07-2012 17:05:30", tags.format_datetime(dict(), test_date, seconds=True))

            test_date = datetime(2012, 7, 20, 17, 5, 30, 0).replace(tzinfo=tzone.utc)
            self.assertEqual("20-07-2012 17:05", tags.format_datetime(dict(), test_date))
            self.assertEqual("20-07-2012 17:05:30", tags.format_datetime(dict(), test_date, seconds=True))

            context = dict(user_org=self.org)

            # date without timezone
            test_date = datetime(2012, 7, 20, 17, 5, 0, 0)
            self.assertEqual("20-07-2012 19:05", tags.format_datetime(context, test_date))

            test_date = datetime(2012, 7, 20, 17, 5, 0, 0).replace(tzinfo=tzone.utc)
            self.assertEqual("20-07-2012 19:05", tags.format_datetime(context, test_date))

            # the org has month first configured
            self.org.date_format = "M"
            self.org.save()

            # date without timezone
            test_date = datetime(2012, 7, 20, 17, 5, 0, 0)
            self.assertEqual("07-20-2012 19:05", tags.format_datetime(context, test_date))

            test_date = datetime(2012, 7, 20, 17, 5, 0, 0).replace(tzinfo=tzone.utc)
            self.assertEqual("07-20-2012 19:05", tags.format_datetime(context, test_date))

            # the org has year first configured
            self.org.date_format = "Y"
            self.org.save()

            # date without timezone
            test_date = datetime(2012, 7, 20, 17, 5, 0, 0)
            self.assertEqual("2012-07-20 19:05", tags.format_datetime(context, test_date))

            test_date = datetime(2012, 7, 20, 17, 5, 0, 0).replace(tzinfo=tzone.utc)
            self.assertEqual("2012-07-20 19:05", tags.format_datetime(context, test_date))

    def test_oxford(self):
        self.assertEqual(
            "",
            self._render(
                "{% for word in words %}{{ word }}{{ forloop|oxford }}{% endfor %}",
                {"words": []},
            ),
        )
        self.assertEqual(
            "one",
            self._render(
                "{% for word in words %}{{ word }}{{ forloop|oxford }}{% endfor %}",
                {"words": ["one"]},
            ),
        )
        self.assertEqual(
            "one and two",
            self._render(
                "{% for word in words %}{{ word }}{{ forloop|oxford }}{% endfor %}",
                {"words": ["one", "two"]},
            ),
        )
        with translation.override("es"):
            self.assertEqual(
                "one y two",
                self._render(
                    "{% for word in words %}{{ word }}{{ forloop|oxford }}{% endfor %}",
                    {"words": ["one", "two"]},
                ),
            )
        with translation.override("fr"):
            self.assertEqual(
                "one et two",
                self._render(
                    "{% for word in words %}{{ word }}{{ forloop|oxford }}{% endfor %}",
                    {"words": ["one", "two"]},
                ),
            )
        self.assertEqual(
            "one or two",
            self._render(
                '{% for word in words %}{{ word }}{{ forloop|oxford:"or" }}{% endfor %}',
                {"words": ["one", "two"]},
            ),
        )
        with translation.override("es"):
            self.assertEqual(
                "uno o dos",
                self._render(
                    '{% for word in words %}{{ word }}{{ forloop|oxford:_("or") }}{% endfor %}',
                    {"words": ["uno", "dos"]},
                ),
            )
        self.assertEqual(
            "one, two, and three",
            self._render(
                "{% for word in words %}{{ word }}{{ forloop|oxford }}{% endfor %}",
                {"words": ["one", "two", "three"]},
            ),
        )

    def test_to_json(self):
        from temba.utils.templatetags.temba import to_json

        # only works with plain str objects
        self.assertRaises(ValueError, to_json, dict())

        self.assertEqual(to_json(json.dumps({})), 'JSON.parse("{}")')
        self.assertEqual(to_json(json.dumps({"a": 1})), 'JSON.parse("{\\u0022a\\u0022: 1}")')
        self.assertEqual(
            to_json(json.dumps({"special": '"'})),
            'JSON.parse("{\\u0022special\\u0022: \\u0022\\u005C\\u0022\\u0022}")',
        )

        # ecapes special <script>
        self.assertEqual(
            to_json(json.dumps({"special": '<script>alert("XSS");</script>'})),
            'JSON.parse("{\\u0022special\\u0022: \\u0022\\u003Cscript\\u003Ealert(\\u005C\\u0022XSS\\u005C\\u0022)\\u003B\\u003C/script\\u003E\\u0022}")',
        )

    def test_verbose_name_plural(self):
        flow = self.create_flow("Test")
        group = self.create_group("Testers", contacts=[])

        self.assertEqual("Flows", tags.verbose_name_plural(flow))
        self.assertEqual("Groups", tags.verbose_name_plural(group))
        self.assertEqual("Campaigns", tags.verbose_name_plural(Campaign()))
        self.assertEqual("Campaign Events", tags.verbose_name_plural(CampaignEvent()))

    def test_object_url(self):
        flow = self.create_flow("Test")
        group = self.create_group("Testers", contacts=[])

        self.assertEqual(f"/flow/editor/{flow.uuid}/", tags.object_url(flow))
        self.assertEqual(f"/contact/group/{group.uuid}/", tags.object_url(group))

    def test_object_class_plural(self):
        self.assertEqual("Flow", tags.object_class_name(Flow()))
        self.assertEqual("Campaign", tags.object_class_name(Campaign()))
        self.assertEqual("CampaignEvent", tags.object_class_name(CampaignEvent()))
        self.assertEqual("Trigger", tags.object_class_name(Trigger()))

    def test_unsnake(self):
        self.assertEqual("Rapid Pro", tags.unsnake("rapid_pro"))
        self.assertEqual("Contact Birth Year", tags.unsnake("contact_birth_year"))

    def test_day(self):
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="date"></temba-date>',
            tags.day(datetime(2024, 4, 3, 14, 45, 30, 0, tzone.utc)),
        )
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="date"></temba-date>',
            tags.day("2024-04-03T14:45:30+00:00"),
        )

    def test_datetime(self):
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="datetime"></temba-date>',
            tags.datetime(datetime(2024, 4, 3, 14, 45, 30, 0, tzone.utc)),
        )
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="datetime"></temba-date>',
            tags.datetime("2024-04-03T14:45:30+00:00"),
        )

    def test_duration(self):
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="duration"></temba-date>',
            tags.duration(datetime(2024, 4, 3, 14, 45, 30, 0, tzone.utc)),
        )
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="duration"></temba-date>',
            tags.duration("2024-04-03T14:45:30+00:00"),
        )

    def test_timedate(self):
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="timedate"></temba-date>',
            tags.timedate(datetime(2024, 4, 3, 14, 45, 30, 0, tzone.utc)),
        )
        self.assertEqual(
            '<temba-date value="2024-04-03T14:45:30+00:00" display="timedate"></temba-date>',
            tags.timedate("2024-04-03T14:45:30+00:00"),
        )
