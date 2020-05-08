def branding(request):
    """
    Stuff our branding into the context
    """
    if "vanilla" in request.GET:
        request.session["vanilla"] = request.GET.get("vanilla")

    return dict(brand=request.branding, vanilla=request.session.get("vanilla", "0") == "1")
