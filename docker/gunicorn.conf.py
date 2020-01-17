import multiprocessing


bind = '0.0.0.0:8000'
workers = multiprocessing.cpu_count() * 2 + 1
threads = workers
name = 'rapidpro'
env = 'DJANGO_SETTINGS_MODULE=temba.settings'
proc_name = 'rapidpro'
default_proc_name = proc_name
loglevel = 'debug'
accesslog = 'gunicorn.access'
errorlog = 'gunicorn.error'
timeout = 120
chdir = '/app'
capture_output = True
