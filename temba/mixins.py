from django.contrib import messages
from django.shortcuts import redirect


class NotFoundRedirectMixin:
    """
    Mixture for redirection when model object can't be found.

    :param redirect_checking_model: a model that is using for filtering, by default it's a model of view.
    :param redirect_params: dict with configuratuions

        :param filter_key: keyword that is using for model filtering, by default is same as `filter_value`
        :param filter_value: keyword for selecting value from the request kwargs to be used for model filtering
        :param model_manager: using to select exact manager of model
        :param message: message for messages the messages framework

    :param redirect_url: an url for redict, when object not found

    """
    redirect_checking_model = None
    redirect_params = {
        "filter_key": None,
        "filter_value": 'pk',
        "model_manager": 'objects',
        "message": '',
    }
    redirect_url = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.redirect_checking_model is None:
            self.redirect_checking_model = getattr(self, "model")
        
        if not self.redirect_params['filter_key']:
            self.redirect_params['filter_key'] = self.redirect_params['filter_value']

    def dispatch(self, request, *args, **kwargs):        
        if self.redirect_url and self.redirect_checking_model:
            checking_value = kwargs.get(self.redirect_params['filter_value'])
            is_exists = (
                getattr(self.redirect_checking_model, self.redirect_params["model_manager"])
                .filter(**{self.redirect_params['filter_key']: checking_value})
                .exists()
            )

            if not is_exists:
                if self.redirect_params.get("message"):
                    messages.error(request, self.redirect_params["message"])
                return redirect(self.redirect_url)
        
        return super().dispatch(request, *args, **kwargs)
