import os
started_shell_with = None

def pre_run_cell(info):
    import guest_access
    guest_access.mark_worlds_stale_if_necessary()

def startup():
    import IPython
    ip = IPython.get_ipython()
    ip.events.register("pre_run_cell", pre_run_cell)

def main(module_name):
    import sys
    import os
    import IPython
    from traitlets.config import Config

    global started_shell_with
    started_shell_with = module_name

    c = Config()
    c.InteractiveShellApp.exec_lines = [
        '%load_ext autoreload',
        '%autoreload 2',
        'import shell',
        'shell.startup()',
        f'import {module_name}',
    ]
    IPython.start_ipython(config=c, argv=[])
    os._exit(0)
