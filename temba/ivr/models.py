from temba.channels.models import IVRCall


class IVRCall(IVRCall):
    class Meta:
        proxy = True

    def start_call(self):
        from temba.ivr.tasks import start_call_task
        start_call_task.delay(self.pk)
