import multiprocessing

workers = multiprocessing.cpu_count() * 2 + 1
threads = workers
proc_name = 'rapidpro'
default_proc_name = proc_name
accesslog = 'gunicorn.access'
errorlog = 'gunicorn.error'
capture_output = True
timeout = 120
