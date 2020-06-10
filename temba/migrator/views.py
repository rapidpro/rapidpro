import logging

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect, HttpResponse
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from smartmin.views import SmartCRUDL, SmartFormView, SmartReadView, SmartListView

from temba.orgs.views import InferOrgMixin, OrgPermsMixin
from temba.utils.views import NonAtomicMixin

from .models import Migrator, MigrationTask
from .tasks import start_migration
from ..utils import on_transaction_commit


class MigrationPermsMixin(OrgPermsMixin):
    def has_permission(self, request, *args, **kwargs):
        """
        Figures out if the current user has permissions for this view.
        """
        self.kwargs = kwargs
        self.args = args
        self.request = request
        self.org = self.derive_org()

        return True if self.get_user().is_superuser else False


class MigrateCRUDL(SmartCRUDL):
    actions = ("create", "read", "logs")

    model = MigrationTask

    class Create(NonAtomicMixin, InferOrgMixin, MigrationPermsMixin, SmartFormView):
        class MigrationForm(forms.Form):
            org = forms.ChoiceField(
                label="Organization", required=True, help_text="Select the organization from the live server"
            )

            def __init__(self, *args, **kwargs):
                self.org = kwargs["org"]
                del kwargs["org"]
                super().__init__(*args, **kwargs)

                migration = Migrator()

                self.fields["org"].choices = [(None, "---")] + [(org.id, org.name) for org in migration.get_all_orgs()]

            def clean_org(self):
                org = self.cleaned_data.get("org")

                try:
                    org_id = int(org)
                except Exception:
                    raise ValidationError(_("Please type the correct organization ID, only integer is acceptable."))

                migration = Migrator(org_id=org_id)
                org = migration.get_org()
                if not org:
                    raise ValidationError(_("The organization ID was not found on live server."))

                return org_id

        success_message = _("Data migration started successfully")
        form_class = MigrationForm
        submit_button_name = "Start migration"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

        def form_valid(self, form):
            try:
                user = self.request.user
                org = user.get_org()
                migration_task = MigrationTask.create(org=org, user=user, migration_org=form.cleaned_data.get("org"))
                on_transaction_commit(lambda: start_migration.delay(migration_task.id))
            except Exception as e:
                # this is an unexpected error, report it to sentry
                logger = logging.getLogger(__name__)
                logger.error("Exception on the migration: %s" % str(e), exc_info=True)
                form._errors["org"] = form.error_class([_("Sorry, something went wrong on the migration.")])
                return self.form_invalid(form)

            return HttpResponseRedirect(reverse("migrator.migrationtask_read", args=[migration_task.uuid]))

    class Read(MigrationPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def derive_title(self):
            return self.object.uuid

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["log_url"] = f"{reverse('migrator.migrationtask_logs')}?uuid={self.object.uuid}"
            return context

        def get_gear_links(self):
            return [
                dict(title=_("New migration"), href=reverse("migrator.migrationtask_create")),
                dict(
                    title=_("Download logs"),
                    href=f"{reverse('migrator.migrationtask_logs')}?uuid={self.object.uuid}",
                    js_class="download-logs",
                ),
            ]

    class Logs(MigrationPermsMixin, SmartListView):
        def get(self, request, *args, **kwargs):
            file_path = f"{settings.MEDIA_ROOT}/migration_logs/{self.request.GET.get('uuid')}.log"
            with open(file_path, "r", encoding="utf-8") as f:
                response = HttpResponse(content=f.read())
                response["Content-Type"] = "text/plain"
                response["Content-Disposition"] = f"attachment; filename={self.request.GET.get('uuid')}.txt"
                return response
