import json
from collections import defaultdict

from django.contrib.postgres.aggregates import ArrayAgg
from django.contrib.postgres.fields.jsonb import KeyTextTransform
from django.db import models
from django.db.models import Q
from django.db.models.functions import Cast

from smartmin.models import SmartModel

from django.conf import settings
from temba.flows.models import Flow, FlowRun
from temba.utils.models import JSONField
from temba.orgs.models import Org


class Report(SmartModel):
    ID = "id"
    TITLE = "title"
    DESCRIPTION = "description"
    CONFIG = "config"

    title = models.CharField(max_length=64)
    description = models.TextField()
    org = models.ForeignKey(Org, on_delete=models.PROTECT)
    config = JSONField(null=True)

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
        return dict(text=self.title, id=self.pk, description=self.description, config=self.config)

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
    flows_failed = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-started_on", "-id"]

    @classmethod
    def get_last_collection_process_status(cls, org):
        last_dc: DataCollectionProcess = (
            cls.objects.filter(Q(related_org=org) | Q(start_type=cls.TYPE_AUTO)).order_by("-started_on").first()
        )
        if last_dc:
            data_status = {
                "lastUpdated": str(last_dc.started_on),
                "completed": bool(last_dc.completed_on),
                "progress": (
                    (last_dc.flows_skipped + last_dc.flows_processed + last_dc.flows_failed) / last_dc.flows_total
                    if last_dc.flows_total
                    else 1
                ),
            }
        else:
            data_status = {"lastUpdated": None, "completed": True, "progress": 1}
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

    class Meta:
        ordering = ["-last_updated", "-id"]

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
        final_data = defaultdict(lambda: defaultdict(list))
        map(lambda res, categories: map(lambda ctg: final_data[res][ctg].extend([]), categories), flow_results.items())
        runs_data = (
            FlowRun.objects.filter(flow_id=flow.id)
            .annotate(
                **{
                    f"annotated_{result_key}": KeyTextTransform(
                        "category", KeyTextTransform(result_key, Cast("results", JSONField()))
                    )
                    for result_key in result_keys
                }
            )
            .values(*[f"annotated_{result_key}" for result_key in result_keys])
            .annotate(contact_ids=ArrayAgg("contact_id"))
        )
        for run_data in runs_data:
            for result_key in result_keys:
                category_key = run_data.get(f"annotated_{result_key}")
                if category_key is not None:
                    final_data[result_key][category_key].extend(run_data.get("contact_ids", []))

        # convert sets to lists
        final_data = json.loads(json.dumps(final_data))

        # create or update the aggregated data in DB
        if is_data_exists:
            flow_results_agg = flow.aggregated_results
            flow_results_agg.data = final_data
            flow_results_agg.save()
        else:
            flow_results_agg = cls.objects.create(flow=flow, data=final_data)
        return flow_results_agg
