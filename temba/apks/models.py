from gettext import gettext as _
from urllib.parse import urlparse

import boto3

from django.conf import settings
from django.db import models
from django.utils import timezone


class Apk(models.Model):
    DOWNLOAD_EXPIRES = 60 * 60 * 24  # Up to 24 hours

    TYPE_RELAYER = "R"
    TYPE_MESSAGE_PACK = "M"

    TYPE_CHOICES = (
        (TYPE_RELAYER, _("Relayer Application APK")),
        (TYPE_MESSAGE_PACK, _("Message Pack Application APK")),
    )

    apk_type = models.CharField(choices=TYPE_CHOICES, max_length=1)

    name = models.CharField(
        verbose_name=_("Name"),
        max_length=64,
        blank=True,
        null=True,
        help_text=_("Descriptive label for this application APK"),
    )

    apk_file = models.FileField(upload_to="apks")

    created_on = models.DateTimeField(default=timezone.now)

    description = models.TextField(null=True, blank=True, default="")

    def s3_location(self):
        url_parts = urlparse(self.url)
        return dict(Bucket=url_parts.netloc.split(".")[0], Key=url_parts.path[1:])

    @classmethod
    def s3_client(cls):
        session = boto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
        )
        return session.client("s3")

    def filename(self):
        url_parts = urlparse(self.url)
        return url_parts.path.split("/")[-1]

    def get_download_link(self):
        if self.url:
            s3 = self.s3_client()
            s3_params = {
                **self.s3_location(),
                # force browser to download and not uncompress our gzipped files
                "ResponseContentDisposition": "attachment;",
                "ResponseContentType": "application/octet",
                "ResponseContentEncoding": "none",
            }

            return s3.generate_presigned_url("get_object", Params=s3_params, ExpiresIn=Apk.DOWNLOAD_EXPIRES)
        else:
            return ""
