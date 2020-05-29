import multiprocessing

workers = multiprocessing.cpu_count() * 2 + 1
threads = workers
# env = 'DJANGO_SETTINGS_MODULE=temba.settings'
proc_name = 'rapidpro'
default_proc_name = proc_name
# loglevel = 'debug'
accesslog = 'gunicorn.access'
errorlog = 'gunicorn.error'
capture_output = True
