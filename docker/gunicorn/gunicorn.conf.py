import multiprocessing

workers = multiprocessing.cpu_count() * 2 + 1
proc_name = 'rapidpro'
default_proc_name = proc_name
accesslog = 'gunicorn.access'
errorlog = 'gunicorn.error'
capture_output = True
max_requests = 2000
max_requests_jitter = 100
timeout = 120
