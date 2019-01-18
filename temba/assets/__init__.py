from django.conf import settings
from django.core.checks import Error, register


@register()
def check_storage_url(app_configs, **kwargs):
    errors = []
    if not settings.STORAGE_URL:
        errors.append(
            Error(
                "No storage URL set",
                hint='Set STORAGE_URL in your Django settings. Should be "https://"+AWS_BUCKET_DOMAIN if using S3',
                id="assets.E001",
            )
        )
    return errors
