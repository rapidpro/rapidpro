from uuid import uuid4
from temba.tests import TembaTest
from django.core.urlresolvers import reverse
from .models import Archive
from datetime import date, timedelta


class ArchiveViewTest(TembaTest):

    def create_archive(self, idx, start_date=None, end_date=None):

        if not start_date:
            start_date = date(2018, idx, 1)
            end_date = date(2018, idx + 1, 1) - timedelta(days=1)

        archive_hash = uuid4().hex
        return Archive.objects.create(
            archive_type=Archive.TYPE_MSG if idx % 2 == 0 else Archive.TYPE_FLOWRUN,
            archive_size=100000 * idx,
            archive_hash=archive_hash,
            archive_url=f'http://s3-bucket.aws.com/my/{archive_hash}',
            record_count=123456789 * idx,
            start_date=start_date,
            end_date=end_date,
            build_time=idx * 123,
            org=self.org
        )

    def test_empty_list(self):
        self.login(self.admin)
        response = self.client.get(reverse('archives.archive_list', args=['runs']))
        self.assertEqual(0, response.context['object_list'].count())
        self.assertEqual('Run Archive', response.context['title'])

        response = self.client.get(reverse('archives.archive_list', args=['messages']))
        self.assertEqual(0, response.context['object_list'].count())
        self.assertEqual('Message Archive', response.context['title'])

    def test_archive_type_filter(self):
        for idx in range(1, 10):
            self.create_archive((idx))

        # create a daily archive
        archive = self.create_archive(1, start_date=date(2018, 2, 1), end_date=(date(2018, 2, 2)))
        self.assertEqual(Archive.PERIOD_DAILY, archive.archive_period())

        self.login(self.admin)

        # make sure we have the right number of each
        response = self.client.get(reverse('archives.archive_list', args=['runs']))
        self.assertEqual(6, response.context['object_list'].count())

        response = self.client.get(reverse('archives.archive_list', args=['messages']))
        self.assertEqual(4, response.context['object_list'].count())

    def test_download(self):
        self.login(self.admin)

        archive = self.create_archive(1)
        response = self.client.get(reverse('archives.archive_read', args=[archive.id]))
        url = response.get('Location')

        self.assertEqual(302, response.status_code)
        self.assertTrue(f'https://s3-bucket.s3.amazonaws.com/my/{archive.archive_hash}?'
                        f'response-content-disposition=attachment%3B&'
                        f'response-content-type=application%2Foctet&'
                        f'response-content-encoding=none' in url)
