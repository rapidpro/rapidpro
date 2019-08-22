from uuid import uuid4

from django.utils import timezone

from temba.flows.models import Flow, FlowRun, FlowSession
from temba.utils.text import slugify_with


class MockSessionBuilder:
    """
    Builds sessions and runs that should look almost like the real thing from mailroom/goflow
    """

    PERSIST_EVENTS = {"msg_created", "msg_received"}
    EXIT_TYPES = {"completed": "C", "interrupted": "I", "expired": "E"}

    def __init__(self, contact, flow, start=None):
        self.org = contact.org
        self.contact = contact
        self.start = start

        contact_def = {"uuid": str(contact.uuid), "name": contact.name, "language": contact.language}
        session = {
            "uuid": str(uuid4()),
            "type": Flow.GOFLOW_TYPES[flow.flow_type],
            "environment": self.org.as_environment_def(),
            "trigger": {
                "type": "manual",
                "flow": flow.as_export_ref(),
                "contact": contact_def,
                "triggered_on": self._now(),
            },
            "contact": contact_def,
            "runs": [
                {
                    "uuid": str(uuid4()),
                    "flow": flow.as_export_ref(),
                    "path": [],
                    "events": [],
                    "results": {},
                    "status": "active",
                    "created_on": self._now(),
                    "modified_on": self._now(),
                    "exited_on": None,
                }
            ],
            "status": "active",
        }

        self.session = session
        self.current_run = session["runs"][0]

    def add_step(self, exit_uuid, new_node_uuid):
        if exit_uuid:
            self.current_run["path"][-1]["exit_uuid"] = exit_uuid

        self.current_run["path"].append({"uuid": str(uuid4()), "node_uuid": new_node_uuid, "arrived_on": self._now()})
        self.current_run["modified_on"] = self._now()
        return self

    def add_result(self, name, value, category, node_uuid, input):
        self.current_run["results"][slugify_with(name)] = {
            "name": name,
            "value": value,
            "category": category,
            "node_uuid": node_uuid,
            "input": input,
            "created_on": self._now(),
        }
        self.current_run["modified_on"] = self._now()
        self._log_event(
            "run_result_changed", name=name, value=value, category=category, node_uuid=node_uuid, input=input
        )
        return self

    def wait_for_msg(self):
        self.session["wait"] = {"type": "msg"}
        self.session["status"] = "waiting"
        self.current_run["status"] = "waiting"
        self.current_run["modified_on"] = self._now()
        self._log_event("msg_wait")
        return self

    def resume_with_msg(self, msg):
        self.session["wait"] = None
        self.session["status"] = "active"
        self.current_run["status"] = "active"
        self.current_run["modified_on"] = self._now()
        self._log_event("msg_received", msg={"urn": msg.contact_urn.urn, "text": msg.text})
        return self

    def complete(self):
        self._exit("completed")
        return self

    def interrupt(self):
        self._exit("interrupted")
        return self

    def fail(self, text):
        self._log_event("failure", msg={"text": text})
        self._exit("failed")
        return self

    def save(self):
        assert self.session["status"] != "active", "active sessions never persisted to database"

        db_flow_types = {v: k for k, v in Flow.GOFLOW_TYPES.items()}

        # interrupt any active runs or sessions
        self.contact.flowsession_set.filter(status=FlowSession.STATUS_WAITING).update(
            status=FlowSession.STATUS_INTERRUPTED, ended_on=timezone.now()
        )
        self.contact.runs.filter(is_active=True).update(
            status=FlowRun.STATUS_INTERRUPTED,
            exit_type=FlowRun.EXIT_TYPE_INTERRUPTED,
            is_active=False,
            modified_on=timezone.now(),
            exited_on=timezone.now(),
        )

        session_obj = FlowSession.objects.create(
            uuid=self.session["uuid"],
            org=self.org,
            contact=self.contact,
            session_type=db_flow_types[self.session["type"]],
            output=self.session,
            status=FlowSession.GOFLOW_STATUSES[self.session["status"]],
        )

        for i, run in enumerate(self.session["runs"]):
            FlowRun.objects.create(
                uuid=run["uuid"],
                org=self.org,
                start=self.start if i == 0 else None,
                flow=Flow.objects.get(uuid=run["flow"]["uuid"]),
                contact=self.contact,
                session=session_obj,
                path=run["path"],
                events=[e for e in run["events"] if e["type"] in self.PERSIST_EVENTS],
                results=run["results"],
                exit_type=self.EXIT_TYPES.get(run["status"]),
                is_active=run["status"] in ("waiting", "active"),
                status=FlowRun.GOFLOW_STATUSES[run["status"]],
                current_node_uuid=run["path"][-1]["node_uuid"] if run["path"] else None,
                modified_on=run["modified_on"],
                exited_on=run["exited_on"],
                responded=bool([e for e in run["events"] if e["type"] == "msg_received"]),
            )

        return session_obj

    def _now(self):
        return timezone.now().isoformat()

    def _log_event(self, _type, **kwargs):
        self.current_run["events"].append({"type": _type, "created_on": self._now(), **kwargs})

    def _exit(self, status):
        self.session["status"] = status

        for run in self.session["runs"]:
            run["status"] = status
            run["modified_on"] = self._now()
