export function hex(n: number | bigint): string {
  return '0x'+n.toString(16);
}
export function assert(x: boolean): asserts x {
  if (!(x)) {
    throw new Error('assertion failed');
  }
}
export function yieldMe(): Promise<void> {
  let { promise, resolve, reject } = Promise.withResolvers<void>();
  queueMicrotask(resolve);
  return promise;
}
export function hexbuf(array: bigint[]): string {
  return array.map(num => num.toString(16).padStart(2, '0')).join('');
}

/*
export function notNull<T>(val: T | null): T {
  if (val === null) {
    throw new Error('unexpectedly null');
  }
  return val;
}
*/
/*
export function unwrap<T>(val: T | undefined): T {
  if (val === undefined) {
    throw new Error('unexpectedly undefined');
  }
  return val;
}
*/
export function downcast(ty: any, val: unknown): InstanceType<typeof ty> {
  if (!(val instanceof ty)) {
    console.error('downcast fail:', ty, val);
    throw new Error('downcast fail');
  }
  return val;
}
export function downcastString(val: unknown): string {
  if (typeof val == 'string') {
    return val;
  }
  if (val instanceof String) {
    return ''+val;
  }
  console.error('downcastString fail:', val);
  throw new Error('downcastString fail');
}
export function sleep(n: number): Promise<void> {
  let { promise, resolve, reject } = Promise.withResolvers<void>();
  setTimeout(resolve, n);
  return promise;
}

