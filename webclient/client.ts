import * as mmTypes from 'build/mm_types';
import { assert, Type, u64, u8 } from 'types_base';

var theRPCConn: RPCConn

function hex(n: number): string {
  return '0x'+n.toString(16);
}
/*
function notNull<T>(val: T | null): T {
  if (val === null) {
    throw new Error('unexpectedly null');
  }
  return val;
}
function notUndefined<T>(val: T | undefined): T {
  if (val === undefined) {
    throw new Error('unexpectedly undefined');
  }
  return val;
}
*/
function downcast(ty: any, val: unknown): InstanceType<typeof ty> {
  if (!(val instanceof ty)) {
    throw new Error('unexpectedly undefined');
  }
  return val;
}
function yieldMe(): Promise<void> {
  let { promise, resolve, reject } = Promise.withResolvers<void>();
  queueMicrotask(resolve);
  return promise;
}
function sleep(n: number): Promise<void> {
  let { promise, resolve, reject } = Promise.withResolvers<void>();
  setTimeout(resolve, n);
  return promise;
}

async function doRead(addr: bigint, len: number | bigint): Promise<DataView> {
  let resp = await theRPCConn.reqRead(addr, len);
  if (resp.byteLength < len) {
    throw new Error(`short read addr=${addr} len=${len} actual=${resp.byteLength}`);
  }
  return resp;
}
async function doWrite(addr: bigint, data: ArrayBufferView): Promise<void> {
  let [actual, len] = await theRPCConn.reqWrite(addr, data);
  if (actual < len) {
    throw new Error(`short write addr=${addr} len=${len} actual=${actual}`);
  }
}


const utf8Decoder = new TextDecoder();

declare module 'build/mm_types' {
  interface Rect {
    get width(): number;
    get height(): number;
  }
}
Object.defineProperty(mmTypes.Rect, 'width', {
  get(): number { return this.max.x - this.min.x; }
});
Object.defineProperty(mmTypes.Rect, 'height', {
  get(): number { return this.max.y - this.min.y; }
});

class Reader {
  dv: DataView
  off: bigint
  size: bigint
  constructor(dv: DataView | Uint8Array | ArrayBuffer, off: bigint = 0n) {
    if (dv instanceof ArrayBuffer) {
      dv = new DataView(dv);
    }
    if (dv instanceof Uint8Array) {
      dv = new DataView(dv.buffer, dv.byteOffset, dv.byteLength);
    }
    assert(dv instanceof DataView);
    this.dv = dv;
    this.off = off;
    this.size = BigInt(this.dv.byteLength);
  }
  readOff(size: bigint): bigint {
    let off = this.off;
    if (size > this.size - off) {
      throw new Error(`Reader overread - wanted ${size} only had ${this.size - off}/${this.size}`);
    }
    this.off = off + size;
    return off;
  }
  read<T>(ty: Type<T>, size: bigint | undefined = undefined) {
    if (size === undefined) {
      size = ty.sizeof;
    }
    assert(typeof size == 'number');
    let off = this.readOff(size);
    return ty.get(this.dv, off);
  }
  readArrayUnbounded<T>(callback: () => T): T[] {
    let out = [];
    while (this.off < this.size) {
      out.push(callback());
    }
    return out;
  }
  readArray<T>(count: bigint, callback: () => T): T[] {
    let out = [];
    for (let i = 0n; i < count; i++) {
      out.push(callback());
    }
    return out;
  }
  expectEnd(): void {
    if (this.size != this.off) {
      throw new Error(`Reader underread by ${this.size - this.off}`);
    }
  }
  static with<T>(dv: DataView | Uint8Array | ArrayBuffer, off: bigint = 0n,
                 callback: (_: Reader) => T): T {
    let self = new Reader(dv, off);
    let ret = callback(self);
    self.expectEnd();
    return ret;
  }
  remaining(): bigint {
    return this.size - this.off;
  }
}

// These could be structs but using Reader avoids a circular dependency
function readExlUtilRange(r: Reader) {
  return {start: r.read(u64), size: r.read(u64)}
}
function readHelloModInfo(r: Reader) {
  let ret = r.readArrayUnbounded(() => ({
    total: readExlUtilRange(r),
    text: readExlUtilRange(r),
    rodata: readExlUtilRange(r),
    data: readExlUtilRange(r),
    buildId: r.readArray(16n, () => r.read(u8)),
    rawData: r.dv.buffer,
  }));
  return ret;
}
type HelloModInfo = ReturnType<typeof readHelloModInfo>;

function toHex(array: bigint[]): String {
  return array.map(num => num.toString(16).padStart(2, '0')).join('');
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
  hasValue: boolean;
  hasException: boolean;
}
interface SignalRO<out T> {
  get cur(): T;
  get maybeCur(): T | undefined;
  [Symbol.asyncIterator]<U extends T>(): SignalAsyncIterator<U, any>; // XXX
  then<TResult1, TResult2 = never>(onfulfilled?: ((value: T) => TResult1 | PromiseLike<TResult1>) | undefined | null, onrejected?: ((reason: any) => TResult2 | PromiseLike<TResult2>) | undefined | null): Promise<TResult1 | TResult2>;
  map<U>(f: (_: T) => U): SignalRO<U>;
  bind<U>(f: (_: T) => SignalRO<U>): SignalRO<U>;
  hasValueSig(): Signal<boolean>;
  addLinkOut(link: SignalLinkOut<T>): void;
  removeLinkOut(link: SignalLinkOut<T>): void;
  addLinkIn(link: Kill): void;
  removeLinkIn(link: Kill): void;
  readonly hasValue: boolean;
  readonly hasException: boolean;
  readonly valueOrException: T | Error | undefined;

}
class Signal<T> implements SignalRO<T> {
  linksIn: Set<Kill>;
  linksOut: Set<SignalLinkOut<T>>;
  hasValue: boolean;
  hasException: boolean;
  valueOrException: T | Error | undefined;
  constructor(initialValue: T | undefined = undefined, hasInitialValue: boolean = true) {
    this.linksIn = new Set;
    this.linksOut = new Set;
    this.hasValue = hasInitialValue;
    this.hasException = false;
    this.valueOrException = initialValue;
  }
  static of<U>(valueOrSig: SignalRO<U> | U): SignalRO<U> {
    if (valueOrSig instanceof Signal) {
      return valueOrSig;
    } else {
      return new Signal(valueOrSig as U);
    }
  }
  static withoutInitialValue<U>(): Signal<U> {
    return new Signal<U>(undefined, false);
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

  [Symbol.asyncIterator]<U extends T>(): SignalAsyncIterator<U, any> { // XXX
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

  bind<U>(f: (_: T) => Signal<U>): Signal<U> {
    let boundSignal = new Signal<U>;
    let forwardingLink: SomeSignalLink | null = null;
    SignalLink.create(this, boundSignal, (hasException, valueOrException) => {
      if (forwardingLink !== null) {
        forwardingLink.kill();
      }
      if (hasException) {
        boundSignal.reject(valueOrException as Error);
      } else {
        let forwarderSignal = Signal.of(f(valueOrException as T));
        forwardingLink = SignalLink.create(forwarderSignal, boundSignal, (hasException, valueOrException) => {
          if (hasException) {
            boundSignal.reject(valueOrException as Error);
          } else {
            boundSignal.set(valueOrException as U);
          }
        });
      }
    });
    return boundSignal;
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

class SignalAsyncIterator<T, TReturn> implements AsyncIterator<T, TReturn> {
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
    console.log('>> return', value);
    return {value: await value, done: true};
  }
  throw(e?: any): Promise<IteratorResult<T, TReturn>> {
    if (this.link !== null) {
      this.link.kill();
    }
    return Promise.reject(e);
  }
}

function testSignals() {
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
}


interface Kill {
  kill: () => void;
}
interface Reject {
  reject: (reason?: any) => void;
}
class Lifecycle {
  killCallbacks: (() => void)[] = [];
  dead: boolean = false;
  pushKill<T extends HasLifecycle | Reject /* | Kill*/>(objToKill: T): T {
    const lifecycle = (objToKill as HasLifecycle).lifecycle;
    let ok = false;
    if (lifecycle !== undefined) {
      this.killCallbacks.push(lifecycle.kill.bind(lifecycle));
      ok = true;
    }
    if ((objToKill as Reject).reject !== undefined) {
      this.killCallbacks.push(() => {
        (objToKill as Reject).reject(`rejected by KillBox of ${this.constructor.name}`);
      });
      ok = true;
    }
    /*
    if ((objToKill as Kill).kill !== undefined) {
      this.killCallbacks.push(() => {
        (objToKill as Kill).kill();
      });
      ok = true;
    }
    */
    if (!ok) {
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

class Throttle {
  delay: number;
  constructor(public name: string, public defaultDelay: number = 100) {
    this.delay = this.defaultDelay;
  }
  reset(): void {
    this.delay = this.defaultDelay;
  }
  then<TResult1, TResult2 = never>(onfulfilled?: ((value: void) => TResult1 | PromiseLike<TResult1>) | undefined | null, onrejected?: ((reason: any) => TResult2 | PromiseLike<TResult2>) | undefined | null): Promise<TResult1 | TResult2> {
    let delay = this.delay;
    this.delay *= 2;
    addStatsLine(`Throttle "${this.name}" waiting for ${delay} ms`);
    return sleep(delay).then(onfulfilled, onrejected);
  }
}

interface WSWrapperDelegate {
  onOpen(e: Event): void;
  onMessage(e: MessageEvent<any>): void;
  onClose(e: Event): void;
}
class WSWrapper {
  openThrottle: Throttle = new Throttle('open');
  state: string = 'idle';
  ws: WebSocket | null = null;
  wantOpen: boolean = false;
  constructor(public wsURL: string, public delegate: WSWrapperDelegate) {
  }
  setWantOpen(wantOpen: boolean): void {
    this.wantOpen = wantOpen;
    this.openCloseWSAsNeeded();
  }
  kill(): void {
    if (this.state == 'open') {
      addStatsLine(`closing ${this.wsURL} due to kill`);
      assert(this.ws !== null);
      this.ws.close();
    }
    this.state = 'dead';
    this.wantOpen = false;
    this.ws = null;
  }
  openCloseWSAsNeeded(): void {
    if (this.state == 'idle' && this.wantOpen) {
      this.openWS();
    } else if (this.state == 'open' && !this.wantOpen) {
      addStatsLine(`closing ${this.wsURL}`);
      assert(this.ws !== null);
      this.ws.close();
    }
  }
  openWS(): void {
    assert(this.ws === null);
    assert(this.state == 'idle');
    addStatsLine(`connecting to ${this.wsURL}`);
    let ws = new WebSocket(this.wsURL);
    this.ws = ws;
    this.state = 'connecting';
    ws.binaryType = 'arraybuffer';
    ws.onopen = (e) => {
      assert(this.ws === ws);
      this.state = 'open';
      addStatsLine(`opened connection to ${this.wsURL}`);
      this.delegate.onOpen(e);
      // if it doesn't close quickly, then reset throttle
      setTimeout(() => {
        if (this.ws === ws) {
          this.openThrottle.reset();
        }
      }, 1000);
    };
    ws.onmessage = (e) => {
      assert(this.ws === ws);
      this.delegate.onMessage(e);
    };
    ws.onclose = (e) => {
      assert(this.ws === ws || this.state === 'dead');
      addStatsLine(`closed connection to ${this.wsURL}`);
      this.ws = null;
      if (this.wantOpen) {
        this.state = 'openThrottle';
        (async () => {
          await this.openThrottle;
          this.state = 'idle';
          this.openCloseWSAsNeeded();
        })();
      } else {
        this.state = 'idle';
      }
      this.delegate.onClose(e);
    };
  }
}

const rpcConnClosedMessage = 'RPCConn closed';

type GetStatsResp = {overrunBytes: bigint, sentBytes: bigint, backpressuredNsec: bigint};
class RPCConn implements WSWrapperDelegate {
  readonly lifecycle = new Lifecycle();
  readonly rpcThrottle = new Throttle('rpc');
  helloInfoPR: PromiseWithResolvers<HelloModInfo>;
  isOpenSig: Signal<boolean>;
  wsw: WSWrapper;
  pendingResponsePRs: PromiseWithResolvers<ArrayBuffer | string>[] = [];
  constructor(baseURL: string) {
    this.wsw = this.lifecycle.pushKill(new WSWrapper(`${baseURL}/ws/rpc`, this));
    this.wsw.setWantOpen(true);
    this.helloInfoPR = this.lifecycle.pushKill(Promise.withResolvers());
    this.isOpenSig = this.lifecycle.pushKill(new Signal(false));
  }
  onOpen() {
    this.pendingResponsePRs = [this.helloPendingResponsePR()];
    this.isOpenSig.set(true);
  }
  helloPendingResponsePR(): PromiseWithResolvers<ArrayBuffer | string> {
    let pr = Promise.withResolvers<ArrayBuffer | string>();
    pr.promise.then((data) => {
      if (typeof data == 'string') {
        this.helloInfoPR.reject(`early error! ${data}`);
        return;
      }
      assert(data instanceof ArrayBuffer);
      this.helloInfoPR.resolve(readHelloModInfo(new Reader(data)));
    }, (reason) => {
      if (reason !== rpcConnClosedMessage) {
        this.helloInfoPR.reject(reason);
      }
    });
    return pr;
  }
  onMessage(e: MessageEvent<any>) {
    let pr = this.pendingResponsePRs.shift();
    if (!pr) {
      throw new Error('got unsolicited packet');
    }
    pr.resolve(e.data);
  }
  onClose() {
    this.isOpenSig.set(false);
    let pr;
    while ((pr = this.pendingResponsePRs.shift()) !== undefined) {
      pr.reject(rpcConnClosedMessage);
    }
  }
  async rawRequest(req: any): Promise<DataView> {
    outerLoop: while (1) {
      while (!this.isOpenSig.cur) {
        addStatsLine('RPC waiting for open');
        await this.isOpenSig;
      }

      //console.log(`sending ${req.byteLength} ${req}`);
      assert(this.wsw.ws !== null);
      this.wsw.ws.send(req);
      let pr = Promise.withResolvers<ArrayBuffer | string>();
      this.pendingResponsePRs.push(pr);
      let response;
      try {
        response = await pr.promise;
      } catch (e) {
        if (e !== rpcConnClosedMessage) {
          throw e;
        }
        addStatsLine('RPC waiting for re-open');
        continue outerLoop;
      }

      if (typeof response == 'string') {
        addStatsLine(`RPC got error: '${response}'`);
        await this.rpcThrottle;
        continue outerLoop;
      }

      assert(response instanceof ArrayBuffer);
      this.rpcThrottle.reset();
      //console.log(`RPC got good response: ${response}`);

      return new DataView(response);
    }
    throw new Error('impossible to get here but tsc is dumb');
  }
  async reqRead(addr: bigint, len: number | bigint): Promise<DataView> {
    let dv = new DataView(new ArrayBuffer(17));
    dv.setInt8(0, 1); // RPC_REQ_READ
    dv.setBigUint64(1, addr, true);
    dv.setBigUint64(9, BigInt(len), true);
    return await this.rawRequest(dv);
  }
  async reqWrite(addr: bigint, data: ArrayBufferView): Promise<[bigint, bigint]> {
    assert(ArrayBuffer.isView(data));
    let headerLen = 9;
    let len = data.byteLength;
    let ab = new ArrayBuffer(headerLen + len);
    new Uint8Array(ab, headerLen).set(new Uint8Array(data.buffer, data.byteOffset, len));
    let dv = new DataView(ab);
    dv.setInt8(0, 2); // RPC_REQ_WRITE
    dv.setBigUint64(1, addr, true);
    let resp = await this.rawRequest(dv);
    assert(resp.byteLength == 8);
    let actual = resp.getBigUint64(0, true);
    assert(actual <= len);
    return [actual, BigInt(len)];
  }
  async reqGetStats(): Promise<GetStatsResp> {
    let u8 = Uint8Array.of(3); // RPC_REQ_GET_STATS
    let resp = await this.rawRequest(u8);
    return Reader.with(resp, 0n, (r) => ({
      overrunBytes: r.read(u64),
      sentBytes: r.read(u64),
      backpressuredNsec: r.read(u64),
    }));

  }
  async reqSetFlags(clear: bigint, set: bigint): Promise<bigint> {
    let dv = new DataView(new ArrayBuffer(17));
    dv.setInt8(0, 4); // RPC_REQ_SET_FLAGS
    dv.setBigUint64(1, clear, true);
    dv.setBigUint64(9, set, true);
    let resp = await this.rawRequest(dv);
    assert(resp.byteLength == 8);
    return resp.getBigUint64(0, true);
  }
}


// --

// --

const svgNS = 'http://www.w3.org/2000/svg';

interface SVGElementWithDTCache extends SVGElement {
  _fill?: string;
  _stroke?: string;
  _strokeWidth?: number;
}
abstract class DiagramThingKind {
  group: SVGGElement;
  elmsByPtr: Map<bigint, SVGElement> = new Map();
  constructor(public diagram: Diagram) {
    this.group = document.createElementNS(svgNS, 'g') as SVGGElement;
    this.group.setAttribute('kind', this.constructor.name);
    diagram.svg.appendChild(this.group);
  }
  renderThings() {
    this.genericElmsCount = 0;
    let seen = new Set();
    this.seen = seen;
    for (let thing of this.getThingsFromFrame()) {
      let worldID = this.getWorldID(thing);
      assert(typeof worldID == 'number');
      if (worldID !== this.diagram.worldID) {
        continue;
      }
      this.renderThing(thing);
    }
    for (let elm of Array.from(this.group.children)) {
      if (!this.seen.has(elm)) {
        //console.log(`${this.diagram.curFrame.number}/${this.diagram.curFrame.worldID} removing stale ${elm} at ${elm.ptr} from ${elm.createdFrameNum}`);
        elm.deleteMe();
      }
    }
    this.seen = null;
    //console.log(`now have ${this.group.childElementCount} children`);
  }
  getOrCreateElmFor(ptr, wantedTag) {
    let elm = this.elmsByPtr.get(ptr);
    //console.log(elm);
    if (elm && elm.tagName != wantedTag) {
      elm.deleteMe();
      elm = null;
    }
    if (!elm) {
      elm = document.createElementNS(svgNS, wantedTag);
      assert(elm.tagName == wantedTag);
      //console.log(`creating ${wantedTag}`);
      elm.ptr = ptr;
      elm.setAttribute('ptr', hex(ptr)); // ** remove this if perf is an issue
      elm.createdFrameNum = this.diagram.curFrame.number;
      elm.deleteMe = () => {
        //console.log(`** deleteMe for ${wantedTag} ptr=${ptr}`);
        elm.parentNode.removeChild(elm);
        this.elmsByPtr.delete(ptr);
      };
      this.group.appendChild(elm);
      this.elmsByPtr.set(ptr, elm);
    }
    this.seen.add(elm);
    return elm;
  }

  poly(key: any, points: number[],
       {fill = 'none', stroke, strokeWidth = 0}:
       {fill?: string, stroke?: string, strokeWidth?: number}) {
    let dia = this.diagram;
    let stringified = '';
    for (let i = 0, len = points.length; i < len; i += 2) {
      let x = dia.transformX(points[i]);
      let y = dia.transformY(points[i+1]);
      stringified += ` ${x},${y}`;
    }
    let polygon = this.getOrCreateElmFor(key, 'polygon') as SVGElementWithDTCache;
    if (polygon._fill === fill &&
        polygon._stroke === stroke &&
        polygon._strokeWidth === strokeWidth &&
        polygon.getAttribute('points') === stringified) {
      return;
    }
    polygon.setAttribute('points', stringified);
    polygon.setAttribute('fill', fill);
    if (stroke !== undefined) {
      polygon.setAttribute('stroke', stroke);
      polygon.setAttribute('stroke-width', ''+strokeWidth);
      polygon.setAttribute('stroke-linejoin', 'bevel');
    }
    polygon._fill = fill;
    polygon._stroke = stroke;
    polygon._strokeWidth = strokeWidth;
  }

  line(key, x1, y1, x2, y2, {stroke, strokeWidth}) {
    let dia = this.diagram;
    let line = this.getOrCreateElmFor(key, 'line');
    let tx1 = dia.transformX(x1);
    let tx2 = dia.transformX(x2);
    let ty1 = dia.transformY(y1);
    let ty2 = dia.transformY(y2);
    if (line.tx1 === tx1 &&
        line.tx2 === tx2 &&
        line.ty1 === ty1 &&
        line.ty2 === ty2 &&
        line.stroke === stroke &&
        line.strokeWidth === strokeWidth) {
      return;
    }
    line.setAttribute('x1', tx1);
    line.setAttribute('y1', ty1);
    line.setAttribute('x2', tx2);
    line.setAttribute('y2', ty2);
    line.setAttribute('stroke', stroke);
    line.setAttribute('stroke-width', strokeWidth);

    line.tx1 = tx1;
    line.tx2 = tx2;
    line.ty1 = ty1;
    line.ty2 = ty2;
    line.stroke = stroke;
    line.strokeWidth = strokeWidth;
  }

  circle(key, cx, cy, r, {fill, stroke, strokeWidth}) {
    let dia = this.diagram;
    let circle = this.getOrCreateElmFor(key, 'circle');
    circle.setAttribute('cx', dia.transformX(cx));
    circle.setAttribute('cy', dia.transformY(cy));
    circle.setAttribute('r', dia.transformSize(r));
    if (fill !== undefined) {
      circle.setAttribute('fill', fill);
    }
    if (stroke !== undefined) {
      circle.setAttribute('stroke', stroke);
      circle.setAttribute('stroke-width', strokeWidth ?? 1);
    }

  }
  rect(key, x, y, width, height, {fill, stroke, strokeWidth}) {
    let dia = this.diagram;
    let rect = this.getOrCreateElmFor(key, 'rect');
    rect.setAttribute('x', dia.transformX(x));
    rect.setAttribute('y', dia.transformY(y));
    rect.setAttribute('width', dia.transformSize(width));
    rect.setAttribute('height', dia.transformSize(height));
    if (fill !== undefined) {
      rect.setAttribute('fill', fill);
    }
    if (stroke !== undefined) {
      rect.setAttribute('stroke', stroke);
      rect.setAttribute('stroke-width', strokeWidth ?? 1);
    }
  }
}

class DiagramHitboxes extends DiagramThingKind {
  getThingsFromFrame() {
    return this.diagram.curFrame.hitboxes.entries();
  }
  getWorldID([ptr, hitbox]) {
    return hitbox.worldID;
  }
  renderThing([ptr, hitbox]) {
    let params = hitbox.params;
    let isCircle = params.isCircle;
    let x = hitbox.rect.min.x;
    let y = hitbox.rect.min.y;
    let w = hitbox.rect.width();
    let h = hitbox.rect.height();

    let dia = this.diagram;
    if (w < 0) {
      // todo: DRY
      return;
    }
    if (isCircle) {
      assert(w == h);
      this.circle(ptr, x + w/2, y + h/2, w/2, {
        stroke: 'green',
        strokeWidth: 4,
        fill: 'yellow',
      });
    } else {
      this.rect(ptr, x, y + h, w, h, {
        stroke: 'green',
        strokeWidth: 4,
        fill: 'yellow',
      });
    }
  }
}

class DiagramNormalColliders extends DiagramThingKind {
  getThingsFromFrame() {
    return this.diagram.curFrame.normalColliders;
  }
  getWorldID(col) {
    return col.worldID;
  }
  renderThing(col) {
    if (col.segmentsCur.length == 0) {
      return;
    }
    let extX = col.extPosCur.x;
    let extY = col.extPosCur.y;
    let dia = this.diagram;
    let points = [];
    let prevSeg = col.segmentsCur[col.segmentsCur.length - 1], seg;
    for (let i = 0, len = col.segmentsCur.length; i < len; i++, prevSeg = seg) {
      seg = col.segmentsCur[i];
      let x1 = seg.rel_pos_1.x;
      let y1 = seg.rel_pos_1.y;
      let x2 = seg.rel_pos_2.x;
      let y2 = seg.rel_pos_2.y;
      if (x1 != prevSeg.rel_pos_2.x ||
          y1 != prevSeg.rel_pos_2.y) {
        return this.renderAsLines(col);
      }
      points.push(x1 + extX, y1 + extY, x2 + extX, y2 + extY);
    }
    this.poly(
      col.ptr,
      points,
      this.myStroke
    );
  }
  renderAsLines(col) {
    let extX = col.extPosCur.x;
    let extY = col.extPosCur.y;
    let dia = this.diagram;
    for (let i = 0; i < col.segmentsCur.length; i++) {
      let seg = col.segmentsCur[i];
      let fakePtr = col.ptr + BigInt(i);
      this.line(
        col.ptr + BigInt(i),
        seg.rel_pos_1.x + extX,
        seg.rel_pos_1.y + extY,
        seg.rel_pos_2.x + extX,
        seg.rel_pos_2.y + extY,
        this.myStroke
      );
    }
  }
  myStroke = {
    stroke: 'red',
    strokeWidth: 2,
  };
}

class DiagramScolColliders extends DiagramThingKind {
  getThingsFromFrame() {
    return this.diagram.curFrame.scolColliders;
  }
  getWorldID(data) {
    return data.worldID;
  }
  renderThing(data) {
    let dia = this.diagram;
    let scol = data.scol;
    if (1) {
      for (let i = 0; i < 4; i++) {
        let fakePtr = data.ptr + BigInt(i);
        let xxyPtr = scol.xxy_ptrs_cur.get(i);
        if (xxyPtr == 0n) {
          // TODO (2025-09-28): not sure what null here means, might be I
          // forgot something, but I'm getting it
          continue;
        }
        let xxy = scol.readPtrFromWithin(xxyPtr , this.mmTypes.XXY);

        this.line(
          fakePtr,

          xxy.x_left + data.extPosCur.x,
          xxy.y + data.extPosCur.y,
          xxy.x_right + data.extPosCur.x,
          xxy.y + data.extPosCur.y,

          {
            stroke: ['pink', 'green', 'blue', 'orange'][i],
            strokeWidth: 4,
          }
        );
      }
    }
    for (let i = 0; i < 4; i++) {
      let resultListIdx = scol.result_list_idxs_cur.get(i);
      let resultList = scol.result_lists_cur.get(i);
      let result = resultList.get(resultListIdx);
      if (result.valid) {
        //console.log(result.angle);

      }

    }
  }
}

class DiagramBGCollisionPoints extends DiagramThingKind {
  getThingsFromFrame() {
    return this.diagram.curFrame.bgCollisionPointEvents;
  }
  getWorldID(data) {
    return data.worldID;
  }
  renderThing(data) {
    this.circle(
      this.genericElmsCount++,
      data.xy.x,
      data.xy.y,
      3,
      {
        fill: data.ret ? 'red' : 'rgba(0, 0, 0, 0.25)',
      }
    );
  }
}

class DiagramBGCollisionTwoPoints extends DiagramThingKind {
  getThingsFromFrame() {
    return this.diagram.curFrame.bgCollisionTwoPointEvents;
  }
  getWorldID(data) {
    return data.worldID;
  }
  renderThing(data) {
    let hit = data.ret;
    this.line(
      this.genericElmsCount++,
      data.xy1.x,
      data.xy1.y,
      data.xy2.x,
      data.xy2.y,
      {
        stroke: hit ? 'black' : 'rgba(0, 0, 0, 0.25)',
        strokeWidth: 1,
      }
    );
  }
}
function timelineTime(): number {
  const ret = document.timeline.currentTime;
  assert(typeof ret === 'number');
  return ret;
}
class Diagram {
  readonly lifecycle = new Lifecycle();
  lastRenderedFrame: HoseFrame | null = null;
  zoomLevel = 1;
  panX = 0;
  panY = 400;
  lastWheelTimeStamp = 0;

  zoomMomentum = 0;
  zoomMomentumTimeoutID: number | null = null;

  zoomMomentumStartZoomLevel: number | null = null;
  zoomMomentumStartPanX: number | null = null;
  zoomMomentumStartPanY: number | null = null;
  zoomMomentumStartTime: number | null = null;
  zoomMomentumCursorRealX: number | null = null;
  zoomMomentumCursorRealY: number | null = null;
  zoomMomentumRAFID: number | null = null;
  mouseDownClientX: number | null = null;
  mouseDownClientY: number | null = null;

  readonly kinds: DiagramThingKind[];
  readonly hitboxes = new DiagramHitboxes(this);
  readonly normalColliders = new DiagramNormalColliders(this);
  readonly scolColliders = new DiagramScolColliders(this);
  readonly bgCollisionPoints = new DiagramBGCollisionPoints(this);
  readonly bgCollisionTwoPoints = new DiagramBGCollisionTwoPoints(this);

  worldID = 0;

  readonly onMouseMoveBound = this.onMouseMove.bind(this);
  readonly onMouseUpBound = this.onMouseUp.bind(this);

  curFrame: HoseFrame | null = null;



  constructor(public svg: SVGSVGElement) {
    this.kinds = [
      this.hitboxes,
      this.normalColliders,
      this.scolColliders,
      this.bgCollisionPoints,
      this.bgCollisionTwoPoints,
    ];

    this.lifecycle.pushKillEventListener(this.svg, 'wheel', this.onWheel.bind(this) as EventListener,
                                         {passive: false});
    this.lifecycle.pushKillEventListener(this.svg, 'mousedown', this.onMouseDown.bind(this) as EventListener);
    this.lifecycle.killCallbacks.push(() => {
      this.svg.innerHTML = '';
      this.lastRenderedFrame = null;
      this.removeMouseMoveUp();
    });
  }
  renderFrame(frame: HoseFrame): void {
    this.curFrame = frame;
    for (let kind of this.kinds) {
      kind.renderThings();
    }
    this.lastRenderedFrame = frame;
    this.curFrame = null;
  }
  transformX(x: number): number {
    return this.panX + this.transformSize(x);
  }
  transformY(y: number): number {
    return this.panY - this.transformSize(y);
  }
  transformSize(size: number): number {
    return size * this.zoomLevel; // todo: match camera
  }
  untransformX(x: number): number {
    return this.untransformSize(x - this.panX);
  }
  untransformY(y: number): number {
    return this.untransformSize(this.panY - y);
  }
  untransformSize(size: number): number {
    return size / this.zoomLevel;
  }
  guessIsTrackpad(e: WheelEvent): boolean {
    // https://stackoverflow.com/questions/10744645/detect-touchpad-vs-mouse-in-javascript
    // hack...
    return e.deltaMode == 0;
  }
  zoomByAmountKeepingPointConstant(zoomAmount: number, cursorRealX: number, cursorRealY: number): void {
    let cursorVirtualX = this.untransformX(cursorRealX), cursorVirtualY = this.untransformY(cursorRealY);
    this.zoomLevel *= zoomAmount;

    // stay centered on the cursor
    this.panX += cursorRealX - this.transformX(cursorVirtualX);
    this.panY += cursorRealY - this.transformY(cursorVirtualY);
  }
  onWheel(e: WheelEvent): void {
    if (this.lastRenderedFrame === null) {
      return;
    }
    e.preventDefault();
    this.disableZoomMomentum();
    let trackpad = this.guessIsTrackpad(e);
    let shouldZoom = !trackpad || e.ctrlKey;
    if (shouldZoom) {
      
      let zoomAmount = 1 - e.deltaY * (trackpad ? 0.009 : 0.002);
      this.zoomByAmountKeepingPointConstant(zoomAmount, e.offsetX, e.offsetY);

      // momentum
      let dt = e.timeStamp - this.lastWheelTimeStamp;
      this.lastWheelTimeStamp = e.timeStamp;
      if (dt < 100) {
        let speedEstimate = Math.log(zoomAmount) / dt;
        this.zoomMomentum = (this.zoomMomentum * 0.5) + (speedEstimate * 0.5);
      } else {
        this.zoomMomentum = 0;
      }
      if (this.zoomMomentumTimeoutID !== null) {
        clearTimeout(this.zoomMomentumTimeoutID);
      }
      let startTime = timelineTime();
      this.zoomMomentumTimeoutID = setTimeout(() => {
        this.zoomMomentumTimeoutID = null;
        this.enableZoomMomentum(startTime, e.offsetX, e.offsetY);
      }, 15);
    } else {
      // pan
      this.panX -= e.deltaX * 0.5;
      this.panY -= e.deltaY * 0.5;
    }

    this.renderFrame(this.lastRenderedFrame);

  }
  enableZoomMomentum(startTime: number, cursorRealX: number, cursorRealY: number): void {
    this.disableZoomMomentum();
    if (this.lifecycle.dead || this.lastRenderedFrame === null || Math.abs(this.zoomMomentum) < 0.001) {
      return;
    }
    this.zoomMomentumStartZoomLevel = this.zoomLevel;
    this.zoomMomentumStartPanX = this.panX;
    this.zoomMomentumStartPanY = this.panY;
    this.zoomMomentumStartTime = startTime;
    this.zoomMomentumCursorRealX = cursorRealX;
    this.zoomMomentumCursorRealY = cursorRealY;
    let cb = (endTime: number) => {
      if (this.lifecycle.dead || this.lastRenderedFrame === null) {
        return;
      }
      this.zoomMomentumRAFID = null;
      let dt = endTime - this.zoomMomentumStartTime!;
      // integrate (v * (1 - decay)^t) from 0 to t_0...
      // -> v * ((1 - decay)^t - 1) / log(1 - decay)
      let dtScale = 0.01;
      let decay = 0.8;
      let momentumMultiplier = ((1 - decay) ** (dt * dtScale) - 1) / Math.log(1 - decay);
      let amount = (this.zoomMomentum / dtScale) * momentumMultiplier;
      let multiplier = Math.exp(amount);



      //let avgSpeed = amount / dt;
      //console.log(`dt=${dt * dtScale} / avgSpeed=${avgSpeed} momentum=${this.zoomMomentum} mm=${momentumMultiplier}`);
      if (dt * dtScale >= 5) {
        // done
        this.zoomMomentumRAFID = null;
        return;
      }


      this.zoomLevel = this.zoomMomentumStartZoomLevel!;
      this.panX = this.zoomMomentumStartPanX!;
      this.panY = this.zoomMomentumStartPanY!;
      this.zoomByAmountKeepingPointConstant(multiplier, this.zoomMomentumCursorRealX!, this.zoomMomentumCursorRealY!);

      this.renderFrame(this.lastRenderedFrame);

      this.zoomMomentumRAFID = requestAnimationFrame(cb);
    };
    cb(timelineTime());
  }
  disableZoomMomentum(): void {
    if (this.zoomMomentumRAFID !== null) {
      cancelAnimationFrame(this.zoomMomentumRAFID!);
      this.zoomMomentumRAFID = null;
    }
  }
  onMouseDown(e: MouseEvent): void {
    if (this.lastRenderedFrame === null) {
      return;
    }
    this.disableZoomMomentum();
    this.mouseDownClientX = e.clientX;
    this.mouseDownClientY = e.clientY;
    window.addEventListener('mousemove', this.onMouseMoveBound);
    window.addEventListener('mouseup', this.onMouseUpBound);
    e.preventDefault();
  }
  onMouseUp(e: MouseEvent): void {
    this.onMouseMove(e);
    this.removeMouseMoveUp();
  }
  onMouseMove(e: MouseEvent): void {
    e.preventDefault();
    if (this.lastRenderedFrame === null) {
      return;
    }
    this.panX += e.clientX - this.mouseDownClientX!;
    this.panY += e.clientY - this.mouseDownClientY!;

    this.mouseDownClientX = e.clientX;
    this.mouseDownClientY = e.clientY;

    this.renderFrame(this.lastRenderedFrame);
  }

  removeMouseMoveUp() {
    window.removeEventListener('mousemove', this.onMouseMoveBound);
    window.removeEventListener('mouseup', this.onMouseUpBound);
  }
};

class Scrubbable {
  readonly lifecycle = new Lifecycle();
  scrubber: HTMLInputElement;
  scrubberDisplay: HTMLDivElement;
  frameIdx = 0;
  framesLength = 0;
  frames: HoseFrame[] = undefined as any;
  constructor(public diagram: Diagram, scrubberRow: HTMLElement) {
    this.scrubber = downcast(HTMLInputElement, scrubberRow.querySelector('#scrubber'));

    this.scrubberDisplay = downcast(HTMLDivElement, scrubberRow.querySelector('#scrubber-display'));

    this.onChangeOrInput = this.onChangeOrInput.bind(this);
    this.lifecycle.pushKillEventListener(
      this.scrubber, 'change', this.onChangeOrInput.bind(this) as EventListener);
    this.lifecycle.pushKillEventListener(
      this.scrubber, 'input', this.onChangeOrInput.bind(this) as EventListener);
    this.lifecycle.pushKillEventListener(
      this.scrubber, 'wheel', this.onWheel.bind(this) as EventListener,
      {passive: false});

    this.updateFrames([]);
  }
  onChangeOrInput() {
    let oldFrameIdx = this.frameIdx;
    this.frameIdx = this.frames.length <= 1 ? 0 : parseInt(this.scrubber.value);
    this.framesLength = this.frames.length;
    this.updateRender(this.frameIdx);
  }
  onWheel(e: WheelEvent) {
    if (e.deltaX == 0) {
      return;
    }
    e.preventDefault();
    console.log(e);
  }
  updateRender(oldFrameIdx: number) {
    let text;
    if (this.framesLength == 0) {
      text = '0/0';
    } else {
      if (this.frameIdx !== oldFrameIdx) {
        this.diagram.renderFrame(this.frames[this.frameIdx]);
      }
      text = `${this.frameIdx + 1}/${this.framesLength}`;
    }
    this.scrubberDisplay.textContent = text;
  }
  updateFrames(frames: HoseFrame[]) {
    if (this.lifecycle.dead) {
      return;
    }
    let oldFrameIdx = this.frameIdx;
    if (this.frameIdx >= this.framesLength - 1) {
      this.frameIdx = frames.length - 1;
    }
    this.frames = frames;
    this.framesLength = frames.length;
    if (frames.length <= 1) {
      // since min=max=value doesn't show as filled, fake it
      this.scrubber.min = '-1';
      this.scrubber.max = '0';
      this.scrubber.value = '0';
      this.scrubber.disabled = true;
    } else {
      this.scrubber.min = '0';
      this.scrubber.max = ''+(frames.length - 1);
      this.scrubber.value = ''+(this.frameIdx);
      this.scrubber.disabled = false;
    }
    this.updateRender(oldFrameIdx);
  }
}

function readableToArrayAsync<R>(readable: ReadableStream<R>): Promise<R[]> {
  // This one-liner is enough in Chrome but not Safari for some reason:
  // return Array.fromAsync(readable);

  let reader = readable.getReader();
  let ai = {[Symbol.asyncIterator]() { return { next() { return reader.read(); }}; }};
  return Array.fromAsync(ai) as Promise<R[]>; // XXX: is this right?
}

async function compressBufs(bufs: ArrayBuffer[]): Promise<URL> {
  let compression = new CompressionStream('gzip');
  let compressedBufs = readableToArrayAsync(compression.readable);
  let writer = compression.writable.getWriter();
  for (let buf of bufs) {
    await writer.write(buf);
  }
  await writer.close();
  let cb = await compressedBufs;
  let file = new Blob(cb);
  let url = await new Promise((resolve, reject) => {
    const reader = Object.assign(new FileReader(), {
      onload: () => resolve(reader.result),
      onerror: () => reject(reader.error),
    });
    reader.readAsDataURL(file);
  });
  return url;
}

async function decompressBuf(url: string): Promise<ArrayBuffer> {
  let buf = await (await fetch(url)).arrayBuffer();
  let decompression = new DecompressionStream('gzip');
  let decompressedBufs = readableToArrayAsync(decompression.readable);
  let writer = decompression.writable.getWriter();
  await writer.write(buf);
  await writer.close();
  let db = await decompressedBufs;
  let length = 0;
  for (let dbuf of db) {
    length += dbuf.byteLength;
  }
  let total = new ArrayBuffer(length);
  let offset = 0;
  for (let dbuf of db) {
    let u8 = new Uint8Array(total, offset, dbuf.byteLength);
    u8.set(new Uint8Array(dbuf));
    offset += dbuf.byteLength;
  }
  return total;
}

async function stringifyWithArrayBuffers(data: any): Promise<string> {
  let curOffset = 0;
  let bufs: ArrayBuffer[] = [];
  let bufToStr = new Map<ArrayBuffer, string>;
  let json = JSON.stringify(data, (key, obj) => {
    if (obj instanceof ArrayBuffer) {
      let str = bufToStr.get(obj);
      if (str === undefined) {
        bufs.push(obj);
        str = `\x01${curOffset},${obj.byteLength}`;
        curOffset += obj.byteLength;
        bufToStr.set(obj, str);
      }
      return str;
    }
    return obj;
  });
  return json + '\n' + await compressBufs(bufs);
}

async function parseWithArrayBuffers(jsonPlus: string): Promise<any> {
  let [json, url] = jsonPlus.split('\n');
  let raw = await decompressBuf(url);
  let strToBuf = new Map;
  return JSON.parse(json, (key, obj) => {
    // silly in-band signaling
    if (typeof obj == 'string' && obj[0] === '\x01') {
      let ab = strToBuf.get(obj);
      if (ab === undefined) {
        let [_offset, _length] = obj.substr(1).split(',');
        let offset = parseInt(_offset);
        let length = parseInt(_length);
        ab = new ArrayBuffer(length);
        new Uint8Array(ab).set(new Uint8Array(raw, offset, length));
        strToBuf.set(obj, ab);
      }
      return ab;
    }
    return obj;
  });
}


class NormalCollider {
}

class HoseFrame {
  constructor(number) {
    this.number = number;
    this.hitboxes = new Map();
    this.relevantHoseMessages = [];
    this.normalColliders = [];
    this.scolColliders = [];
    this.bgCollisionPointEvents = [];
    this.bgCollisionTwoPointEvents = [];
    this.hitboxCollisionEvents = [];
  }
  toString() {
    return `frame[${this.number}]`;
  }

  save(isFirst) {
    let relevantHoseMessages = this.relevantHoseMessages;
    if (isFirst) {
      relevantHoseMessages = [this.relevantHoseMessages[0]];
      for (let data of this.normalColliders) {
        if (data.firstSeenFrameNumber !== this.number) {
          relevantHoseMessages.push(data.rawData);
        }
      }
      relevantHoseMessages.push(...this.relevantHoseMessages.slice(1));
    }
    return {
      number: this.number,
      relevantHoseMessages,
    }
  }

  static loadInto(frameInfo, hoseParser) {
    for (let [i, buffer] of frameInfo.relevantHoseMessages.entries()) {
      let tag = hoseParser.parse(buffer);
      if (i == 0) {
        assert(tag == 'do_many');
        hoseParser.curFrame.number = frameInfo.number;
      }
    }
  }

  static async loadFramesInto(frames, hoseParser) {
    for (let [i, frame] of frames.entries()) {
      hoseParser.dontUpdateDiagram = i != frames.length - 1;
      HoseFrame.loadInto(frame, hoseParser);
      await yieldMe();
    }
    hoseParser.finalizeFrame();
  }
};

class HoseParser {
  constructor(mmTypes, scrubbable) {
    this.mmTypes = mmTypes;
    this.scrubbable = scrubbable;

    this.savedFrames = [];
    this.curFrame = new HoseFrame(0);

    this.normalColliderMap = new Map();
    this.scolColliderMap = new Map();

    this.tagStats = {};

    this.dontUpdateDiagram = false;
  }
  parse(buffer) {
    assert(buffer instanceof ArrayBuffer);
    let r = new Reader(buffer);
    let tag = r.read(ty.tag8);

    this.tagStats[tag] ??= [0, 0];
    this.tagStats[tag][0] += r.size; // total length
    this.tagStats[tag][1]++; // message count

    let bufferToSave = null;

    switch (tag) {
    case 'collisi':
      this.parseCollision(r);
      break;
    case 'do_many':
      this.parseDoMany(r);
      break;
    case 'hitbox':
      this.parseHitbox(r);
      break;
    case 'normco+':
    case 'normco*':
      this.parseNormalCollider(r);
      break;
    case 'normco-':
      this.parseNormalColliderGone(r);
      break;
    case 'scolcol':
      this.parseScolCollider(r);
      break;
    case 'scolco~':
      bufferToSave = this.parseHashedScolCollider(r);
      break;
    case 'bgcol2p':
      this.parseBGCollisionTwoPoint(r);
      break;
    case 'bgcol1p':
      this.parseBGCollisionPoint(r);
      break;
    case 'overrun':
      this.parseOverrun(r);
      break;
    case 'memmon':
    case 'pause':
    case 'unpause':
    case 'allcols':
    // TODO
      break;
    default:
      addStatsLine(`hose message with unknown tag ${JSON.stringify(tag)}`);
    }
    r.expectEnd();

    if (bufferToSave === null) {
      // either not scol, or parseScolCollider returned null
      bufferToSave = buffer;
    }
    assert(bufferToSave instanceof ArrayBuffer);
    this.curFrame.relevantHoseMessages.push(bufferToSave);

    //console.log(`hose message ${new BigUint64Array(e.data)}`);
    return tag;
  }

  finalizeFrame() {
    for (let data of this.normalColliderMap.values()) {
      if (data.firstSeenFrameNumber !== this.curFrame.number) {
        this.curFrame.normalColliders.push(data);
      }
    }

    let oldFrame = this.curFrame;
    this.savedFrames.push(oldFrame);
    if (!this.dontUpdateDiagram) {
      this.scrubbable.updateFrames(this.savedFrames);
    }
    this.curFrame = new HoseFrame(oldFrame.number + 1);
  }

  parseDoMany(r) {
    let areaSys = r.read(ty.u64);
    let worldID = r.read(ty.u64);
    if (worldID == 0) {
      this.finalizeFrame();
    };
  }
  parseCollision(r) {
    let hb1 = r.read(ty.u64);
    let hb2 = r.read(ty.u64);
    let ret = r.read(ty.u64);
    let data = {
      hb1: this.curFrame.hitboxes.get(hb1),
      hb2: this.curFrame.hitboxes.get(hb2),
      ret: ret,
    }
    if (hb1) {
      data.worldID = data.hb1.worldID;
    } else if (hb2) {
      data.worldID = data.hb2.worldID;
    } else {
      data.worldID = -1;
    }
    this.curFrame.hitboxCollisionEvents.push(data);
  }
  parseHitbox(r) {
    let ptr = r.read(ty.u64);
    let hb = r.read(this.mmTypes.Hitbox, r.remaining() - 1);
    hb.worldID = r.read(ty.s8);
    this.curFrame.hitboxes.set(ptr, hb);
  }
  parseNormalCollider(r) {
    let data = new NormalCollider;
    Object.assign(data, {
      ptr: r.read(ty.u64),
      col: r.read(this.mmTypes.Collider, 0x3e0),
      extPosCur: r.read(this.mmTypes.Point2D),
      extPosOld: r.read(this.mmTypes.Point2D),
      firstSeenFrameNumber: this.curFrame.number,
      segmentsCur: [],
      segmentsOld: [],
      rawData: r.dv.buffer,
    });
    for (let i = 0, count = data.col.segments_cur.count; i < count; i++) {
      data.segmentsCur.push(r.read(this.mmTypes.ColliderSegment));
    }
    for (let i = 0, count = data.col.segments_old.count; i < count; i++) {
      data.segmentsOld.push(r.read(this.mmTypes.ColliderSegment));
    }
    data.worldID = r.read(ty.s8);
    this.normalColliderMap.set(data.ptr, data);
    this.curFrame.normalColliders.push(data);
  }
  parseNormalColliderGone(r) {
    let ptr = r.read(ty.u64);
    this.normalColliderMap.delete(ptr);
  }
  parseScolCollider(r) {
    let data = {
      ptr: r.read(ty.u64),
      scol: r.read(this.mmTypes.Scol, 0x103c),
      extPosCur: r.read(this.mmTypes.Point2D),
      extPosOld: r.read(this.mmTypes.Point2D),
      worldID: r.read(ty.s8),
      originalMessage: r.dv.buffer,
    };
    data.scol._ptr = data.ptr;
    this.scolColliderMap.set(data.ptr, data);
    this.curFrame.scolColliders.push(data);
  }
  parseHashedScolCollider(r) {
    let ptr = r.read(ty.u64);
    let data = this.scolColliderMap.get(ptr);
    if (data !== undefined) {
      this.curFrame.scolColliders.push(data);
      // return the message that needs to be saved if we save to file
      return data.originalMessage;
    } else {
      // this could happen right when connecting
      addStatsLine(`!! missing scol for ${ptr}`);
      return null;
    }
  }
  parseBGCollisionTwoPoint(r) {
    let data = {
      worldID: r.read(ty.u8),
      xy1: r.read(this.mmTypes.Point2D),
      xy2: r.read(this.mmTypes.Point2D),
      shapemask: r.read(ty.u32),
      enable1: r.read(ty.u8),
      enable2: r.read(ty.u8),
      ret: r.read(ty.u8),
      cp: r.read(ty.u8) ? r.read(this.mmTypes.CollisionPoint) : null,
    };
    data.bic = data.cp?.bic;
    this.curFrame.bgCollisionTwoPointEvents.push(data);
  }
  parseBGCollisionPoint(r) {
    let data = {
      worldID: r.read(ty.u8),
      xy: r.read(this.mmTypes.Point2D),
      enable1: r.read(ty.u8),
      enable2: r.read(ty.u8),
      ret: r.read(ty.u8),
      bic: r.read(this.mmTypes.CollisionBIC),
    };
    this.curFrame.bgCollisionPointEvents.push(data);
  }
  parseOverrun(r) {
    document.getElementById('overrun').innerHTML = 'got overrun!';
  }
}

class HoseConn implements WSWrapperDelegate {
  wsw: WSWrapper;
  readonly lifecycle = new Lifecycle();
  constructor(baseURL: string, public hoseParserPromise: Promise<HoseParser>) {
    this.wsw = new WSWrapper(`${baseURL}/ws/hose`, this);
    this.wsw.setWantOpen(true);
    this.hoseParserPromise = hoseParserPromise;
    this.lifecycle.killCallbacks.push(() => {
      this.wsw.kill();
    });
  }
  onOpen() {
  }
  async onMessage(e: MessageEvent<any>) {
    (await this.hoseParserPromise).parse(e.data);
  }
  onClose() {
  }
}

var g_exportJsonPromise = (async () => {
  return await (await fetch('../exlaunch/build/export.json')).json();
})();

/*
class ButtonWrapper
  constructor(button, onClick) {
    this.onClick = onClick;
    this.button = button;
    button.addEventListener('click', onClick);
  }
  kill() {
    this.button.removeEventListener('click', this.onClick);
  }
}
*/

class BaseConns {
  readonly lifecycle = new Lifecycle();
  readonly hoseParserPromise: Promise<HoseParser>;
  readonly mmTypesPromise: Promise<string>; // XXX
  readonly stillAlivePR: PromiseWithResolvers<never>;
  diagram?: Diagram;
  scrubbable?: Scrubbable;
  _hoseParser?: HoseParser;
  constructor(hash: string) {
    location.hash = hash;
    this.hoseParserPromise = this.doHoseParser();
    this.mmTypesPromise = this.doMMTypes();
    this.stillAlivePR = this.pushKill(Promise.withResolvers());
    this.loadingSig = this.pushKill(new Signal(true));
  }

  async doHoseParser(): Promise<HoseParser> {
    await yieldMe();
    let mmTypes = await this.mmTypesPromise;
    let diagram = this.lifecycle.pushKill(new Diagram(document.getElementById('diagram'), mmTypes));
    this.diagram = diagram; // mostly for debugging
    let scrubbable = this.lifecycle.pushKill(new Scrubbable(diagram, document.getElementById('scrubber-row')));
    this.scrubbable = scrubbable; // ditto
    let parser = new HoseParser(mmTypes, scrubbable);
    this._hoseParser = parser; // ditto
    return parser;
  }

  async doMMTypes(): Promise<string> {
    await yieldMe();
    let [helloInfo, exportJson] = await Promise.any([
      Promise.all([this.helloInfoPromise, g_exportJsonPromise]),
      this.stillAlivePR.promise,
    ]);
    return new MMTypes(helloInfo, exportJson);
  }

  async saveRaw(nframes) {
    let savedFrames = (await this.hoseParserPromise).savedFrames;
    savedFrames = savedFrames.slice(-nframes);
    if (savedFrames.length && savedFrames[0].number == 0) {
      savedFrames.shift();
    }
    return {
      frames: savedFrames.map((f, i) => f.save(i == 0)),
      helloInfo: (await this.helloInfoPromise).rawData,
    }
  }
  async save(nframes) {
    return await stringifyWithArrayBuffers(await this.saveRaw(nframes));
  }
}

function addStatsLine(text: string) {
  let div = document.createElement('div');
  div.textContent = text;
  let parent = downcast(HTMLElement, document.getElementById('stats'));
  parent.insertBefore(div, parent.firstChild);
  while (parent.childElementCount > 10) {
    parent.removeChild(parent.lastChild);
  }
}

class LiveConns extends BaseConns {
  constructor(public baseURL: string) {
    super('live');

    this.rpc = this.pushKill(new RPCConn(baseURL));
    this.helloInfoPromise = this.rpc.helloInfoPR.promise;

    this.hose = this.pushKill(new HoseConn(baseURL, this.hoseParserPromise));

    document.getElementById('controls-left').removeAttribute('inert');

    this.pushCatchKill(this.doStats());
    this.pushCatchKill(this.doCheckboxes());

    this.loadingSig.set(false);
  }
  async doStats() {
    let lastFrameNum = 0;
    while (!this.dead) {
      if (this.rpc.isOpen) {
        let stats = await this.rpc.reqGetStats();
        let hoseParser = await this.hoseParserPromise;
        let frame = hoseParser.curFrame;
        let frames = frame.number - lastFrameNum;
        lastFrameNum = frame.number;
        let text = `stats: overrunBytes=${stats.overrunBytes} sentBytes=${stats.sentBytes} bp=${stats.backpressuredNsec} frames=${frames} tagStats=${JSON.stringify(hoseParser.tagStats)}\n`
        addStatsLine(text);
        hoseParser.tagStats = {};
      }
      await sleep(1000);
    }
  }

  async updateFlags() {
    let flags = [
      ['controls-backpressure', 1n],
      ['controls-send-coll-stuff', 2n], // XXX
      ['controls-bg-events', 4n],
      ['controls-pause', 8n],
      ['controls-send-coll-stuff', 0x10n],
    ];
    let allFlagVals = 0n;
    let setFlagVals = 0n;
    for (let [flagName, flagVal] of flags) {
      allFlagVals |= flagVal;
      if (document.getElementById(flagName).checked) {
        setFlagVals |= flagVal;
      }
    }
    await this.rpc.reqSetFlags(allFlagVals & ~setFlagVals, setFlagVals);
  }

  async doCheckboxes() {
    let checkedPR = Promise.withResolvers();
    for (let input of document.querySelectorAll('#controls input[type=checkbox]')) {
      input.checked = localStorage[input.id] === '1';
      input.onchange = (e) => {
        localStorage[input.id] = input.checked ? '1' : '0';
        //checkedPR.resolve();
      }
    }
    while (1) {
      console.log('wtf');
      await Promise.any([
        Promise.all([
          checkedPR.promise,
          this.rpc.openedCond,
        ]),
        this.stillAlivePR.promise,
      ]);
      await this.updateFlags();
    }
  }
}

class LoadedConns extends BaseConns {
  constructor(savedText) {
    super('loaded');
    this.helloInfoPR = Promise.withResolvers();
    this.helloInfoPromise = this.helloInfoPR.promise;

    document.getElementById('controls-left').setAttribute('inert', '');
    addStatsLine('loading...');
    this.loadStartTime = new Date().getTime();

    this.load(savedText);
  }
  async load(savedText) {
    let stuff = await parseWithArrayBuffers(savedText);
    await this.loadRaw(stuff);
    let loadTime = new Date().getTime() - this.loadStartTime;
    addStatsLine(`loaded in ${loadTime}ms.`);
    this.loadingSig.set(false);
  }
  async loadRaw({frames, helloInfo}) {
    this.helloInfoPR.resolve(readHelloModInfo(new Reader(helloInfo)));
    let hoseParser = await this.hoseParserPromise;
    await HoseFrame.loadFramesInto(frames, hoseParser);
  }

}

function jsMemCallback() {
  let text;
  try {
    let mem = performance.memory.usedJSHeapSize / 1048576;
    text = Math.floor(mem);
  } catch (e) {
    document.getElementById('js-mem-container').style.display = 'none';
    return;
  }
  document.getElementById('js-mem').textContent = text;
}
setInterval(jsMemCallback, 2000);
jsMemCallback();

class ButtonWrapper {
  readonly lifecycle = new Lifecycle();
  elm: HTMLButtonElement;
  clicks: Signal<number>;
  constructor(id: string, {enabled}: {enabled: Signal<boolean> | boolean | undefined}) {
    this.elm = downcast(HTMLButtonElement, document.getElementById(id));
    this.clicks = this.pushKill(Signal.withoutInitialValue());
    this.lifecycle.pushKillEventListener(this.elm, 'click', this.onClick.bind(this));
    this.lifecycle.pushKill(Signal.of<boolean | undefined>(enabled).map(e => {
      this.elm.disabled = !e;
    }));
  }
  onClick() {
    this.clicks.set((this.clicks.maybeCur ?? 0) + 1);
  }
}

class UI {
  readonly lifecycle = new Lifecycle();
  constructor(connsSig: SignalRO<BaseConns>) {
    this.notLoadingSig = connsSig.bind(conns =>
      !conns ? true : conns.loadingSig.map(loading => !loading));
    const lc = this.lifecycle;

    lc.pushKill(lc.pushKill(new ButtonWrapper('load-button', {
      enabled: new Signal(true),//this.notLoadingSig,
    })).clicks.map(() => {
      connsSig.maybeCur?.kill();
      connsSig.set(new LoadedConns(localStorage.zw));
    }));

    lc.pushKill(lc.pushKill(new ButtonWrapper('live-button', {
      enabled: true,
    })).clicks.map(async () => {
      g_connsSig.maybeCur?.kill();
      g_connsSig.set(new LiveConns('ws://neb-solidus:8000'));
    }));

    lc.pushKill(lc.pushKill(new ButtonWrapper('disconnect-button', {
      enabled: true,
    })).clicks.map(async () => {
      g_connsSig.maybeCur?.kill();
    }));

    lc.pushKill(lc.pushKill(new ButtonWrapper('save-button', {
      enabled: this.notLoadingSig,
    })).clicks.map(async () => {
      addStatsLine('saving...');
      localStorage.zw = await connsSig.cur.save();
      addStatsLine('saved.');
    }));
  }
}

g_connsSig = new Signal(null);
g_ui = new UI(g_connsSig);


function loadByHash() {
  if (location.hash.length < 2) {
    return;
  }
  let hash = location.hash.substr(1);
  if (hash == 'live') {
    document.getElementById('live-button').click();
  } else if (hash == 'loaded') {
    // todo: more than one save
    document.getElementById('load-button').click();
  }
}
loadByHash();

