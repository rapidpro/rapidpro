import iso8601

from django.db import migrations, transaction
from django.db.models import Q

RESULT_NAME = "name"
RESULT_NODE_UUID = "node_uuid"
RESULT_CATEGORY = "category"
RESULT_CATEGORY_LOCALIZED = "category_localized"
RESULT_VALUE = "value"
RESULT_INPUT = "input"
RESULT_CREATED_ON = "created_on"


def build_context(run, snapshot_on):
    def result_wrapper(res):
        return {
            "__default__": res[RESULT_VALUE],
            "text": res.get(RESULT_INPUT),
            "time": res[RESULT_CREATED_ON],
            "category": res.get(RESULT_CATEGORY_LOCALIZED, res[RESULT_CATEGORY]),
            "value": res[RESULT_VALUE],
        }

    context = {}
    default_lines = []

    for key, result in run.results.items():
        result_created_on = iso8601.parse_date(result[RESULT_CREATED_ON])
        if result_created_on < snapshot_on or not snapshot_on:
            context[key] = result_wrapper(result)
            default_lines.append("%s: %s" % (result[RESULT_NAME], result[RESULT_VALUE]))

    context["__default__"] = "\n".join(default_lines)
    context["contact"] = str(run.contact.uuid)
    return context


def backfill_related_run_contexts(FlowRun):
    active_runs = FlowRun.objects.filter(is_active=True).filter(Q(parent_context=None) | Q(child_context=None))
    total_to_update = active_runs.count()
    if not total_to_update:
        return

    print(f"Found {total_to_update} active runs that need child/parent contexts backfilled...")

    max_id = 0
    num_updated = 0
    while True:
        batch = list(
            active_runs.filter(id__gt=max_id)
            .select_related("contact", "parent", "parent__contact")
            .order_by("id")[:1000]
        )
        if not batch:
            break

        batch_ids = [r.id for r in batch]

        # get all the completed runs which are children of runs in this batch
        batch_children = (
            FlowRun.objects.filter(parent_id__in=batch_ids, is_active=False)
            .select_related("contact")
            .order_by("exited_on")
        )

        # organize into a dict of parent -> last child
        last_child_by_parent = {}
        for child in batch_children:
            if child.contact == child.parent.contact:
                last_child_by_parent[child.parent] = child

        with transaction.atomic():
            for active_run in batch:
                parent = active_run.parent
                child = last_child_by_parent.get(active_run)

                parent_context = None
                child_context = None
                needs_update = False

                if parent and not active_run.parent_context:
                    parent_context = build_context(parent, active_run.created_on)
                    needs_update = True

                if child and not active_run.child_context:
                    child_context = build_context(child, child.exited_on)
                    needs_update = True

                if needs_update:
                    active_run.parent_context = parent_context
                    active_run.child_context = child_context
                    active_run.save(update_fields=("parent_context", "child_context"))

        num_updated += len(batch)
        max_id = batch[-1].id
        print(f" > Updated {num_updated} of {total_to_update} active runs")


def apply_manual():
    from temba.flows.models import FlowRun

    backfill_related_run_contexts(FlowRun)


def apply_as_migration(apps, schema_editor):
    FlowRun = apps.get_model("flows", "FlowRun")
    backfill_related_run_contexts(FlowRun)


def clear_migration(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    atomic = False

    dependencies = [("flows", "0162_auto_20180528_1705")]

    operations = [migrations.RunPython(apply_as_migration, clear_migration)]
