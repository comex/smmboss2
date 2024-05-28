started_shell_with = None

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
        f'import {module_name}',
    ]
    IPython.start_ipython(config=c, argv=[])
    os._exit(0)
