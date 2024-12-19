from django.db.models import F, Value

from temba.tests import TembaTest

from .functions import SplitPart


class FunctionsTest(TembaTest):
    def test_split_part(self):
        self.org.counts.create(scope="foo:bar:baz", count=2)

        count1 = self.org.counts.annotate(
            part1=SplitPart(F("scope"), Value(":"), Value(1)), part2=SplitPart(F("scope"), Value(":"), Value(2))
        ).get()

        self.assertEqual(count1.part1, "foo")
        self.assertEqual(count1.part2, "bar")
