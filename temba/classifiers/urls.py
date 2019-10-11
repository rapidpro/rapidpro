from django.conf.urls import include, url

from .models import Classifier
from .views import ClassifierCRUDL

# build up all the type specific urls
type_urls = []
for cl_type in Classifier.get_types():
    cl_urls = cl_type.get_urls()
    for u in cl_urls:
        u.name = "classifiers.types.%s.%s" % (cl_type.slug, u.name)

    if cl_urls:
        type_urls.append(url("^%s/" % cl_type.slug, include(cl_urls)))

urlpatterns = [url(r"^", include(ClassifierCRUDL().as_urlpatterns())), url(r"^classifiers/types/", include(type_urls))]
