from django.contrib.auth.models import User

field = User._meta.get_field('email')
field.max_length = 254
field = User._meta.get_field('username')
field.max_length = 254
