import zope.interface


class IMetricBackend(zope.interface.Interface):
    """
    A metric backend
    """

    def gauge(event, value=None):
        """
        Sets the value of a gauge
        """

    def increment(event, value=None):
        """
        Increments a counter
        """
