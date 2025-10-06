import traceback
try:
    import sys, os, importlib, subprocess
    from pathlib import Path

    def _sopy_load_into_venv():
        global _sopy_loaded_into_venv
        try:
            _sopy_loaded_into_venv
        except NameError:
            pass
        else:
            return
        py_dir = Path(__file__).parent
        gdb_venv_path = py_dir / '.gdb_venv'
        subprocess.check_call(['uv', 'sync', '-p', sys.executable], cwd=str(py_dir), env={
            **os.environ,
            'UV_PROJECT_ENVIRONMENT': str(gdb_venv_path),
        })
        site_packages_dirs = list(gdb_venv_path.glob('lib/python*/site-packages'))
        assert len(site_packages_dirs) == 1, site_packages_dirs
        sys.path.insert(0, str(site_packages_dirs[0]))
        # also add this directory as an import directory
        sys.path.append(str(py_dir))

    def _sopy_reload_all():
        import guest_access, smmboss, gdb_guest
        importlib.reload(guest_access)
        importlib.reload(smmboss)
        importlib.reload(gdb_guest)
        guest = guest_access.CachingGuest(gdb_guest.GDBGuest())
        mm = smmboss.MM.with_guest(guest)
        gdb_guest.add_niceties(mm)
        for (_attr, _attrval) in mm.world.__dict__.items():
            if not _attr.startswith('_'):
                globals()[_attr] = _attrval

    _sopy_load_into_venv()
    _sopy_reload_all()
except:
    # GDB does not natively print traceback for exceptions encountered while
    # sourcing
    traceback.print_exc()
    raise
