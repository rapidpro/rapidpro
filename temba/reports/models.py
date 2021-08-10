from django.db import models, connection
from django.db.models import Q
from django.utils.translation import ugettext_lazy as _

from smartmin.models import SmartModel

from django.conf import settings
from temba.flows.models import Flow
from temba.utils.models import JSONField
from temba.orgs.models import Org


class Report(SmartModel):
    TITLE = "title"
    DESCRIPTION = "description"
    CONFIG = "config"
    ID = "id"

    title = models.CharField(verbose_name=_("Title"), max_length=64, help_text=_("The name title or this report"))

    description = models.TextField(verbose_name=_("Description"), help_text=_("The full description for the report"))

    org = models.ForeignKey(Org, on_delete=models.PROTECT)

    config = JSONField(
        null=True, verbose_name=_("Configuration"), help_text=_("The JSON encoded configurations for this report")
    )

    is_published = models.BooleanField(default=False, help_text=_("Whether this report is currently published"))

    @classmethod
    def create_report(cls, org, user, json_dict):
        title = json_dict.get(Report.TITLE) or json_dict.get("text")
        description = json_dict.get(Report.DESCRIPTION)
        config = json_dict.get(Report.CONFIG)
        id = json_dict.get(Report.ID)

        existing = cls.objects.filter(pk=id, org=org)
        if existing:
            existing.update(title=title, description=description, config=config)

            return cls.objects.get(pk=id)

        return cls.objects.create(
            title=title, description=description, config=config, org=org, created_by=user, modified_by=user
        )

    def as_json(self):
        return dict(
            text=self.title, id=self.pk, description=self.description, config=self.config, public=self.is_published
        )

    def __str__(self):  # pragma: needs cover
        return "%s - %s" % (self.pk, self.title)

    class Meta:
        unique_together = (("org", "title"),)


class DataCollectionProcess(models.Model):
    TYPE_AUTO = "A"
    TYPE_MANUAL = "M"
    TYPE_CHOICES = ((TYPE_AUTO, "Automatically"), (TYPE_MANUAL, "Manually"))

    start_type = models.CharField(choices=TYPE_CHOICES, max_length=1, default=TYPE_AUTO)
    related_org = models.ForeignKey("orgs.Org", on_delete=models.SET_NULL, null=True)
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    started_on = models.DateTimeField(auto_now_add=True)
    completed_on = models.DateTimeField(blank=True, null=True)
    flows_total = models.PositiveIntegerField(default=0)
    flows_skipped = models.PositiveIntegerField(default=0)
    flows_processed = models.PositiveIntegerField(default=0)

    @classmethod
    def get_last_collection_process_status(cls, org):
        last_dc: DataCollectionProcess = (
            cls.objects.filter(Q(related_org=org) | Q(start_type=cls.TYPE_AUTO)).order_by("-started_on").first()
        )
        data_status = {
            "lastUpdate": str(last_dc.started_on),
            "completed": bool(last_dc.completed_on),
            "progress": (
                (last_dc.flows_skipped + last_dc.flows_processed) / last_dc.flows_total if last_dc.flows_total else 1
            ),
        }
        return data_status


class CollectedFlowResultsData(models.Model):
    """
    A model to store flow results data that is required for analytics reports, in more practical way.
    {
        result_key: {
            # ids of all contacts answered with that category
            category_key: [1, 4, 15],
            another_category_key: [3, 7]
        }
    }
    """

    flow = models.OneToOneField("flows.Flow", on_delete=models.PROTECT, related_name="aggregated_results")
    data = JSONField(default=dict)
    last_updated = models.DateTimeField(auto_now=True)

    @classmethod
    def collect_results_data(cls, flow: Flow):
        flow_results = {result["key"]: result["categories"] for result in flow.metadata["results"]}
        result_keys = sorted(list(flow_results.keys()))
        is_data_exists = cls.objects.filter(flow_id=flow.id).exists()
        is_data_already_updated = is_data_exists and not flow.runs.filter(
            modified_on__gt=flow.aggregated_results.last_updated
        )
        if is_data_already_updated or not result_keys:
            return

        # select runs data and build the aggregated data to save
        def category_extractor(field):
            return "results::jsonb #> '{%s, category}' as %s" % (field, field)

        query = (
            f"SELECT {', '.join(map(category_extractor, result_keys))}, array_agg(contact_id) as contact_id "
            f"FROM flows_flowrun WHERE flow_id = {flow.id}"
            f"GROUP BY {', '.join(result_keys)}"
        )

        final_data = {key: {category: set() for category in categories} for key, categories in flow_results.items()}
        with connection.cursor() as cursor:
            cursor.execute(query)
            result_answers = cursor.fetchall()
            for idx, result_key in enumerate(result_keys):
                for record in result_answers:
                    category_key = record[idx]
                    if category_key:
                        final_data[result_key][category_key].update(record[-1])

        # convert sets to lists
        for result_key, category_keys in flow_results.items():
            for category_key in category_keys:
                final_data[result_key][category_key] = list(final_data[result_key][category_key])

        # create or update the aggregated data in DB
        if is_data_exists:
            flow_results_agg = flow.aggregated_results
            flow_results_agg.data = final_data
            flow_results_agg.save()
        else:
            flow_results_agg = cls.objects.create(flow=flow, data=final_data)
        return flow_results_agg
