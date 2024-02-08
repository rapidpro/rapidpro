from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse


def make_smtp_url(host: str, port: int, username: str, password: str, from_email: str, tls: bool) -> str:
    """
    Formats an STMP configuration URL from its constituent parts.
    """
    username = quote(username)
    password = quote(password, safe="")
    params = {}
    if from_email:
        params["from"] = f"{from_email.strip()}"
    if tls:
        params["tls"] = "true"

    url = f"smtp://{username}:{password}@{host}:{port}/"
    if params:
        url += f"?{urlencode(params)}"
    return url


def parse_smtp_url(smtp_url: str) -> tuple:
    """
    Parses an STMP configuration URL into a tuple of its constituent parts.
    """

    parsed = urlparse(smtp_url or "")
    tls_param = parse_qs(parsed.query).get("tls")
    from_param = parse_qs(parsed.query).get("from")

    return (
        parsed.hostname,
        parsed.port or 25,
        unquote(parsed.username) if parsed.username else None,
        unquote(parsed.password) if parsed.username else None,
        from_param[0] if from_param else None,
        tls_param[0] == "true" if tls_param else False,
    )
