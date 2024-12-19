import base64
from datetime import date, datetime, timezone as tzone

from temba.archives.models import Archive
from temba.tests import TembaTest
from temba.utils import s3


class ArchiveTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.s3_calls = []

        def record_s3(model, params, **kwargs):
            self.s3_calls.append((model.name, params))

        s3.client().meta.events.register("provide-client-params.s3.*", record_s3)

    def test_iter_records(self):
        archive = self.create_archive(Archive.TYPE_MSG, "D", date(2024, 8, 14), [{"id": 1}, {"id": 2}, {"id": 3}])
        bucket, key = archive.get_storage_location()
        self.assertEqual("test-archives", bucket)
        self.assertEqual(f"{self.org.id}/message_D20240814_{archive.hash}.jsonl.gz", key)

        # can fetch records without any filtering
        records_iter = archive.iter_records()

        self.assertEqual(next(records_iter), {"id": 1})
        self.assertEqual(next(records_iter), {"id": 2})
        self.assertEqual(next(records_iter), {"id": 3})
        self.assertRaises(StopIteration, next, records_iter)

        # can filter using where dict
        records_iter = archive.iter_records(where={"id__gt": 1})

        self.assertEqual([{"id": 2}, {"id": 3}], [r for r in records_iter])

        # can also filter using raw where string (used by search_archives command)
        records_iter = archive.iter_records(where={"__raw__": "s.id < 3"})

        self.assertEqual([{"id": 1}, {"id": 2}], list(records_iter))

        self.assertEqual(
            [
                ("GetObject", {"Bucket": bucket, "Key": key}),
                (
                    "SelectObjectContent",
                    {
                        "Bucket": bucket,
                        "Key": key,
                        "Expression": "SELECT s.* FROM s3object s WHERE s.id > 1",
                        "ExpressionType": "SQL",
                        "InputSerialization": {"CompressionType": "GZIP", "JSON": {"Type": "LINES"}},
                        "OutputSerialization": {"JSON": {"RecordDelimiter": "\n"}},
                    },
                ),
                (
                    "SelectObjectContent",
                    {
                        "Bucket": bucket,
                        "Key": key,
                        "Expression": "SELECT s.* FROM s3object s WHERE s.id < 3",
                        "ExpressionType": "SQL",
                        "InputSerialization": {"CompressionType": "GZIP", "JSON": {"Type": "LINES"}},
                        "OutputSerialization": {"JSON": {"RecordDelimiter": "\n"}},
                    },
                ),
            ],
            self.s3_calls,
        )

    def test_iter_all_records(self):
        d1 = self.create_archive(
            Archive.TYPE_MSG,
            "D",
            date(2020, 7, 31),
            [
                {"id": 1, "created_on": "2020-07-30T10:00:00Z", "contact": {"name": "Bob"}},
                {"id": 2, "created_on": "2020-07-30T15:00:00Z", "contact": {"name": "Jim"}},
            ],
        )
        self.create_archive(
            Archive.TYPE_MSG,
            "M",
            date(2020, 7, 1),
            [
                {"id": 1, "created_on": "2020-07-30T10:00:00Z", "contact": {"name": "Bob"}},
                {"id": 2, "created_on": "2020-07-30T15:00:00Z", "contact": {"name": "Jim"}},
            ],
            rollup_of=(d1,),
        )
        self.create_archive(
            Archive.TYPE_MSG,
            "D",
            date(2020, 8, 1),
            [
                {"id": 3, "created_on": "2020-08-01T10:00:00Z", "contact": {"name": "Jim"}},
                {"id": 4, "created_on": "2020-08-01T15:00:00Z", "contact": {"name": "Bob"}},
            ],
        )
        self.create_archive(
            Archive.TYPE_FLOWRUN,
            "D",
            date(2020, 8, 1),
            [
                {"id": 3, "created_on": "2020-08-01T10:00:00Z", "contact": {"name": "Jim"}},
                {"id": 4, "created_on": "2020-08-01T15:00:00Z", "contact": {"name": "Bob"}},
            ],
        )
        self.create_archive(
            Archive.TYPE_MSG,
            "D",
            date(2020, 8, 2),
            [
                {"id": 5, "created_on": "2020-08-02T10:00:00Z", "contact": {"name": "Bob"}},
                {"id": 6, "created_on": "2020-08-02T15:00:00Z", "contact": {"name": "Bob"}},
            ],
        )

        def assert_records(record_iter, ids):
            self.assertEqual(ids, [r["id"] for r in list(record_iter)])

        assert_records(Archive.iter_all_records(self.org, Archive.TYPE_MSG), [1, 2, 3, 4, 5, 6])
        assert_records(
            Archive.iter_all_records(self.org, Archive.TYPE_MSG, after=datetime(2020, 7, 30, 12, 0, 0, 0, tzone.utc)),
            [2, 3, 4, 5, 6],
        )
        assert_records(
            Archive.iter_all_records(self.org, Archive.TYPE_MSG, before=datetime(2020, 8, 2, 12, 0, 0, 0, tzone.utc)),
            [1, 2, 3, 4, 5],
        )
        assert_records(
            Archive.iter_all_records(
                self.org,
                Archive.TYPE_MSG,
                after=datetime(2020, 7, 30, 12, 0, 0, 0, tzone.utc),
                before=datetime(2020, 8, 2, 12, 0, 0, 0, tzone.utc),
            ),
            [2, 3, 4, 5],
        )
        assert_records(
            Archive.iter_all_records(
                self.org,
                Archive.TYPE_MSG,
                after=datetime(2020, 7, 30, 12, 0, 0, 0, tzone.utc),
                before=datetime(2020, 8, 2, 12, 0, 0, 0, tzone.utc),
                where={"contact__name": "Bob"},
            ),
            [4, 5],
        )

    def test_end_date(self):
        daily = self.create_archive(Archive.TYPE_FLOWRUN, "D", date(2018, 2, 1), [], needs_deletion=True)
        monthly = self.create_archive(Archive.TYPE_FLOWRUN, "M", date(2018, 1, 1), [])

        self.assertEqual(date(2018, 2, 2), daily.get_end_date())
        self.assertEqual(date(2018, 2, 1), monthly.get_end_date())

        # check the start date of our db data
        self.assertEqual(date(2018, 2, 1), self.org.get_delete_date(archive_type=Archive.TYPE_FLOWRUN))

    def test_rewrite(self):
        archive = self.create_archive(
            Archive.TYPE_FLOWRUN,
            "D",
            date(2020, 8, 1),
            [
                {"id": 1, "created_on": "2020-08-01T09:00:00Z", "contact": {"name": "Bob"}},
                {"id": 2, "created_on": "2020-08-01T10:00:00Z", "contact": {"name": "Jim"}},
                {"id": 3, "created_on": "2020-08-01T15:00:00Z", "contact": {"name": "Bob"}},
            ],
        )

        bucket, key = archive.get_storage_location()

        def purge_jim(record):
            return record if record["contact"]["name"] != "Jim" else None

        archive.rewrite(purge_jim, delete_old=True)

        bucket, new_key = archive.get_storage_location()
        self.assertNotEqual(key, new_key)

        self.assertEqual(32, len(archive.hash))
        self.assertEqual(
            f"https://test-archives.s3.amazonaws.com/{self.org.id}/run_D20200801_{archive.hash}.jsonl.gz", archive.url
        )

        hash_b64 = base64.standard_b64encode(bytes.fromhex(archive.hash)).decode()

        self.assertEqual("PutObject", self.s3_calls[-2][0])
        self.assertEqual("test-archives", self.s3_calls[-2][1]["Bucket"])
        self.assertEqual(f"{self.org.id}/run_D20200801_{archive.hash}.jsonl.gz", self.s3_calls[-2][1]["Key"])
        self.assertEqual(hash_b64, self.s3_calls[-2][1]["ContentMD5"])
        self.assertEqual("DeleteObject", self.s3_calls[-1][0])
        self.assertEqual("test-archives", self.s3_calls[-1][1]["Bucket"])
