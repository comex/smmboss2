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
    data = get_it()
    import json, sys
    json.dump(data, sys.stdout, indent=2)
    print()

if __name__ == '__main__':
    main()
