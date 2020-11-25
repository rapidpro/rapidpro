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

            start_from = forms.ChoiceField(
                label="Start from", required=True, help_text="Select the step that this process should start from"
            )

            migration_related_uuid = forms.CharField(
                label="UUID of the migration failed (if any)",
                required=False,
                help_text="Specify the failed migration UUID, this field will be used to get some data from that another migration",
            )

            def __init__(self, *args, **kwargs):
                self.org = kwargs["org"]
                del kwargs["org"]
                super().__init__(*args, **kwargs)

                migration = Migrator()

                self.fields["org"].choices = [(None, "---")] + [(org.id, org.name) for org in migration.get_all_orgs()]

                self.fields["start_from"].choices = [
                    (0, "Beginning"),
                    (1, "Channels"),
                    (2, "Contact Fields"),
                    (3, "Contacts"),
                    (4, "Contact Groups"),
                    (5, "Channel Events"),
                    (6, "Schedules"),
                    (7, "Msg Broadcasts"),
                    (8, "Msg Labels"),
                    (9, "Msgs"),
                    (10, "Flow Labels"),
                    (11, "Flows"),
                    (12, "Resthooks"),
                    (13, "Webhook Events"),
                    (14, "Campaigns"),
                    (15, "Triggers"),
                    (16, "Trackable Links"),
                    (17, "Parse Data"),
                ]

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

            def clean_migration_related_uuid(self):
                migration_related_uuid = self.cleaned_data.get("migration_related_uuid")
                start_from = self.cleaned_data.get("start_from")

                if int(start_from) > 0 and not migration_related_uuid:
                    raise ValidationError(
                        _("Migration related UUID needs to be specified when start from is not 'Beginning'")
                    )

                if migration_related_uuid and int(start_from) == 0:
                    raise ValidationError(
                        _("Migration related UUID is only accepted for start from after the 'Beginning'")
                    )

                return migration_related_uuid

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
                migration_related_uuid = form.cleaned_data.get("migration_related_uuid")
                migration_task = MigrationTask.create(
                    org=org,
                    user=user,
                    migration_org=form.cleaned_data.get("org"),
                    start_from=int(form.cleaned_data.get("start_from")),
                    migration_related_uuid=migration_related_uuid if migration_related_uuid else None,
                )
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
