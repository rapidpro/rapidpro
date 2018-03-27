# -*- coding: utf-8 -*-
def branding(request):
    """
    Stuff our branding into the context
    """
    return dict(brand=request.branding)
