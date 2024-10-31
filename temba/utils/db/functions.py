from django.db import models
from django.db.models import Func


class SplitPart(Func):
    function = "SPLIT_PART"
    output_field = models.CharField()
