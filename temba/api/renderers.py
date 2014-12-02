from __future__ import unicode_literals

from django import forms
from rest_framework import parsers
from rest_framework.renderers import BrowsableAPIRenderer
from rest_framework.settings import api_settings

class PostFormAPIRenderer(BrowsableAPIRenderer):

    def get_form(self, view, method, request):
        """
        Get a form, possibly bound to either the input or output data.
        In the absence on of the Resource having an associated form then
        provide a form that can be used to submit arbitrary content.
        """
        obj = getattr(view, 'object', None)
        if not self.show_form_for_method(view, method, request, obj):
            return

        if method in ('DELETE', 'OPTIONS'):
            return True  # Don't actually need to return a form

        if not getattr(view, 'get_serializer', None) or not parsers.FormParser in view.parser_classes: # pragma: no cover
            return

        if method =='POST' and hasattr(view, 'form_serializer_class'):
            serializer_class = view.form_serializer_class
            context = view.get_serializer_context()
            serializer = serializer_class(context=context)
        else:
            serializer = view.get_serializer(instance=obj)  # pragma: no cover

        fields = self.serializer_to_form_fields(serializer)

        # Creating an on the fly form see:
        # http://stackoverflow.com/questions/3915024/dynamically-creating-classes-python
        OnTheFlyForm = type(str("OnTheFlyForm"), (forms.Form,), fields)
        data = (obj is not None) and serializer.data or None
        form_instance = OnTheFlyForm(data)
        return form_instance

    def get_raw_data_form(self, view, method, request, media_types):
        """
        Returns a form that allows for arbitrary content types to be tunneled
        via standard HTML forms.
        (Which are typically application/x-www-form-urlencoded)
        """

        # If we're not using content overloading there's no point in supplying a generic form,
        # as the view won't treat the form's value as the content of the request.
        if not (api_settings.FORM_CONTENT_OVERRIDE
                and api_settings.FORM_CONTENTTYPE_OVERRIDE):
            return None  # pragma: no cover

        # Check permissions
        obj = getattr(view, 'object', None)
        if not self.show_form_for_method(view, method, request, obj):
            return

        content_type_field = api_settings.FORM_CONTENTTYPE_OVERRIDE
        content_field = api_settings.FORM_CONTENT_OVERRIDE
        choices = [(media_type, media_type) for media_type in media_types]
        initial = media_types[0]

        # NB. http://jacobian.org/writing/dynamic-form-generation/
        class GenericContentForm(forms.Form):
            def __init__(self):
                super(GenericContentForm, self).__init__()

                self.fields[content_type_field] = forms.ChoiceField(
                    label='Media type',
                    choices=choices,
                    initial=initial
                )
                self.fields[content_field] = forms.CharField(
                    label='Content',
                    widget=forms.Textarea
                )

        return GenericContentForm()