import { assert, yieldMe } from './base.js';

const refCountedFinalizationRegistry = new FinalizationRegistry<RefCount>((refCount) => {
  if (refCount.value) {
    throw new Error('RefCounted destroyed without being disposed');
  }
});

export function dispose(obj: Disposable) {
  obj[Symbol.dispose]();
}

interface Rejectable {
  reject(_: Error): unknown;
}

class DisposedError extends Error {
  constructor() {
    super('DisposedError');
  }
}
const DISPOSED_ERROR = new DisposedError;

class RefCount {
  value: number = 1;
}
export class RefCounted {
  readonly refCount: RefCount;
  readonly disposeStack: DisposableStack = new DisposableStack();
  isDead(): boolean {
    return this.refCount.value == 0;
  }
  use<T extends Disposable | HotPotato<RefCounted>>(val: T): (T extends HotPotato<infer U> ? U : T) {
    if (val instanceof HotPotato) {
      return this.use(val.used()) as any;
    }
    return this.disposeStack.use(val) as any;
  }
  useRejectable<T extends Rejectable>(val: T): T {
    this.disposeStack.defer(() => val.reject(DISPOSED_ERROR));
    return val;
  }
  useEventListener(target: EventTarget, event: string, callback: EventListenerOrEventListenerObject, options?: AddEventListenerOptions | boolean): void {
    target.addEventListener(event, callback, options);
    this.disposeStack.defer(() => {
      target.removeEventListener(event, callback, options);
    });
  };
  constructor() {
    this.refCount = new RefCount;
    refCountedFinalizationRegistry.register(this, this.refCount);
  }
  _destructor(): void {}
  [Symbol.dispose](): void {
    this.unref();
  }
  unref(): void {
    const newValue = --this.refCount.value;
    if (newValue == 0) {
      this._destructor();
      this.disposeStack.dispose();
    } else if (newValue < 0) {
      throw new Error('excess dispose on RefCounted');
    }
  }
  ref() {
    const oldValue = this.refCount.value++;
    if (oldValue === 0) {
      throw new Error('resurrecting RefCounted');
    }
    return this;
  }
}

class HotPotato<out T extends RefCounted> {
  readonly _val: T;
  constructor(val: T) {
    this._val = val;
  }
  used(): T {
    return this._val;
  }
}

interface SomeSignalLink {
  kill(): void;
}
interface SignalLinkOut<in SourceT> {
  update(isException: boolean, valueOrException: SourceT | Error, link: SomeSignalLink | undefined): void;
  kill(): void;
}
class SignalLink<SourceT> implements SignalLinkOut<SourceT> {
  constructor(
    public readonly source: SignalRO<SourceT>,
    public readonly target: SomeSignal | null,
    public readonly update: (isException: boolean, valueOrException: SourceT | Error, link: SomeSignalLink | undefined) => void
  ) {}
  static create<SourceU>(
    source: SignalRO<SourceU>, // strong
    target: SomeSignal | null, // weak
    update: (isException: boolean, valueOrException: SourceU | Error, link: SomeSignalLink | undefined) => void
  ): SignalLink<SourceU> | null {
    if (target !== null && target.hasException) {
      return null;
    }
    if (source.hasException) {
      update(true, source.valueOrException as SourceU, undefined);
      return null;
    }
    let link = new SignalLink<SourceU>(source.ref(), target, update);
    if (target !== null) {
      target.addLinkIn(link);
    }
    source.addLinkOut(link);
    if (source.hasValue) {
      update(false, source.valueOrException as Error, link);
    }
    return link;
  }
  kill(): void {
    this.source.removeLinkOut(this);
    this.source.unref();
    if (this.target !== null) {
      this.target.removeLinkIn(this);
    }
  }
}

interface SomeSignal {
  readonly hasException: boolean;
  addLinkIn(link: Kill): void;
  removeLinkIn(link: Kill): void;
}
export interface SignalRO<out T> extends RefCounted {
  get cur(): T;
  get maybeCur(): T | undefined;
  [Symbol.asyncIterator](): AsyncIterator<T, any>;
  then<TResult1, TResult2 = never>(onfulfilled?: ((value: T) => TResult1 | PromiseLike<TResult1>) | undefined | null, onrejected?: ((reason: any) => TResult2 | PromiseLike<TResult2>) | undefined | null): Promise<TResult1 | TResult2>;
  map<U>(f: (_: T) => U): HotPotato<SignalRO<U>>;
  bind<U>(f: (_: T) => HotPotato<SignalRO<U>> | U): HotPotato<SignalRO<U>>;
  hasValueSig(): HotPotato<Signal<boolean>>;
  addLinkOut(link: SignalLinkOut<T>): void;
  removeLinkOut(link: SignalLinkOut<T>): void;
  readonly hasValue: boolean;
  readonly hasException: boolean;
  readonly valueOrException: T | Error | undefined;

}

type SignalVal<T> = T extends Signal<infer U> ? U : never;

class _NoInitialValueDontUse {}
const NO_INITIAL_VALUE_DONT_USE = new _NoInitialValueDontUse

export class Signal<T> extends RefCounted implements SignalRO<T> {
  linksIn: Set<SomeSignalLink>;
  linksOut: Set<SignalLinkOut<T>>;
  hasValue: boolean;
  hasException: boolean;
  valueOrException: T | Error | undefined;
  private constructor(hasValue: boolean, valueOrException: T | undefined) {
    super();
    this.linksIn = new Set;
    this.linksOut = new Set;
    this.hasException = false;
    this.hasValue = hasValue;
    this.valueOrException = valueOrException as T;
  }
  static of<U>(valueOrSig: SignalRO<U> | HotPotato<SignalRO<U>> | U): HotPotato<SignalRO<U>> {
    if (valueOrSig instanceof Signal) {
      return new HotPotato(valueOrSig.ref());
    } else if (valueOrSig instanceof HotPotato) {
      assert(valueOrSig._val instanceof Signal);
      return valueOrSig;
    } else {
      return new HotPotato(new Signal(true, valueOrSig as U));
    }
  }
  static create<U>(initialValue: U): HotPotato<Signal<U>> {
    return new HotPotato(new Signal<U>(true, initialValue));
  }
  static withoutInitialValue<U>(): HotPotato<Signal<U>> {
    return new HotPotato(new Signal<U>(false, undefined));
  }
  bump(): void {
    assert(this.hasValue || this.hasException);
    for (let link of this.linksOut) {
      link.update(this.hasException, this.valueOrException as (T | Error), link);
    }
  }
  get cur(): T {
    if (this.hasException) {
      throw this.valueOrException;
    } else if (this.hasValue) {
      return this.valueOrException as T;
    } else {
      throw new Error('no cur');
    }
  }
  get maybeCur(): T | undefined {
    if (this.hasException) {
      throw this.valueOrException;
    } else {
      return this.valueOrException as T;
    }
  }
  set(value: T): void {
    if (this.hasException) {
      return;
    }
    if (this.hasValue && this.valueOrException === value) {
      return;
    }
    this.valueOrException = value;
    this.hasValue = true;
    this.bump();
  }
  reject(exception: Error): void {
    if (this.hasException) {
      return;
    }
    this.valueOrException = exception;
    this.hasException = true;
    this.hasValue = false;
    this.bump();
    for (let link of this.linksIn) {
      link.kill();
    }
    for (let link of this.linksOut) {
      link.kill();
    }
  }

  [Symbol.asyncIterator](): AsyncIterator<T, any> {
    return new SignalAsyncIterator(this);
  }

  // this signature copy+pasted from TypeScript:
  then<TResult1, TResult2 = never>(onfulfilled?: ((value: T) => TResult1 | PromiseLike<TResult1>) | undefined | null, onrejected?: ((reason: any) => TResult2 | PromiseLike<TResult2>) | undefined | null): Promise<TResult1 | TResult2> {
    let pr = Promise.withResolvers<T>();
    let stillCreating = true;
    SignalLink.create(this, null, (hasException, valueOrException) => {
      if (stillCreating) {
        return;
      }
      if (hasException) {
        pr.reject(valueOrException);
      } else {
        pr.resolve(valueOrException as T);
      }
    });
    stillCreating = false;
    return pr.promise.then(onfulfilled, onrejected);
  }

  map<U>(f: (_: T) => U): HotPotato<Signal<U>> {
    using mappedSignal = Signal.withoutInitialValue<U>().used();
    SignalLink.create(this, mappedSignal, (hasException, valueOrException) => {
      if (hasException) {
        mappedSignal.reject(valueOrException as Error);
      } else {
        mappedSignal.set(f(valueOrException as T));
      }
    });
    return new HotPotato(mappedSignal.ref());
  }

  bind<U>(f: (_: T) => HotPotato<SignalRO<U>> | U): HotPotato<Signal<U>> {
    using boundSignal = Signal.withoutInitialValue<U>().used();
    let forwardingLink: SomeSignalLink | null = null;
    SignalLink.create(this, boundSignal, (hasException, valueOrException) => {
      if (forwardingLink !== null) {
        forwardingLink.kill();
        forwardingLink = null;
      }
      if (hasException) {
        boundSignal.reject(valueOrException as Error);
      } else {
        using forwarderSignal = Signal.of(f(valueOrException as T)).used();
        forwardingLink = SignalLink.create(forwarderSignal, boundSignal, (hasException2, valueOrException2) => {
          if (hasException2) {
            boundSignal.reject(valueOrException2 as Error);
          } else {
            boundSignal.set(valueOrException2 as U);
          }
        });
      }
    });
    return new HotPotato(boundSignal.ref());
  }

  static combine<Obj>(obj: Obj):
    HotPotato<Signal<{ [Key in keyof Obj]: Obj[Key] extends Signal<infer T> ? T : Obj[Key] }>>
  {
    using retSignal = Signal.withoutInitialValue().used();
    let numMissingVals = 1; // add 1 temporarily so we don't set retSignal during the loop
    let retValue: any = {};
    for (const key in obj) {
      let sig = obj[key];
      if (!(sig instanceof Signal)) {
        retValue[key] = sig;
        continue;
      }
      let hasVal = false;
      numMissingVals++;
      SignalLink.create(sig, retSignal, (hasException, valueOrException) => {
        if (hasException) {
          retSignal.reject(valueOrException as Error);
          return;
        }
        if (!hasVal) {
          hasVal = true;
          numMissingVals--;
        }
        retValue[key] = valueOrException;
        if (numMissingVals) {
          // Some keys are still missing values.
          return;
        }
        retSignal.set({...retValue});
      });
    }
    --numMissingVals; // remove the temporary addition
    if (!numMissingVals) {
      retSignal.set({...retValue});
    }
    return new HotPotato(retSignal.ref() as any);
  }

  hasValueSig(): HotPotato<Signal<boolean>> {
    using ret = this.map((_) => true).used();
    if (!this.hasValue && !this.hasException) {
      ret.set(false);
    }
    return new HotPotato(ret.ref());
  }

  _destructor(): void {
    if (!this.hasException) {
      this.reject(DISPOSED_ERROR);
    }
  }
  addLinkOut(link: SignalLinkOut<T>): void {
    this.linksOut.add(link);
  }
  removeLinkOut(link: SignalLinkOut<T>): void {
    this.linksOut.delete(link);
  }
  addLinkIn(link: SomeSignalLink): void {
    this.linksIn.add(link);
  }
  removeLinkIn(link: SomeSignalLink): void {
    this.linksIn.delete(link);
  }
}

export class SignalAsyncIterator<T, TReturn> implements AsyncIterator<T, TReturn> {
  state: number;
  signal: SignalRO<T>;
  curPR: PromiseWithResolvers<IteratorYieldResult<T>> | null;
  link: SignalLink<T> | null;
  constructor(signal: SignalRO<T>) {
    this.state = 1;
    this.signal = signal;
    this.curPR = null;
    this.link = SignalLink.create<T>(signal, null, (hasException, valueOrException) => {
      if (this.state == -1) {
        this.state = 0;
        let curPR = this.curPR;
        this.curPR = null;
        assert(curPR !== null);
        if (hasException) {
          curPR.reject(valueOrException);
        } else {
          curPR.resolve({done: false, value: valueOrException as T});
        }
      } else if (this.state == 0) {
        this.state = 1;
      }
    });
  }
  [Symbol.asyncIterator]() { return this; }
  next(): Promise<IteratorResult<T>> {
    if (this.state == 1) {
      this.state = 0;
      if (this.signal.hasException) {
        return Promise.reject(this.signal.valueOrException);
      } else if (this.signal.hasValue) {
        return Promise.resolve({done: false, value: this.signal.valueOrException as T});
      }
    }
    if (this.state == 0) {
      this.state = -1;
      this.curPR = Promise.withResolvers();
    }
    assert(this.curPR !== null);
    return this.curPR.promise;
  }
  async return(value: TReturn | PromiseLike<TReturn>): Promise<IteratorReturnResult<TReturn>> {
    if (this.link !== null) {
      this.link.kill();
    }
    return {value: await value, done: true};
  }
  throw(e?: any): Promise<IteratorResult<T, TReturn>> {
    if (this.link !== null) {
      this.link.kill();
    }
    return Promise.reject(e);
  }
}

export interface Kill {
  kill: () => void;
}
export interface Reject {
  reject: (reason?: any) => void;
}

function testCheck(cond: boolean): void {
  let junk: string;
  if (cond) {
    junk = ' OK';
  } else {
    let obj = new Error();
    (Error as any).captureStackTrace(obj);
    junk = '\n' + obj.stack!.toString() + '\n';
  }
  document.getElementById('results')!.append(document.createTextNode(junk));
}

function testSignalBind(): void {
  using holder = new RefCounted;
  const sigList: any[] = [
    holder.use(Signal.create(10)),
    holder.use(Signal.withoutInitialValue()),
    1000
  ];
  testCheck(sigList[1].hasValue === false);
  using indexSig = Signal.create(2).used();
  let indexedSigRefCount;
  {
    using indexedSig = indexSig.bind(i => sigList[i]).used();
    indexedSigRefCount = indexedSig.refCount;
    testCheck(indexedSigRefCount.value == 1);
    testCheck(indexedSig.cur === 1000);
    indexSig.set(1);
    testCheck(indexedSig.cur === 1000);
    sigList[1].set(123);
    testCheck(indexedSig.cur === 123);
    // We don't reevaluate the function 'just because', so we have a stale value:
    sigList[1] = holder.use(Signal.create(124));
    testCheck(indexedSig.cur === 123);
    // But we do eventually reevaluate:
    indexSig.set(0);
    indexSig.set(1);
    testCheck(indexedSig.cur === 124);
    // Make sure stale updates don't change the value:
    indexSig.set(0);
    testCheck(indexedSig.cur === 10);
    sigList[1].set(125);
    testCheck(indexedSig.cur === 10);
  }
  // Make sure kill cleans up after itself:
  testCheck(indexedSigRefCount.value == 0);
  testCheck(sigList[0].linksOut.size === 0);
}

function testSignalMap(): void {
  using signal = Signal.withoutInitialValue<number>().used();
  {
    using mappedSig = signal.map(x => 2 * x).used();
    testCheck(!mappedSig.hasValue);
    signal.set(10);
    testCheck(mappedSig.cur == 20);
  }
  // Make sure kill cleans up after itself:
  testCheck(signal.linksOut.size === 0);
}

function testSignalCombine(): void {
  using signalA = Signal.withoutInitialValue<string>().used();
  using signalB = Signal.withoutInitialValue<string>().used();
  using mappedSig = Signal.combine({a: signalA, b: signalB, c: 123}).used();
  testCheck(!mappedSig.hasValue);
  signalA.set('a');
  testCheck(!mappedSig.hasValue);
  signalB.set('b');
  testCheck(mappedSig.hasValue);
  const firstVal = mappedSig.cur;
  testCheck(firstVal.a == 'a');
  testCheck(firstVal.b == 'b');
  testCheck(firstVal.c == 123);
  signalA.set('d');
  testCheck(mappedSig.cur.a == 'd');
  testCheck(mappedSig.cur.b == 'b');
  testCheck(firstVal.a == 'a');
}

async function testSignalAsyncIterate(): Promise<void> {
  using signal = Signal.withoutInitialValue<number>().used();
  const coro: any = (async () => {
    let vals = [];
    try {
      for await (let val of signal) {
        vals.push(val);
      }
    } catch (e) {
      return {e, vals};
    }
  })();
  signal.set(0);
  signal.set(1);
  signal.set(2);
  await yieldMe();
  signal.set(3);
  signal.reject(new Error('bla bla'));
  const ret = await coro;
  testCheck(ret.e.message === 'bla bla');
  testCheck(JSON.stringify(ret.vals) == JSON.stringify([0, 2, 3]));
  testCheck(signal.linksOut.size === 0);
}

export async function testSignals() {
  testSignalBind();
  testSignalMap();
  testSignalCombine();
  await testSignalAsyncIterate();
}

