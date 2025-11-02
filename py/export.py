import re
import json
from pymonad.maybe import Just, Nothing
import smmboss
import guest_access

def mget(dict, key):
    try:
        return Just(dict[key])
    except KeyError:
        return Nothing

def unique_inner_items(iterable_of_iterables):
    return list({key: None
                 for outer in iterable_of_iterables
                 for key in outer})

def mrotate_dict(dict):
    '''see assert below for what this does'''
    return {
        inner_key: {
            outer_key: mget(inner, inner_key)
            for (outer_key, inner) in dict.items()
        }
        for inner_key in unique_inner_items(dict.values())
    }
assert (
    mrotate_dict(
        {'a': {'x': 1, 'y': 2}, 'b': {'x': 3, 'y': 4}, 'c': {'x': 5}}
    )
    ==
    {'x': {'a': Just(1), 'b': Just(3), 'c': Just(5)},
     'y': {'a': Just(2), 'b': Just(4), 'c': Nothing}}
)

def strip_maybe_from_dict_values(dict):
    '''
    converts
    {'foo': Just(1), 'bar': Nothing}
    to
    {'foo': 1}
    '''
    return {key: maybeval.value
        for (key, maybeval) in dict.items()
        if maybeval.is_just()}

def only(iterable):
    got_any = False
    ret = Nothing
    for item in iterable:
        if not got_any:
            first = item
            ret = Just(item)
            got_any = True
        elif item != first:
            ret = Nothing
    return ret

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

def merge_json_dicts(ver_to_maybeval, path=()):
    '''get the subset that is valid for all versions''' 

    any_missing = Nothing in ver_to_maybeval.values()
    ver_to_val = strip_maybe_from_dict_values(ver_to_maybeval)

    val_ty = type(next(iter(ver_to_val.values())))

    ret = Nothing

    if issubclass(val_ty, dict):
        rotated = mrotate_dict(ver_to_val)
        mret = Just({key2: merge_json_dicts(ver_to_maybeval2, path + (key2,))
            for (key2, ver_to_maybeval2) in rotated.items()})
    else:
        mret = only(ver_to_val.values())

    match path:
        case (('addrs', 'version') |
              ('addrs', 'addrs', _)):
            # version and addrs will always differ
            return {'any_missing': any_missing}
        case ('classes', _, 'sizeof'):
            return {'any_missing': any_missing, 'any_present': bool(ver_to_val)}

    if mret.is_nothing():
        # Okay, there is a conflict.
        raise Exception(f'merge_versions: unexpected conflict at path {path}: {ver_to_val}')
    ret = mret.value
    if any_missing:
        if len(path) >= 2 and path[-2] in ('props', 'classes'):
            ret['missing_on_some_versions'] = True
        else:
            raise Exception(f'merge_versions: unexpectedly had some versions missing at path {path}')

    return ret

def write_typescript(fp):
    def p(*args):
        print(*args, file=fp)
    ver_to_jsi = json_info()
    merged = merge_json_dicts({ver: Just(jsi) for (ver, jsi) in ver_to_jsi.items()})
    p(f'''
// autogenerated by py/export.py from rev {git_describe()}
// do not edit
''')
    p('import {')
    p('  Struct, Type, u8, u16, u32, u64, s8, s16,')
    p('  s32, s64, f32, f64, ')
    p('  Array, Pointer,')
    p('  ArrayType, PointerType,')
    p('  createArrayType, createPointerType,')
    p('  GuestPtr, Angle, GuestCString,')
    p('} from "types_base";')
    p('type MMAddrs = {')
    for name, x in merged['addrs']['addrs'].items():
        question = '?' if x['any_missing'] else ''
        p(f'  {name}{question}: bigint;')
    p('};')

    def munge(name: str) -> str:
        return re.sub(r'[\(\),]', '_', re.sub(r'\s', '', name))

    did_classes = set()
    def emit_class(cls_name):
        cls_info = merged['classes'][cls_name]
        if cls_name in did_classes:
            return
        did_classes.add(cls_name)
        munged_cls = munge(cls_name)
        match cls_info['kind']:
            case 'struct':
                munged_base = 'Struct'
                if cls_info['base']:
                    emit_class(cls_info['base'])
                    munged_base = munge(cls_info['base'])
                for prop_name, prop_info in cls_info['props'].items():
                    p(f'let offsetof_{munged_cls}_{prop_name}: bigint | undefined = undefined;')
                p(f'export class {munged_cls} extends {munged_base} {{')
                p(f'  static name: string = "{munged_cls}";')
                if not cls_info['sizeof']['any_present']:
                    pass
                elif cls_info['sizeof']['any_missing']:
                    p(f'  static sizeof: bigint? = undefined;')
                else:
                    p(f'  static sizeof: bigint = undefined as any;')
                p(f'  static get(dv: DataView, off: bigint) {{ return new {munged_cls}(dv, off); }}')
                p(f'  get type(): typeof {munged_cls} {{ return {munged_cls}; }}')
                for prop_name, prop_info in cls_info['props'].items():
                    prop_ty = munge(prop_info['ptr_cls'])
                    # prop_cls_info = merged['classes'][prop_info['ptr_cls']]
                    # if prop_cls_info['kind'] == 'primitive':
                    #     prop_ts_type
                    # else:
                    offsetof_var = f'offsetof_{munged_cls}_{prop_name}'
                    p(f'  get {prop_name}(): {prop_ty} {{')
                    if prop_info.get('any_missing') or cls_info.get('any_missing'):
                        p(f'    if ({offsetof_var} === undefined) {{ return undefined; }}')
                    p(f'    return {prop_ty}.get(this.dv, this.off + ({offsetof_var} as bigint));')
                    p(f'  }}')
                p(f'}}')
            case 'primitive':
                pass
            case 'fixed_array':
                emit_class(cls_info['val_ptr_cls'])
                munged_item = munge(cls_info['val_ptr_cls'])
                p(f'export type {munged_cls} = Array<{munged_item}>;')
                p(f'export const {munged_cls}: ArrayType<{munged_item}> = createArrayType("{munged_cls}", {cls_info["count"]}n, {munged_item});')
            case 'ptr':
                emit_class(cls_info['val_cls'])
                munged_pointee = munge(cls_info['val_cls'])
                p(f'export type {munged_cls} = Pointer<{munged_pointee}>;')
                p(f'export const {munged_cls}: PointerType<{munged_pointee}> = createPointerType("{munged_cls}", {munged_pointee});')
            case _:
                raise ValueError(cls_info)
    for cls_name in merged['classes']:
        emit_class(cls_name)

    p('export const setOffsets: { [build_id: string]: () => void } = {')
    for ver, jsi in ver_to_jsi.items():
        p(f'  [{json.dumps(ver)}]: () => {{')
        for cls_name, cls_info in jsi['classes'].items():
            munged_cls = munge(cls_name)
            if cls_info['kind'] == 'struct':
                if cls_info['sizeof'] is not None:
                    p(f'    {munged_cls}.sizeof = {cls_info["sizeof"]}n;')
                for prop_name, prop_info in cls_info['props'].items():
                    p(f'    offsetof_{munged_cls}_{prop_name} = {prop_info["offset"]}n;')
        p(f'  }},')
    p('}')

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
