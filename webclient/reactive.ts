import { assert, yieldMe } from './base.js';

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
    source: SignalRO<SourceU>,
    target: SomeSignal | null,
    update: (isException: boolean, valueOrException: SourceU | Error, link: SomeSignalLink | undefined) => void
  ): SignalLink<SourceU> | null {
    if (target !== null && target.hasException) {
      return null;
    }
    if (source.hasException) {
      update(true, source.valueOrException as SourceU, undefined);
      return null;
    }
    let link = new SignalLink<SourceU>(source, target, update);
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
export interface SignalRO<out T> {
  get cur(): T;
  get maybeCur(): T | undefined;
  [Symbol.asyncIterator](): AsyncIterator<T, any>;
  then<TResult1, TResult2 = never>(onfulfilled?: ((value: T) => TResult1 | PromiseLike<TResult1>) | undefined | null, onrejected?: ((reason: any) => TResult2 | PromiseLike<TResult2>) | undefined | null): Promise<TResult1 | TResult2>;
  map<U>(f: (_: T) => U): SignalRO<U>;
  bind<U>(f: (_: T) => SignalRO<U> | U): SignalRO<U>;
  hasValueSig(): Signal<boolean>;
  addLinkOut(link: SignalLinkOut<T>): void;
  removeLinkOut(link: SignalLinkOut<T>): void;
  readonly hasValue: boolean;
  readonly hasException: boolean;
  readonly valueOrException: T | Error | undefined;

}

type SignalVal<T> = T extends Signal<infer U> ? U : never;

class _NoInitialValueDontUse {}
const NO_INITIAL_VALUE_DONT_USE = new _NoInitialValueDontUse

export class Signal<T> implements SignalRO<T> {
  linksIn: Set<Kill>;
  linksOut: Set<SignalLinkOut<T>>;
  hasValue: boolean;
  hasException: boolean;
  valueOrException: T | Error | undefined;
  constructor(initialValue: T | _NoInitialValueDontUse = NO_INITIAL_VALUE_DONT_USE) {
    this.linksIn = new Set;
    this.linksOut = new Set;
    this.hasException = false;
    if (initialValue === NO_INITIAL_VALUE_DONT_USE) {
      this.hasValue = false;
      this.valueOrException = undefined;
    } else {
      this.hasValue = true;
      this.valueOrException = initialValue as T;
    }
  }
  static of<U>(valueOrSig: SignalRO<U> | U): SignalRO<U> {
    if (valueOrSig instanceof Signal) {
      return valueOrSig;
    } else {
      return new Signal(valueOrSig as U);
    }
  }
  static withoutInitialValue<U>(): Signal<U> {
    return new Signal<U>();
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

  map<U>(f: (_: T) => U): Signal<U> {
    let mappedSignal = new Signal<U>;
    SignalLink.create(this, mappedSignal, (hasException, valueOrException) => {
      if (hasException) {
        mappedSignal.reject(valueOrException as Error);
      } else {
        mappedSignal.set(f(valueOrException as T));
      }
    });
    return mappedSignal;
  }

  bind<U>(f: (_: T) => SignalRO<U> | U): Signal<U> {
    let boundSignal = new Signal<U>;
    let forwardingLink: SomeSignalLink | null = null;
    SignalLink.create(this, boundSignal, (hasException, valueOrException) => {
      if (forwardingLink !== null) {
        forwardingLink.kill();
        forwardingLink = null;
      }
      if (hasException) {
        boundSignal.reject(valueOrException as Error);
      } else {
        let forwarderSignal = Signal.of(f(valueOrException as T));
        forwardingLink = SignalLink.create(forwarderSignal, boundSignal, (hasException2, valueOrException2) => {
          if (hasException2) {
            boundSignal.reject(valueOrException2 as Error);
          } else {
            boundSignal.set(valueOrException2 as U);
          }
        });
      }
    });
    return boundSignal;
  }

  static multimap<Obj>(obj: Obj):
    Signal<{ [Key in keyof Obj]: Obj[Key] extends Signal<infer T> ? T : Obj[Key] }>
  {
    const retSignal = new Signal();
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
    return retSignal as any;
  }

  hasValueSig(): Signal<boolean> {
    let ret = this.map((_) => true);
    if (!this.hasValue && !this.hasException) {
      ret.set(false);
    }
    return ret;
  }

  kill(): void {
    this.reject(new Error('killed'));
  }
  addLinkOut(link: SignalLinkOut<T>): void {
    this.linksOut.add(link);
  }
  removeLinkOut(link: SignalLinkOut<T>): void {
    this.linksOut.delete(link);
  }
  addLinkIn(link: Kill): void {
    this.linksIn.add(link);
  }
  removeLinkIn(link: Kill): void {
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
export class Lifecycle {
  killCallbacks: (() => void)[] = [];
  dead: boolean = false;
  pushKill<T extends HasLifecycle | Reject>(objToKill: T): T {
    const lifecycle = (objToKill as HasLifecycle).lifecycle;
    const isLifecycle = lifecycle !== undefined;
    const isReject = (objToKill as Reject).reject !== undefined;
    if (isLifecycle && isReject) {
        throw new Error("can't be both lifecycle and reject");
    } else if (isLifecycle) {
      this.killCallbacks.push(lifecycle.kill.bind(lifecycle));
    } else if (isReject) {
      this.killCallbacks.push(() => {
        (objToKill as Reject).reject(`rejected by KillBox of ${this.constructor.name}`);
      });
    } else {
      throw new Error('not sure how to clean this up');
    }
    return objToKill;
  }
  pushKillEventListener(target: EventTarget, event: string, callback: EventListenerOrEventListenerObject, options?: AddEventListenerOptions | boolean): void {
    target.addEventListener(event, callback, options);
    this.killCallbacks.push(() => {
      target.removeEventListener(event, callback, options);
    });
  };
  kill() {
    if (this.dead) {
      return;
    }
    this.dead = true;
    for (let cb of this.killCallbacks) {
      cb();
    }
    this.killCallbacks = null as any;
  };
  pushCatchKill(promise: Promise<any>): void {
    promise.then(null, (exception) => {
      if (this.dead && typeof exception === 'object' && exception.message === 'killed') {
        // ignore
        console.log('caught killed exception');
        return;
      }
      throw exception; // devtools will catch if open
    });

  }
}
interface HasLifecycle {
  readonly lifecycle: Lifecycle;
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
  const sigList: any[] = [new Signal(10), new Signal, 1000];
  testCheck(sigList[1].hasValue === false);
  const indexSig = new Signal(2);
  const indexedSig = indexSig.bind(i => sigList[i]);
  testCheck(indexedSig.cur === 1000);
  indexSig.set(1);
  testCheck(indexedSig.cur === 1000);
  sigList[1].set(123);
  testCheck(indexedSig.cur === 123);
  // We don't reevaluate the function 'just because', so we have a stale value:
  sigList[1] = new Signal(124);
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
  // Make sure kill cleans up after itself:
  indexedSig.kill();
  testCheck(sigList[0].linksOut.size === 0);
}

function testSignalMap(): void {
  const signal = new Signal<number>;
  const mappedSig = signal.map(x => 2 * x);
  testCheck(!mappedSig.hasValue);
  signal.set(10);
  testCheck(mappedSig.cur == 20);
  // Make sure kill cleans up after itself:
  mappedSig.kill();
  testCheck(signal.linksOut.size === 0);
}

function testSignalMultimap(): void {
  const signalA = new Signal<string>;
  const signalB = new Signal<string>;
  const mappedSig = Signal.multimap({a: signalA, b: signalB, c: 123});
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
  const signal = new Signal<number>;
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
  testSignalMultimap();
  await testSignalAsyncIterate();
  /*
  let sig = new Signal<number>;
  let moresigs: Signal<number>[] = [new Signal, new Signal];
  let sig3 = sig.bind(i => moresigs[i]).map(x => x * 2);
  (async () => {
    for await (let val of sig3) {
      console.log(val);
    }
  })();
  sig.set(0);
  moresigs[0].set(42);
  moresigs[0].set(43);
  sig.set(1);
  moresigs[0].set(NaN);
  moresigs[1].set(44);
  console.log('*', sig3.cur);
  */
}

