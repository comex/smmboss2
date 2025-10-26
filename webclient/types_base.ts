export abstract class Struct {
  constructor(public dv: DataView, public off: number) {}
}
export interface Type<T> {
  name: string;
  sizeof?: bigint;
  get(dv: DataView, off: number): T;
}
class NumericType<T> implements Type<T> {

  constructor(public name: string, public sizeof: bigint, dataViewName: string, needBigintConversion: boolean) {
    let code;
    if (needBigintConversion) {
      code = `return BigInt(dv.get${dataViewName}(off, true));`
    } else {
      code = `return dv.get${dataViewName}(off, true);`
    }
    this.get = new Function('dv', 'off', code) as any;
  }
  get(dv: DataView, off: number): T {
      throw new Error('should be replaced by dynamic function');
  }
}

export class Array<ItemI, ItemT extends Type<ItemI>> implements Type<ArrayInstance<ItemI, ItemT>> {
  readonly Instance: (dv: DataView, off: number) => ArrayInstance<ItemI, ItemT>;
  constructor(public name: string, public count: number, public itemTy: ItemT) {
    let arrayThis = this;
    this.Instance = class {
      new 

    };
  }
  get(dv: DataView, off: number): ArrayInstance<ItemI, ItemT> {
    return new ArrayInstance
  }
}
export interface ArrayInstance<ItemI, ItemT extends Type<ItemI>> {
  constructor: Array<ItemI, ItemT>;
}

export const u8 =  new NumericType<bigint>('u8',  1n, 'Uint8', true);
export const u16 = new NumericType<bigint>('u16', 2n, 'Uint16', true);
export const u32 = new NumericType<bigint>('u32', 4n, 'Uint32', true);
export const u64 = new NumericType<bigint>('u64', 8n, 'BigUint64', false);
export const s8  = new NumericType<bigint>('s8',  1n, 'Int8', true);
export const s16 = new NumericType<bigint>('s16', 2n, 'Int16', true);
export const s32 = new NumericType<bigint>('s32', 4n, 'Int32', true);
export const s64 = new NumericType<bigint>('s64', 8n, 'BigInt64', false);
export const f32 = new NumericType<number>('f32', 4n, 'Float32', false);
export const f64 = new NumericType<number>('f64', 8n, 'Float64', false);
