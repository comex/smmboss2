# /// script
# dependencies = [
#     "PyYAML",
# ]
# ///

import smmboss
import guest_access
import re

def all_keys(iterable_of_iterables):
    return list({key: None
                 for outer in iterable_of_iterables
                 for key in outer})

class Absent:
    pass
ABSENT = Absent()

def rotate(items_iter_or_dict, ignore_missing=False):
    '''
    converts
    {'a': {'x': 1, 'y': 2}, 'b': {'x': 3, 'x': 4}}
    to
    {'x': {'a': 1, 'b': 3}, 'y': {'a': 2, 'b': 4}}
    '''
    if isinstance(items_iter_or_dict, dict):
        items_iter = items_iter_or_dict.items()
    else:
        items_iter = iter(items_iter_or_dict)
    items = list(items_iter)
    return {
        inner_key: {
            outer_key: inner[inner_key]
            for (outer_key, inner) in items
            if not ignore_missing or inner_key in inner
        }
        for inner_key in all_keys((inner for (outer_key, inner) in items))
    }

def only(dict_or_iterable):
    if isinstance(dict_or_iterable, dict):
        my_iter = dict_or_iterable.items()
    else:
        my_iter = enumerate(dict_or_iterable)
    have_oval = False
    for (key, val) in dict_or_iterable.items():
        if not have_oval:
            okey = key
            oval = val
            have_oval = True
        else:
            if oval != val:
                raise ValueError(f'only: got conflict: [{okey}]=[{oval}] but [{key}]=[{val}]')
    if not have_oval:
        raise ValueError(f'only: got no valid values: {dict_or_iterable}')
    return oval

def json_info_for_build(build_id, classes_to_export):
    mm = smmboss.MM.detached(build_id=build_id)
    world = mm.world
    cls_infos = {}

    def add_info_for(cls):
        if cls.__name__ in cls_infos:
            return
        cls_infos[cls.__name__] = None
        sizeof = getattr(cls, 'sizeof_star', None)
        if isinstance(sizeof, property):
            sizeof = None
        if issubclass(cls, world.GuestStruct):
            base = cls._base_guest_struct()
            if base is not None:
                add_info_for(base)

            cls_info = {
                'kind': 'struct',
                'sizeof': sizeof,
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
            add_info_for(cls.val_ptr_ty)
            cls_info = {
                'kind': 'fixed_array',
                'count': cls.count,
                'val_ptr_cls': cls.val_ptr_ty.__name__,
            }
        elif ((issubclass(cls, world.GuestPrimPtr) and hasattr(cls, 'code')) or
              cls is world.GuestPtr or
              cls is world.GuestCString):
            cls_info = {
                'kind': 'primitive',
                'sizeof': sizeof,
                'is_float': isinstance(cls, world.GuestPrimPtr) and cls.code in 'fd',
                'is_integer': isinstance(cls, world.GuestPrimPtr) and cls.code not in 'fd',
            }
        else:
            raise Exception(f"? {cls}")
        cls_infos[cls.__name__] = cls_info

    for cls_name in classes_to_export:
        cls = getattr(world, cls_name)
        add_info_for(cls)

    return {
        'classes': cls_infos,
        'addrs': smmboss.get_addrs_yaml()[build_id],
    }

def build_id_for_version(version):
    rets = [build_id for (build_id, info) in smmboss.get_addrs_yaml().items()
            if info.get('version') == version]
    assert len(rets) == 1, rets
    return rets[0]


RELEVANT_VERSIONS = [301, 302]
ROOT_CLASSES = [
    'AreaSystem',
    'Hitbox',
    'ColliderSegment',
    'Scol',
    'CollisionPoint',
]

def json_info():
    return {
        build_id: json_info_for_build(build_id, ROOT_CLASSES)
        for build_id in map(build_id_for_version, RELEVANT_VERSIONS)
    }

def write_json_info(fp):
    data = json_info()
    import json
    json.dump(data, fp, indent=2)
    fp.write('\n')

    pass

def git_describe():
    import subprocess
    return subprocess.check_output(['git', 'describe', '--always', '--dirty'], text=True).strip()

def merge_versions(ver_to_val, path=()):
    assert ver_to_val
    have_ret = False
    val_ty = type(next(iter(ver_to_val.values())))
    if issubclass(val_ty, dict):
        rotated = rotate(ver_to_val, ignore_missing=False)
        return {key2: merge_versions(ver_to_val2, path + (key2,)) for (key2, ver_to_val2) in rotated.items()}
    try:
        return only(ver_to_val)
    except ValueError:
        pass
    # Okay, there is a conflict.
    ignore_conflict = path and (
        path[0] == 'addrs'
    )
    if ignore_conflict:
        return None
    raise Exception(f'merge_versions: unexpected conflict at path {path}: {ver_to_val}')
    

def write_typescript(fp):
    def p(*args):
        print(*args, file=fp)
    p(f'''
// autogenerated by py/export.py from rev {git_describe()}
// do not edit
''')
    jsi = json_info()
    merged = merge_versions(jsi)
    p('type MMAddrs = {')
    for name in merged['addrs']:
        p(f'  {name}?: number;')
    p('};')

    def munge(name: str) -> str:
        return re.sub(r'[\(\),]', '_', re.sub(r'\s', '', name))

    for cls_name, cls_info in merged['classes'].items():
        cls_name = munge(cls_name)
        match cls_info['kind']:
            case 'struct':
                base = cls_info['base'] or 'Struct'
                p(f'export class {cls_name} extends {base} {{')
                for prop_name, prop_info in cls_info['props'].items():
                    prop_ty = munge(prop_info['ptr_cls'])
                    p(f'  offsetof_{prop_name}: number;')
                    p(f'  get {prop_name}() {{ return {prop_ty}.get(this.dv, this.off + this.offsetof_{prop_name}; }}')
                p(f'}}')
            case 'primitive':
                pass
            case 'fixed_array':
                val_ptr_cls = munge(cls_info['val_ptr_cls'])
                p(f'class {cls_name} extends GuestArray<{val_ptr_cls}> {{')
                p(f'  static readonly Element = {val_cls};')
                p(f'  static readonly count = {cls_info['count']};')
                p(f'}}')
            case 'ptr':
                val_cls = munge(cls_info['val_cls'])
                p(f'class {cls_name} extends GuestPtr<{val_cls}> {{')
                p(f'  static readonly Pointee = {val_cls};')
                p(f'}}')
            case _:
                raise ValueError(kind)

def write_cpp_header(fp):
    def p(*args):
        print(*args, file=fp)
    p(f'''
#pragma once
// autogenerated by py/export.py from rev {git_describe()}
// do not edit
#include <stdint.h>
#include <stdlib.h>
#include <array>
constexpr uintptr_t MISSING_ADDR = 0xee;
using BuildId = std::array<uint8_t, 16>;

''')
    p('enum mm_version : uint8_t {')
    for ver in RELEVANT_VERSIONS:
        p(f'    VER_{ver},')
    p('    VER_COUNT,')
    p('};')
    p()
    p('static constexpr BuildId s_version_to_build_id[VER_COUNT] = {')
    for ver in RELEVANT_VERSIONS:
        bid = build_id_for_version(ver)
        bid_bytes = bytes.fromhex(bid[:32])
        bid_hex = ', '.join(f'0x{byte:02x}' for byte in bid_bytes)
        p(f'    [VER_{ver}] = {{ {bid_hex} }},')
    p('};')
    p('extern uintptr_t get_addr_impl(const uintptr_t (&by_ver)[VER_COUNT], const char *name);')
    p()
    yaml_data = {ver: smmboss.get_addrs_yaml()[build_id_for_version(ver)] for ver in RELEVANT_VERSIONS}
    addrs = rotate(rotate(yaml_data)['addrs'], default=None)
    p('struct mm_addrs {')
    for name, addrs_by_ver in addrs.items():
        p(f'    static inline uintptr_t {name}() {{')
        p('        static const uintptr_t by_ver[VER_COUNT] = {')
        for ver, addr in addrs_by_ver.items():
            if addr is None:
                addr_str = 'MISSING_ADDR'
            else:
                addr_str = f'{addr:#x}'
            p(f'            [VER_{ver}] = {addr_str},')
        p('        };')
        p(f'        return get_addr_impl(by_ver, "{name}");')
        p('    }')
    p('};')

# TODO: support autogenerating C++ structs too

def main():
    import json, sys
    from pathlib import Path
    mode = sys.argv[1]
    out_file = Path(sys.argv[2])
    deps_file = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    try:
        with open(out_file, 'w') as fp:
            if mode == 'json':
                write_json_info(fp)
            elif mode == 'typescript':
                write_typescript(fp)
            elif mode == 'cpp_header':
                write_cpp_header(fp)
            else:
                raise Exception(f'unknown mode {mode!r}')
        if deps_file is not None:
            py_dir = Path(__file__).resolve().parent
            dep_paths = []
            for mod in sys.modules.values():
                try:
                    path = mod.__file__
                except AttributeError:
                    continue
                path = Path(path).resolve()
                if path.is_relative_to(py_dir):
                    dep_paths.append(path)
            dep_paths.append(Path(smmboss.addrs_yaml_path()).resolve())

            dep_paths.extend(map(Path, guest_access.extra_dep_filenames))

            with open(deps_file, 'w') as fp:
                fp.write(f'{str(out_file)}: \\\n')
                for path in dep_paths:
                    fp.write(f' {str(path)}\\\n')
                fp.write('\n')
    except:
        out_file.unlink(missing_ok=True)
        if deps_file is not None:
            deps_file.unlink(missing_ok=True)
        raise

if __name__ == '__main__':
    main()
