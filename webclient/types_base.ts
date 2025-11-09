import { assert, hex } from './base.js';
export interface DataViewWithAddr extends DataView {
  addr: bigint;
}

export abstract class Instance {
  abstract type: Type<unknown>;
  constructor(public dv: DataView, public off: bigint) {}
  get sizeof(): bigint | undefined {
    return (this.type as any).sizeof;
  }
}
export interface Type<T> {
  name: string;
  sizeof?: bigint;
  get(dv: DataView, off: bigint): T;
}
class NumericType<T> implements Type<T> {
  constructor(public name: string, public sizeof: bigint, dataViewName: string, needBigintConversion: boolean) {
    let code;
    if (needBigintConversion) {
      code = `return BigInt(dv.get${dataViewName}(Number(off), true));`
    } else {
      code = `return dv.get${dataViewName}(Number(off), true);`
    }
    this.get = new Function('dv', 'off', code) as any;
  }
  get(dv: DataView, off: bigint): T {
      throw new Error('should be replaced by dynamic function');
  }
}


export interface ArrayType<ItemI> extends Type<Array<ItemI>> {
  itemType: Type<ItemI>;
}

export interface Array<ItemI> extends Instance {
  type: ArrayType<ItemI>;
  at(n: number | bigint): ItemI;
}


export function createArrayType<ItemI, ItemTy extends Type<ItemI>>(name_: string, count_: bigint, itemTy_: ItemTy): ArrayType<ItemI> {
    const sizeOfItem = (): bigint => {
      assert(itemTy_.sizeof !== undefined);
      return itemTy_.sizeof;
    };
    class A extends Instance {
      get type(): typeof A { return A; }
      static readonly name: string = name_;
      static readonly itemType: ItemTy = itemTy_;
      static readonly count: bigint = count_;
      static get sizeof(): bigint { return sizeOfItem() * count_; }
      static get(dv: DataView, off: bigint): Array<ItemI> {
        return new A(dv, off);
      }
      at(n: number | bigint): ItemI {
        n = BigInt(n);
        if (n < 0 || n >= this.type.count) {
          throw new Error(`out-of-bounds array index: ${n}, count = ${this.type.count}`);
        }
        return itemTy_.get(this.dv, this.off + sizeOfItem() * n);
      }
    };
    return A;
}

export interface PointerType<PointeeI> extends Type<Pointer<PointeeI>> {
  pointeeType: Type<PointeeI>;
}
export interface Pointer<PointeeI> {
  type: PointerType<PointeeI>;
  readonly raw: bigint;
  readonly pointee: PointeeI | null;
}
export function createPointerType<PointeeI, PointeeTy extends Type<PointeeI>>(name_: string, pointeeTy_: PointeeTy): PointerType<PointeeI> {
    class P {
      get type(): typeof P { return P; }
      static readonly name: string = name_;
      static readonly pointeeType: PointeeTy = pointeeTy_;
      static readonly sizeof: bigint = 8n;
      static get(dv: DataView, off: bigint): Pointer<PointeeI> {
        return new P(dv, off);
      }
      constructor(public dv: DataView, public off: bigint) {}
      get raw(): bigint {
        return u64.get(this.dv, this.off);
      }
      get pointee(): PointeeI | null {
        const dv = this.dv as DataViewWithAddr;
        const addr = this.raw;
        if (addr === 0n) {
          return null;
        }
        if (dv.addr === undefined) {
          throw new Error("can't dereference pointer with DataView with no addr property");
        }
        let newOff = addr - dv.addr;
        if (newOff < 0 || newOff > dv.byteLength) {
          throw new Error(`out-of-bounds target addr 0x${addr.toString(16)}`);
        }
        return pointeeTy_.get(dv, newOff);
      }
    };
    return P;
}

export abstract class Struct extends Instance {
}

export type u8  = bigint;
export type u16 = bigint;
export type u32 = bigint;
export type u64 = bigint;
export type s8  = bigint;
export type s16 = bigint;
export type s32 = bigint;
export type s64 = bigint;
export type f32 = number;
export type f64 = number;

export const u8 =  new NumericType<u8 >('u8',  1n, 'Uint8', true);
export const u16 = new NumericType<u16>('u16', 2n, 'Uint16', true);
export const u32 = new NumericType<u32>('u32', 4n, 'Uint32', true);
export const u64 = new NumericType<u64>('u64', 8n, 'BigUint64', false);
export const s8  = new NumericType<s8>('s8',  1n, 'Int8', true);
export const s16 = new NumericType<s16>('s16', 2n, 'Int16', true);
export const s32 = new NumericType<s32>('s32', 4n, 'Int32', true);
export const s64 = new NumericType<s64>('s64', 8n, 'BigInt64', false);
export const f32 = new NumericType<number>('f32', 4n, 'Float32', false);
export const f64 = new NumericType<number>('f64', 8n, 'Float64', false);

export class Void extends Instance {
  get type() : typeof Void { return Void; }
  static readonly name: string = 'Void';
  static get(dv: DataView, off: bigint): Void {
    return new Void(dv, off);
  }
}

export const GuestPtr: PointerType<Void> = createPointerType('GuestPtr', Void);
export type GuestPtr = Pointer<Void>;

export class Angle extends Struct {
  static name: string = "Angle";
  static sizeof: bigint = 4n;
  static get(dv: DataView, off: bigint) { return new Angle(dv, off); }
  get type(): typeof Angle { return Angle; }
}

export class GuestCString extends Instance {
  static name: string = "GuestCString";
  get type() : typeof GuestCString { return GuestCString; }
  static get(dv: DataView, off: bigint) { return new GuestCString(dv, off); }
}

export class Tag8 {
  static readonly sizeof = 8n;
  static get(dv: DataView, off: bigint): string {
    let bytes = [];
    for (let i = 0n; i < 8n; i++) {
      bytes.push(dv.getUint8(Number(off + i)));
    }
    while (bytes.length > 0 && bytes[bytes.length - 1] == 0) {
      bytes.pop();
    }
    return String.fromCharCode(...bytes);
  }
}
