# require cookies to be on https (ergo logged in users must be on https)
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_AGE = 1209600  # 2 weeks

# settings used by SecurityMiddleware
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
SECURE_HSTS_SECONDS = 86400  # 24 hours
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "HTTPS")
SECURE_REDIRECT_EXEMPT = []
SECURE_SSL_HOST = None
SECURE_SSL_REDIRECT = False

# cross-site request forgery prevention
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_AGE = 1209600  # 2 weeks

# password requirements
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
