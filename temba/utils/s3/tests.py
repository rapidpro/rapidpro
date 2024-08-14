from datetime import datetime, timezone as tzone

from temba.tests import TembaTest
from temba.utils.s3 import compile_select, split_url


class S3Test(TembaTest):
    def test_split_url(self):
        with self.settings(AWS_S3_ADDRESSING_STYLE="virtual"):
            bucket, url = split_url("https://foo.s3.aws.amazon.com/test/this/12345")
            self.assertEqual("foo", bucket)
            self.assertEqual("test/this/12345", url)

        with self.settings(AWS_S3_ADDRESSING_STYLE="path"):
            bucket, url = split_url("https://foo.s3.aws.amazon.com/test/this/12345")
            self.assertEqual("test", bucket)
            self.assertEqual("this/12345", url)


class SelectTest(TembaTest):
    def test_compile_select(self):
        self.assertEqual("SELECT s.* FROM s3object s", compile_select())
        self.assertEqual("SELECT m.* FROM s3object m", compile_select(alias="m"))
        self.assertEqual(
            "SELECT s.name, s.contact.uuid FROM s3object s", compile_select(fields=["name", "contact__uuid"])
        )
        self.assertEqual(
            "SELECT c.* FROM s3object c WHERE c.uuid = '12345'", compile_select(alias="c", where={"uuid": "12345"})
        )
        self.assertEqual(
            "SELECT s.* FROM s3object s WHERE s.contact.uuid = '12345'",
            compile_select(where={"contact__uuid": "12345"}),
        )
        self.assertEqual(
            "SELECT s.* FROM s3object s WHERE s.uuid = '12345' AND s.id = 123 AND s.active = TRUE",
            compile_select(where={"uuid": "12345", "id": 123, "active": True}),
        )
        self.assertEqual(
            "SELECT s.* FROM s3object s WHERE CAST(s.created_on AS TIMESTAMP) > CAST('2021-09-28T18:27:30.123456+00:00' AS TIMESTAMP)",
            compile_select(where={"created_on__gt": datetime(2021, 9, 28, 18, 27, 30, 123456, tzone.utc)}),
        )
        self.assertEqual(
            "SELECT s.* FROM s3object s WHERE CAST(s.modified_on AS TIMESTAMP) <= CAST('2021-09-28T18:27:30.123456+00:00' AS TIMESTAMP)",
            compile_select(where={"modified_on__lte": datetime(2021, 9, 28, 18, 27, 30, 123456, tzone.utc)}),
        )
        self.assertEqual(
            "SELECT s.* FROM s3object s WHERE s.flow.uuid IN ('1234', '2345')",
            compile_select(where={"flow__uuid__in": ("1234", "2345")}),
        )
        self.assertEqual(
            "SELECT s.* FROM s3object s WHERE s.flow IS NULL",
            compile_select(where={"flow__isnull": True}),
        )
        self.assertEqual(
            "SELECT s.* FROM s3object s WHERE s.flow IS NOT NULL",
            compile_select(where={"flow__isnull": False}),
        )

        # a where clause can also be raw S3-select-SQL (used by search_archives command)
        self.assertEqual(
            "SELECT s.* FROM s3object s WHERE s.uuid = '2345' AND s.contact.uuid = '1234'",
            compile_select(where={"uuid": "2345", "__raw__": "s.contact.uuid = '1234'"}),
        )
        self.assertEqual(
            "SELECT s.* FROM s3object s WHERE '1ccf09f6-3fe8-4c0d-a073-981632be5a30' IN s.labels[*].uuid[*]",
            compile_select(where={"__raw__": "'1ccf09f6-3fe8-4c0d-a073-981632be5a30' IN s.labels[*].uuid[*]"}),
        )
