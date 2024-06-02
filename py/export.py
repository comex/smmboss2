import smmboss
def get_info_for_build(build_id, classes_to_export):
    mm = smmboss.MM.detached(build_id=build_id)
    world = mm.world
    cls_infos = {}

    def add_info_for(cls):
        if cls.__name__ in cls_infos:
            return
        cls_infos[cls.__name__] = None
        if issubclass(cls, world.GuestStruct):
            base = cls._base_guest_struct()
            cls_info = {
                'kind': 'struct',
                'base': base and base.__name__,
                'props': {},
            }
            for prop_name, prop in cls._properties():
                assert isinstance(prop, world.MyProperty)
                add_info_for(prop.ptr_cls)
                prop_info = {
                    'offset': prop.offset,
                    'ptr_cls': prop.ptr_cls.__name__,
                }
                cls_info['props'][prop_name] = prop_info
        elif issubclass(cls, world.GuestPtrPtrBase):
            add_info_for(cls.val_ty)
            cls_info = {
                'kind': 'ptr',
                'val_cls': cls.val_ty.__name__,
            }
        elif issubclass(cls, world.GuestFixedArrayBase):
            cls_info = {
                'kind': 'fixed_array',
                'count': cls.count,
                'val_ptr_cls': cls.val_ptr_ty.__name__,
            }
        elif ((issubclass(cls, world.GuestPrimPtr) and hasattr(cls, 'code')) or
              cls is world.GuestPtr or
              cls is world.GuestCString):
            cls_info = {'kind': 'primitive'}
        else:
            raise Exception(f"? {cls}")
        cls_infos[cls.__name__] = cls_info

    for cls_name in classes_to_export:
        cls = getattr(world, cls_name)
        add_info_for(cls)

    return {
        'classes': cls_infos,
        **smmboss.get_addrs_yaml()[build_id],
    }

def build_id_for_version(version):
    rets = [build_id for (build_id, info) in smmboss.get_addrs_yaml().items()
            if info.get('version') == version]
    assert len(rets) == 1, rets
    return rets[0]

def get_all(root_classes, relevant_versions):
    return {
        build_id: get_info_for_build(build_id, root_classes)
        for build_id in map(build_id_for_version, relevant_versions)
    }

def get_it():
    return get_all(
        root_classes=[
            'AreaSystem',
            'Hitbox',
        ],
        relevant_versions=[
            301,
            302,
        ]
    )

def main():
    import json, sys
    from pathlib import Path
    out_file = Path(sys.argv[1])
    deps_file = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    data = get_it()
    try:
        with open(out_file, 'w') as fp:
            json.dump(data, fp, indent=2)
            fp.write('\n')
        if deps_file is not None:
            py_dir = Path(__file__).resolve().parent
            with open(deps_file, 'w') as fp:
                fp.write(f'{str(out_file)}: \\\n')
                for mod in sys.modules.values():
                    try:
                        path = mod.__file__
                    except AttributeError:
                        continue
                    path = Path(path).resolve()
                    if not path.is_relative_to(py_dir):
                        continue
                    fp.write(f' {str(path)}\\\n')
                fp.write('\n')
    except:
        out_file.unlink(missing_ok=True)
        if deps_file is not None:
            deps_file.unlink(missing_ok=True)
        raise

if __name__ == '__main__':
    main()
