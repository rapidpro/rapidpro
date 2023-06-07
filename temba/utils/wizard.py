from formtools.wizard.views import SessionWizardView
from smartmin.views import SmartView, smart_url

from django.core.exceptions import ImproperlyConfigured


class SmartWizardView(SmartView, SessionWizardView):
    submit_button_name = "Submit"

    def __init__(self, *args, **kwargs):
        self.initial_dict = kwargs.get("initial_dict", {})
        self.extra_context = {}
        super(SessionWizardView, self).__init__(*args, **kwargs)

    def derive_readonly(self):
        return []

    def lookup_field_help(self, field, default=None):
        form = self.get_form(self.steps.current)
        return form.fields[field].help_text or default

    def lookup_field_label(self, context, field, object):
        return context["form"].fields[field].label

    def get_template_names(self):
        templates = []

        # start with our smartmin assigned template name
        template_name = self.template_name

        original = self.template_name.split(".")
        if len(original) == 2:
            template_name = f"{original[0]}_{self.steps.current}.{original[1]}"

        if template_name:
            templates.append(template_name)

        templates.append("utils/forms/wizard.html")
        return templates

    def derive_submit_button_name(self):
        """
        Returns the name for our button
        """
        return self.submit_button_name

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["completed"] = ",".join(
            [step for step in self.steps.all if self.get_cleaned_data_for_step(step) is not None]
        )
        context["submit_button_name"] = self.derive_submit_button_name()
        return context

    # here to support standard smartmin behavior, but pragma since object references aren't used yet
    def get_success_url(self):  # pragma: no cover
        if self.success_url:
            # if our smart url references an object, pass that in
            if self.success_url.find("@") > 0:
                return smart_url(self.success_url, self.object)
            else:
                return smart_url(self.success_url, None)
        raise ImproperlyConfigured("No redirect location found, override get_success_url to not use redirect urls")
