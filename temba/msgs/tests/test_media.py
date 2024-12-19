from django.conf import settings

from temba.msgs.models import Media
from temba.tests import TembaTest, mock_uuids


class MediaTest(TembaTest):
    def test_clean_name(self):
        self.assertEqual("file.jpg", Media.clean_name("", "image/jpeg"))
        self.assertEqual("foo.jpg", Media.clean_name("foo", "image/jpeg"))
        self.assertEqual("file.png", Media.clean_name("*.png", "image/png"))
        self.assertEqual("passwd.jpg", Media.clean_name(".passwd", "image/jpeg"))
        self.assertEqual("tést[0].jpg", Media.clean_name("tést[0]/^..\\", "image/jpeg"))

    @mock_uuids
    def test_from_upload(self):
        media = Media.from_upload(
            self.org,
            self.admin,
            self.upload(f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg", "image/jpeg"),
            process=False,
        )

        self.assertEqual("b97f69f7-5edf-45c7-9fda-d37066eae91d", str(media.uuid))
        self.assertEqual(self.org, media.org)
        self.assertEqual(
            f"{settings.STORAGE_URL}/orgs/{self.org.id}/media/b97f/b97f69f7-5edf-45c7-9fda-d37066eae91d/steve%20marten.jpg",
            media.url,
        )
        self.assertEqual("image/jpeg", media.content_type)
        self.assertEqual(
            f"orgs/{self.org.id}/media/b97f/b97f69f7-5edf-45c7-9fda-d37066eae91d/steve marten.jpg", media.path
        )
        self.assertEqual(self.admin, media.created_by)
        self.assertEqual(Media.STATUS_PENDING, media.status)

        # check that our filename is cleaned
        media = Media.from_upload(
            self.org,
            self.admin,
            self.upload(f"{settings.MEDIA_ROOT}/test_media/klab.png", "image/png", name="../../../etc/passwd"),
            process=False,
        )

        self.assertEqual(f"orgs/{self.org.id}/media/14f6/14f6ea01-456b-4417-b0b8-35e942f549f1/passwd.png", media.path)

    @mock_uuids
    def test_process_image_png(self):
        media = Media.from_upload(
            self.org,
            self.admin,
            self.upload(f"{settings.MEDIA_ROOT}/test_media/klab.png", "image/png"),
        )
        media.refresh_from_db()

        self.assertEqual(371425, media.size)
        self.assertEqual(0, media.duration)
        self.assertEqual(480, media.width)
        self.assertEqual(360, media.height)
        self.assertEqual(Media.STATUS_READY, media.status)

    @mock_uuids
    def test_process_audio_wav(self):
        media = Media.from_upload(
            self.org, self.admin, self.upload(f"{settings.MEDIA_ROOT}/test_media/allo.wav", "audio/wav")
        )
        media.refresh_from_db()

        self.assertEqual(81818, media.size)
        self.assertEqual(5110, media.duration)
        self.assertEqual(0, media.width)
        self.assertEqual(0, media.height)
        self.assertEqual(Media.STATUS_READY, media.status)

        alt1, alt2 = list(media.alternates.order_by("id"))

        self.assertEqual(self.org, alt1.org)
        self.assertEqual(
            f"{settings.STORAGE_URL}/orgs/{self.org.id}/media/14f6/14f6ea01-456b-4417-b0b8-35e942f549f1/allo.mp3",
            alt1.url,
        )
        self.assertEqual("audio/mp3", alt1.content_type)
        self.assertEqual(f"orgs/{self.org.id}/media/14f6/14f6ea01-456b-4417-b0b8-35e942f549f1/allo.mp3", alt1.path)
        self.assertAlmostEqual(5517, alt1.size, delta=1000)
        self.assertEqual(5110, alt1.duration)
        self.assertEqual(0, alt1.width)
        self.assertEqual(0, alt1.height)
        self.assertEqual(Media.STATUS_READY, alt1.status)

        self.assertEqual(self.org, alt2.org)
        self.assertEqual(
            f"{settings.STORAGE_URL}/orgs/{self.org.id}/media/d1ee/d1ee73f0-bdb5-47ce-99dd-0c95d4ebf008/allo.m4a",
            alt2.url,
        )
        self.assertEqual("audio/mp4", alt2.content_type)
        self.assertEqual(f"orgs/{self.org.id}/media/d1ee/d1ee73f0-bdb5-47ce-99dd-0c95d4ebf008/allo.m4a", alt2.path)
        self.assertAlmostEqual(20552, alt2.size, delta=7500)
        self.assertEqual(5110, alt2.duration)
        self.assertEqual(0, alt2.width)
        self.assertEqual(0, alt2.height)
        self.assertEqual(Media.STATUS_READY, alt2.status)

    @mock_uuids
    def test_process_audio_m4a(self):
        media = Media.from_upload(
            self.org, self.admin, self.upload(f"{settings.MEDIA_ROOT}/test_media/bubbles.m4a", "audio/mp4")
        )
        media.refresh_from_db()

        self.assertEqual(46468, media.size)
        self.assertEqual(10216, media.duration)
        self.assertEqual(0, media.width)
        self.assertEqual(0, media.height)
        self.assertEqual(Media.STATUS_READY, media.status)

        alt = media.alternates.get()

        self.assertEqual(self.org, alt.org)
        self.assertEqual(
            f"{settings.STORAGE_URL}/orgs/{self.org.id}/media/14f6/14f6ea01-456b-4417-b0b8-35e942f549f1/bubbles.mp3",
            alt.url,
        )
        self.assertEqual("audio/mp3", alt.content_type)
        self.assertEqual(f"orgs/{self.org.id}/media/14f6/14f6ea01-456b-4417-b0b8-35e942f549f1/bubbles.mp3", alt.path)
        self.assertAlmostEqual(41493, alt.size, delta=1000)
        self.assertEqual(10216, alt.duration)
        self.assertEqual(0, alt.width)
        self.assertEqual(0, alt.height)
        self.assertEqual(Media.STATUS_READY, alt.status)

    @mock_uuids
    def test_process_video_mp4(self):
        media = Media.from_upload(
            self.org, self.admin, self.upload(f"{settings.MEDIA_ROOT}/test_media/snow.mp4", "video/mp4")
        )
        media.refresh_from_db()

        self.assertEqual(684558, media.size)
        self.assertEqual(3536, media.duration)
        self.assertEqual(640, media.width)
        self.assertEqual(480, media.height)
        self.assertEqual(Media.STATUS_READY, media.status)

        alt = media.alternates.get()

        self.assertEqual(self.org, alt.org)
        self.assertEqual(
            f"{settings.STORAGE_URL}/orgs/{self.org.id}/media/14f6/14f6ea01-456b-4417-b0b8-35e942f549f1/snow.jpg",
            alt.url,
        )
        self.assertEqual("image/jpeg", alt.content_type)
        self.assertEqual(f"orgs/{self.org.id}/media/14f6/14f6ea01-456b-4417-b0b8-35e942f549f1/snow.jpg", alt.path)
        self.assertAlmostEqual(37613, alt.size, delta=1000)
        self.assertEqual(0, alt.duration)
        self.assertEqual(640, alt.width)
        self.assertEqual(480, alt.height)
        self.assertEqual(Media.STATUS_READY, alt.status)

    @mock_uuids
    def test_process_unsupported(self):
        media = Media.from_upload(
            self.org, self.admin, self.upload(f"{settings.MEDIA_ROOT}/test_imports/simple.xlsx", "audio/m4a")
        )
        media.refresh_from_db()

        self.assertEqual(9635, media.size)
        self.assertEqual(Media.STATUS_FAILED, media.status)
