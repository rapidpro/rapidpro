import logging

from datetime import datetime
from pytz import timezone

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

            start_date = forms.CharField(
                label="Start date and time",
                required=False,
                help_text="Select from when this process should pull data (leave it blank to pull all data). "
                "Format: MM-DD-YYYY HH:mm (Org. timezone)",
            )

            end_date = forms.CharField(
                label="End date and time",
                required=False,
                help_text="Select the end date this process should pull data (depends on start date field). "
                "Format: MM-DD-YYYY HH:mm (Org. timezone)",
            )

            migration_related_uuid = forms.CharField(
                label="UUID of the migration (if any)",
                required=False,
                help_text="Specify the migration UUID, this field will be used to get some data from that "
                "another migration (it would be used for a failed migration or for a specific start "
                "date migration)",
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
                start_date = self.cleaned_data.get("start_date")
                end_date = self.cleaned_data.get("end_date")

                if migration_related_uuid and start_date and end_date:
                    return migration_related_uuid

                if int(start_from) > 0 and not migration_related_uuid:
                    raise ValidationError(
                        _("Migration related UUID needs to be specified when start from is not 'Beginning'")
                    )

                if migration_related_uuid and int(start_from) == 0:
                    raise ValidationError(
                        _("Migration related UUID is only accepted for start from after the 'Beginning'")
                    )

                if start_date and not migration_related_uuid:
                    raise ValidationError(_("Migration related UUID required when start/end date are informed"))

                return migration_related_uuid

            def clean_start_date(self):
                org = self.org
                date = self.cleaned_data.get("start_date")

                if date:
                    date = datetime.strptime(date, "%m-%d-%Y %H:%M")
                    date = timezone(str(org.timezone)).localize(date)

                return date if date else None

            def clean_end_date(self):
                org = self.org

                start_date = self.cleaned_data.get("start_date")
                end_date = self.cleaned_data.get("end_date")

                if start_date and not end_date:
                    raise ValidationError(_("You should add an end date since you provided a start date"))

                if not end_date:
                    return end_date

                end_date = datetime.strptime(end_date, "%m-%d-%Y %H:%M")
                end_date = timezone(str(org.timezone)).localize(end_date)

                if start_date > end_date:
                    raise ValidationError(_("The end date should be greater than the start date"))

                return end_date

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
                date_start = form.cleaned_data.get("start_date")
                end_date = form.cleaned_data.get("end_date")
                migration_task = MigrationTask.create(
                    org=org,
                    user=user,
                    migration_org=form.cleaned_data.get("org"),
                    start_from=int(form.cleaned_data.get("start_from")),
                    migration_related_uuid=migration_related_uuid if migration_related_uuid else None,
                    start_date=date_start if date_start else None,
                    end_date=end_date if end_date else None,
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
