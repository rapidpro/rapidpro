import base64
import gzip
import hashlib
import io
from datetime import date, datetime, timezone as tzone

from django.urls import reverse

from temba.tests import CRUDLTestMixin, TembaTest
from temba.utils import s3

from .models import Archive, jsonlgz_rewrite


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


class ArchiveCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_empty_list(self):
        response = self.assertListFetch(reverse("archives.archive_run"), [self.editor], context_objects=[])
        self.assertContains(response, "No archives found")

        response = self.assertListFetch(reverse("archives.archive_message"), [self.editor], context_objects=[])
        self.assertContains(response, "No archives found")

    def test_archive_type_filter(self):
        # a daily archive that has been rolled up and will not appear in the results
        d1 = self.create_archive(Archive.TYPE_MSG, "D", date(2020, 7, 31), [{"id": 1}, {"id": 2}])
        m1 = self.create_archive(Archive.TYPE_MSG, "M", date(2020, 7, 1), [{"id": 1}, {"id": 2}], rollup_of=(d1,))
        d2 = self.create_archive(Archive.TYPE_MSG, "D", date(2020, 8, 1), [{"id": 3}, {"id": 4}])
        d3 = self.create_archive(Archive.TYPE_FLOWRUN, "D", date(2020, 8, 1), [{"id": 3}, {"id": 4}])

        # create archive for other org
        self.create_archive(Archive.TYPE_MSG, "D", date(2020, 7, 31), [{"id": 1}], org=self.org2)

        runs_url = reverse("archives.archive_run")
        msgs_url = reverse("archives.archive_message")

        self.assertRequestDisallowed(runs_url, [None, self.user, self.agent])
        self.assertRequestDisallowed(msgs_url, [None, self.user, self.agent])

        response = self.assertListFetch(runs_url, [self.editor, self.admin], context_objects=[d3])
        self.assertContains(response, f"/archive/read/{d3.id}/")

        response = self.assertListFetch(msgs_url, [self.editor, self.admin], context_objects=[d2, m1])
        self.assertContains(response, f"/archive/read/{d2.id}/")
        self.assertContains(response, f"/archive/read/{m1.id}/")

    def test_read(self):
        archive = self.create_archive(Archive.TYPE_MSG, "D", date(2020, 7, 31), [{"id": 1}, {"id": 2}])

        download_url = (
            f"http://localhost:9000/test-archives/{self.org.id}/message_D20200731_{archive.hash}.jsonl.gz?response-con"
            f"tent-disposition=attachment%3B&response-content-type=application%2Foctet&response-content-encoding=none"
        )

        self.assertRequestDisallowed(download_url, [None, self.user, self.agent, self.admin2])
        response = self.assertReadFetch(
            reverse("archives.archive_read", args=[archive.id]), [self.editor, self.admin], status=302
        )

        self.assertIn(download_url, response.get("Location"))


class JSONLGZTest(TembaTest):
    def test_jsonlgz_rewrite(self):
        def rewrite(b: bytes, transform):
            in_file = io.BytesIO(b)
            out_file = io.BytesIO()
            md5, size = jsonlgz_rewrite(in_file, out_file, transform)
            return out_file.getvalue(), md5.hexdigest(), size

        data = b'{"id": 123, "name": "Jim"}\n{"id": 234, "name": "Bob"}\n{"id": 345, "name": "Ann"}\n'
        gzipped = gzip.compress(data)

        # rewrite that using a pass-through transform for each record
        data1, hash1, size1 = rewrite(gzipped, lambda r: r)

        self.assertEqual(data, gzip.decompress(data1))
        self.assertEqual(hashlib.md5(data1).hexdigest(), hash1)
        self.assertEqual(68, size1)

        # should get the exact same file and hash if we just repeat that
        data2, hash2, size2 = rewrite(gzipped, lambda r: r)

        self.assertEqual(data1, data2)
        self.assertEqual(hash1, hash2)
        self.assertEqual(68, size2)

        # rewrite with a transform that modifies each record
        def name_to_upper(record) -> dict:
            record["name"] = record["name"].upper()
            return record

        data3, hash3, size3 = rewrite(gzipped, name_to_upper)

        self.assertEqual(
            b'{"id": 123, "name": "JIM"}\n{"id": 234, "name": "BOB"}\n{"id": 345, "name": "ANN"}\n',
            gzip.decompress(data3),
        )
        self.assertEqual(hashlib.md5(data3).hexdigest(), hash3)
        self.assertEqual(68, size3)

        # rewrite with a transform that removes a record
        def remove_bob(record) -> dict:
            return None if record["id"] == 234 else record

        data4, hash4, size4 = rewrite(gzipped, remove_bob)

        self.assertEqual(b'{"id": 123, "name": "Jim"}\n{"id": 345, "name": "Ann"}\n', gzip.decompress(data4))
        self.assertEqual(hashlib.md5(data4).hexdigest(), hash4)
        self.assertEqual(58, size4)
