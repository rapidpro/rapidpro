from django.core.cache import cache
from django import forms
from django.utils.translation import gettext_lazy as _
from django.conf import settings

from .views import UserCRUDL as UserCRUDLBase


USER_RECOVER_ATTEMPTS_CACHE_KEY = "user-email:{email}"
USER_RECOVER_TIME_INTERVAL = settings.USER_RECOVER_TIME_INTERVAL * 60 * 60


class UserCRUDL(UserCRUDLBase):
    class Forget(UserCRUDLBase.Forget):
        class ForgetForm(UserCRUDLBase.Forget.ForgetForm):
            def clean_email(self):
                email = super().clean_email()
                attempts_key = USER_RECOVER_ATTEMPTS_CACHE_KEY.format(email=email)

                attempts = cache.get_or_set(attempts_key, 1, USER_RECOVER_TIME_INTERVAL)
                cache.incr(attempts_key, 1)

                if attempts is not None and attempts > settings.USER_RECOVER_MAX_ATTEMPTS:
                    cache.touch(attempts_key, USER_RECOVER_TIME_INTERVAL)
                    raise forms.ValidationError(
                        _(
                            f"You have exceeded the maximum number of attempts, "
                            "please try again in {settings.USER_RECOVER_TIME_INTERVAL} hours!"
                        )
                    )

                return email

        form_class = ForgetForm
