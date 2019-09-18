from django.conf import settings
from django.test.runner import DiscoverRunner


class TembaTestRunner(DiscoverRunner):
    """
    Adds the ability to exclude tests in given packages to the default test runner
    """

    def __init__(self, *args, **kwargs):
        settings.TESTING = True

        super().__init__(*args, **kwargs)

    def build_suite(self, *args, **kwargs):
        suite = super().build_suite(*args, **kwargs)
        excluded = getattr(settings, "TEST_EXCLUDE", [])
        if not getattr(settings, "RUN_ALL_TESTS", False):
            tests = []
            for case in suite:
                pkg = case.__class__.__module__.split(".")[0]
                if pkg not in excluded:
                    tests.append(case)
            suite._tests = tests
        return suite
