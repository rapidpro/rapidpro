from _pytest.python import Module
from _pytest.unittest import TestCaseFunction, UnitTestCase


def keep_item(item):
    """
    Return `False` for tests collected from TestCase classes that weren't
    defined in the module they were found in, `True` otherwise.
    """
    if not isinstance(item, TestCaseFunction):
        # This isn't a TestCase method, so keep it.
        return True

    parent_cls = item.getparent(UnitTestCase)
    parent_module = item.getparent(Module)
    if None in (parent_cls, parent_module):
        # Should this be possible? We can't filter, though, so keep it.
        return True

    if parent_cls.cls.__module__ == parent_module.module.__name__:
        # The class belongs to the module it was found in.
        return True
    else:
        return False


def pytest_collection_modifyitems(session, config, items):
    """
    Filter out all tests collected from TestCase classes that weren't defined
    in the module they were found in.
    """
    deselected = []
    remaining = []
    for item in items:
        if keep_item(item):
            remaining.append(item)
        else:
            deselected.append(item)

    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = remaining
