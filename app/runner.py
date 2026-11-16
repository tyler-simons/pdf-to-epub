"""Apply resource ceilings before loading the PDF engine. No provider keys inherited."""
import os
import runpy
import sys
if sys.platform.startswith('linux'):
    import resource
    seconds=int(os.environ.get('LEAF_CHILD_SECONDS','300'))
    resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3))
    resource.setrlimit(resource.RLIMIT_CPU,(seconds,seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE,(128*1024**2,128*1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE,(128,128))
runpy.run_module('app.converter',run_name='__main__')
