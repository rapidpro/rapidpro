from datetime import datetime

from django.urls import reverse

from temba.archives.models import Archive

from . import APITest


class ArchivesEndpointTest(APITest):
    def test_endpoint(self):
        endpoint_url = reverse("api.v2.archives") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        # create some archives
        Archive.objects.create(
            org=self.org,
            start_date=datetime(2017, 4, 5),
            build_time=12,
            record_count=34,
            size=345,
            hash="c4ca4238a0b923820dcc509a6f75849b",
            archive_type=Archive.TYPE_MSG,
            period=Archive.PERIOD_DAILY,
        )
        archive2 = Archive.objects.create(
            org=self.org,
            start_date=datetime(2017, 5, 5),
            build_time=12,
            record_count=34,
            size=345,
            hash="c81e728d9d4c2f636f067f89cc14862c",
            archive_type=Archive.TYPE_MSG,
            period=Archive.PERIOD_MONTHLY,
        )
        archive3 = Archive.objects.create(
            org=self.org,
            start_date=datetime(2017, 6, 5),
            build_time=12,
            record_count=34,
            size=345,
            hash="eccbc87e4b5ce2fe28308fd9f2a7baf3",
            archive_type=Archive.TYPE_FLOWRUN,
            period=Archive.PERIOD_DAILY,
        )
        archive4 = Archive.objects.create(
            org=self.org,
            start_date=datetime(2017, 7, 5),
            build_time=12,
            record_count=34,
            size=345,
            hash="a87ff679a2f3e71d9181a67b7542122c",
            archive_type=Archive.TYPE_FLOWRUN,
            period=Archive.PERIOD_MONTHLY,
        )
        # this archive has been rolled up and it should not be included in the API responses
        Archive.objects.create(
            org=self.org,
            start_date=datetime(2017, 5, 1),
            build_time=12,
            record_count=34,
            size=345,
            hash="e4da3b7fbbce2345d7772b0674a318d5",
            archive_type=Archive.TYPE_FLOWRUN,
            period=Archive.PERIOD_DAILY,
            rollup=archive2,
        )

        # create archive for other org
        Archive.objects.create(
            org=self.org2,
            start_date=datetime(2017, 5, 1),
            build_time=12,
            record_count=34,
            size=345,
            hash="1679091c5a880faf6fb5e6087eb1b2dc",
            archive_type=Archive.TYPE_FLOWRUN,
            period=Archive.PERIOD_DAILY,
        )

        # there should be 4 archives in the response, because one has been rolled up
        self.assertGet(
            endpoint_url,
            [self.editor],
            results=[
                {
                    "archive_type": "run",
                    "download_url": "",
                    "hash": "a87ff679a2f3e71d9181a67b7542122c",
                    "period": "monthly",
                    "record_count": 34,
                    "size": 345,
                    "start_date": "2017-07-05",
                },
                {
                    "archive_type": "run",
                    "download_url": "",
                    "hash": "eccbc87e4b5ce2fe28308fd9f2a7baf3",
                    "period": "daily",
                    "record_count": 34,
                    "size": 345,
                    "start_date": "2017-06-05",
                },
                {
                    "archive_type": "message",
                    "download_url": "",
                    "hash": "c81e728d9d4c2f636f067f89cc14862c",
                    "period": "monthly",
                    "record_count": 34,
                    "size": 345,
                    "start_date": "2017-05-05",
                },
                {
                    "archive_type": "message",
                    "download_url": "",
                    "hash": "c4ca4238a0b923820dcc509a6f75849b",
                    "period": "daily",
                    "record_count": 34,
                    "size": 345,
                    "start_date": "2017-04-05",
                },
            ],
            num_queries=self.BASE_SESSION_QUERIES + 2,
        )

        self.assertGet(endpoint_url + "?after=2017-05-01", [self.editor], results=[archive4, archive3, archive2])
        self.assertGet(endpoint_url + "?after=2017-05-01&archive_type=run", [self.editor], results=[archive4, archive3])

        # unknown archive type
        self.assertGet(endpoint_url + "?archive_type=invalid", [self.editor], results=[])

        # only for dailies
        self.assertGet(
            endpoint_url + "?after=2017-05-01&archive_type=run&period=daily", [self.editor], results=[archive3]
        )

        # only for monthlies
        self.assertGet(endpoint_url + "?period=monthly", [self.editor], results=[archive4, archive2])

        # test access from a user with no org
        self.login(self.non_org_user)
        response = self.client.get(endpoint_url)
        self.assertEqual(403, response.status_code)
