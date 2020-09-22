# this mixin allows us to take a peacemeal approach to reskinning
class VanillaMixin:  # pragma: needs cover
    def get_template_names(self):
        templates = super().get_template_names()
        vanilla = self.request.GET.get("vanilla", self.request.session.get("vanilla", "O")) == "1"
        if vanilla:
            original = templates[0].split(".")
            if len(original) == 2:
                vanilla_template = original[0] + "_vanilla." + original[1]
            else:
                vanilla_template = self.template_name_vanilla

            if vanilla_template:
                templates.insert(0, vanilla_template)

        return templates
