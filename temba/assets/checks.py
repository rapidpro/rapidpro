from django.conf import settings
from django.core.checks import Error, register


@register()
def storage_url(app_configs, **kwargs):
    errors = []
    if not settings.STORAGE_URL:
        errors.append(
            Error(
                "No storage URL set",
                hint='Set STORAGE_URL in your Django settings. Should be "https://"+AWS_BUCKET_DOMAIN if using S3',
            )
        )
    elif settings.STORAGE_URL.endswith("/"):
        errors.append(
            Error("Storage URL shouldn't end with trailing slash", hint="Remove trailing slash in STORAGE_URL")
        )
    return errors
