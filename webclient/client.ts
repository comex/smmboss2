import * as mmTypes from './build/mm_types.js';
import { yieldMe, hexbuf, downcast, sleep, assert, hex } from './base.js';
import { Type, u8, u32, u64, s8, Tag8, Pointer, DataViewWithAddr } from './types_base.js';
import { Signal, SignalRO, RefCounted } from './reactive.js';

var theRPCConn: RPCConn

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
  interface Hitbox {
    // This is not part of the actual struct but is appended by `report_hitbox`
    // and then stored by `parseHitbox`.
    worldID: bigint;
  }
  interface Scol {
    // Ditto.
    _ptr?: bigint;
  }
}
Object.defineProperty(mmTypes.Rect, 'width', {
  get(): number { return this.max.x - this.min.x; }
});
Object.defineProperty(mmTypes.Rect, 'height', {
  get(): number { return this.max.y - this.min.y; }
});

class Reader {
  dv: DataView;
  off: bigint;
  size: bigint;
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
  read<T>(ty: Type<T>, size?: bigint, addr?: bigint) {
    if (size === undefined) {
      size = ty.sizeof;
    }
    assert(typeof size == 'bigint');
    let off = this.readOff(size);
    let dv = this.dv;
    if (addr !== undefined) {
      const subDV = new DataView(dv.buffer, Number(off), Number(size));
      (subDV as DataViewWithAddr).addr = addr;
      dv = subDV;
      off = 0n;
    }
    return ty.get(dv, off);
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
type ExlUtilRange = {start: bigint, size: bigint};
function readExlUtilRange(r: Reader): ExlUtilRange {
  return {start: r.read(u64), size: r.read(u64)}
}

type HelloModInfo = Array<{total: ExlUtilRange, text: ExlUtilRange, rodata: ExlUtilRange, data: ExlUtilRange, buildId: string}> & {rawData: ArrayBuffer};
function readHelloModInfo(r: Reader): HelloModInfo {
  return Object.assign(r.readArrayUnbounded(() => ({
    total: readExlUtilRange(r),
    text: readExlUtilRange(r),
    rodata: readExlUtilRange(r),
    data: readExlUtilRange(r),
    buildId: hexbuf(r.readArray(16n, () => r.read(u8))),
  })), {
    rawData: downcast(ArrayBuffer, r.dv.buffer),
  });
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
class WSWrapper extends RefCounted {
  openThrottle: Throttle = new Throttle('open');
  state: string = 'idle';
  ws: WebSocket | null = null;
  wantOpen: boolean = false;
  constructor(public wsURL: string, public delegate: WSWrapperDelegate) {
    super();
  }
  _destructor() {
    if (this.state == 'open') {
      addStatsLine(`closing ${this.wsURL} due to kill`);
      assert(this.ws !== null);
      this.ws.close();
    }
    this.state = 'dead';
    this.wantOpen = false;
    this.ws = null;
  }
  setWantOpen(wantOpen: boolean): void {
    this.wantOpen = wantOpen;
    this.openCloseWSAsNeeded();
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
class RPCConn extends RefCounted implements WSWrapperDelegate {
  readonly rpcThrottle = new Throttle('rpc');
  helloInfoPR: PromiseWithResolvers<HelloModInfo>;
  isOpenSig: Signal<boolean>;
  wsw: WSWrapper;
  pendingResponsePRs: PromiseWithResolvers<ArrayBuffer | string>[] = [];
  constructor(baseURL: string) {
    super();
    this.wsw = this.use(new WSWrapper(`${baseURL}/ws/rpc`, this));
    this.wsw.setWantOpen(true);
    this.helloInfoPR = this.useRejectable(Promise.withResolvers());
    this.isOpenSig = this.use(Signal.create(false));
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

interface ElementDTCache {
  ptr: bigint;
  createdFrameNum: number;
  deleteMe(): void;
}
interface SVGElementWithDTCache extends SVGElement, ElementDTCache {}

interface SVGPolygonWithDTCache extends SVGPolygonElement, ElementDTCache {
  _fill?: string;
  _stroke?: string;
  _strokeWidth?: number;
}
interface SVGLineWithDTCache extends SVGLineElement, ElementDTCache {
  _tx1?: number;
  _tx2?: number;
  _ty1?: number;
  _ty2?: number;
  _stroke?: string;
  _strokeWidth?: number;
}
//interface SVGCircleWithDTCache extends SVGCircleElement, ElementDTCache


abstract class DiagramThingKind<out Thing> {
  group: SVGGElement;
  elmsByPtr: Map<bigint, SVGElementWithDTCache> = new Map();
  genericElmsCount?: number;
  seen?: Set<SVGElementWithDTCache>;
  constructor(public diagram: Diagram) {
    this.group = document.createElementNS(svgNS, 'g') as SVGGElement;
    this.group.setAttribute('kind', this.constructor.name);
    diagram.svg.appendChild(this.group);
  }
  abstract getThingsFromFrame(): Iterable<Thing>;
  abstract getWorldID(thing: Thing): bigint;
  abstract renderThing(thing: Thing): void;
  renderThings(): void {
    this.genericElmsCount = 0;
    let seen = new Set<SVGElementWithDTCache>();
    this.seen = seen;
    for (let thing of this.getThingsFromFrame()) {
      let worldID = this.getWorldID(thing);
      assert(typeof worldID == 'bigint');
      if (worldID !== this.diagram.worldID) {
        continue;
      }
      this.renderThing(thing);
    }
    for (const _elm of Array.from(this.group.children)) {
      const elm = _elm as SVGElementWithDTCache;
      if (!this.seen.has(elm)) {
        //console.log(`${this.diagram.curFrame.number}/${this.diagram.curFrame.worldID} removing stale ${elm} at ${elm.ptr} from ${elm.createdFrameNum}`);
        elm.deleteMe();
      }
    }
    delete this.seen;
    //console.log(`now have ${this.group.childElementCount} children`);
  }
  getOrCreateElmFor(ptr: bigint, wantedTag: string): SVGElementWithDTCache {
    let elm: SVGElementWithDTCache | undefined = this.elmsByPtr.get(ptr);
    //console.log(elm);
    if (elm && elm.tagName != wantedTag) {
      elm.deleteMe();
      elm = undefined;
    }
    if (elm === undefined) {
      const elm_ = document.createElementNS(svgNS, wantedTag) as SVGElementWithDTCache;
      elm = elm_;
      assert(elm.tagName == wantedTag);
      //console.log(`creating ${wantedTag}`);
      elm.ptr = ptr;
      elm.setAttribute('ptr', hex(ptr)); // ** remove this if perf is an issue
      elm.createdFrameNum = this.diagram.curFrame!.number;
      elm.deleteMe = () => {
        //console.log(`** deleteMe for ${wantedTag} ptr=${ptr}`);
        elm_.parentNode!.removeChild(elm_);
        this.elmsByPtr.delete(ptr);
      };
      this.group.appendChild(elm);
      this.elmsByPtr.set(ptr, elm);
    }
    this.seen!.add(elm);
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
    let polygon = this.getOrCreateElmFor(key, 'polygon') as SVGPolygonWithDTCache;
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

  line(key: any, x1: number, y1: number, x2: number, y2: number,
       {stroke, strokeWidth}: {stroke: string, strokeWidth: number}) {
    let dia = this.diagram;
    let line = this.getOrCreateElmFor(key, 'line') as SVGLineWithDTCache;
    let tx1 = dia.transformX(x1);
    let tx2 = dia.transformX(x2);
    let ty1 = dia.transformY(y1);
    let ty2 = dia.transformY(y2);
    if (line._tx1 === tx1 &&
        line._tx2 === tx2 &&
        line._ty1 === ty1 &&
        line._ty2 === ty2 &&
        line._stroke === stroke &&
        line._strokeWidth === strokeWidth) {
      return;
    }
    line.setAttribute('x1', tx1 as any);
    line.setAttribute('y1', ty1 as any);
    line.setAttribute('x2', tx2 as any);
    line.setAttribute('y2', ty2 as any);
    line.setAttribute('stroke', stroke);
    line.setAttribute('stroke-width', strokeWidth as any);

    line._tx1 = tx1;
    line._tx2 = tx2;
    line._ty1 = ty1;
    line._ty2 = ty2;
    line._stroke = stroke;
    line._strokeWidth = strokeWidth;
  }

  circle(key: any, cx: number, cy: number, r: number,
         {fill, stroke, strokeWidth}: {fill: string, stroke?: string, strokeWidth?: number}) {
    let dia = this.diagram;
    let circle = this.getOrCreateElmFor(key, 'circle');
    circle.setAttribute('cx', dia.transformX(cx) as any);
    circle.setAttribute('cy', dia.transformY(cy) as any);
    circle.setAttribute('r', dia.transformSize(r) as any);
    if (fill !== undefined) {
      circle.setAttribute('fill', fill);
    }
    if (stroke !== undefined) {
      circle.setAttribute('stroke', stroke);
      circle.setAttribute('stroke-width', (strokeWidth ?? 1) as any);
    }
  }

  rect(key: any, x: number, y: number, width: number, height: number,
       {fill, stroke, strokeWidth}: {fill: string, stroke: string, strokeWidth: number}) {
    let dia = this.diagram;
    let rect = this.getOrCreateElmFor(key, 'rect');
    rect.setAttribute('x', dia.transformX(x) as any);
    rect.setAttribute('y', dia.transformY(y) as any);
    rect.setAttribute('width', dia.transformSize(width) as any);
    rect.setAttribute('height', dia.transformSize(height) as any);
    if (fill !== undefined) {
      rect.setAttribute('fill', fill);
    }
    if (stroke !== undefined) {
      rect.setAttribute('stroke', stroke);
      rect.setAttribute('stroke-width', (strokeWidth ?? 1) as any);
    }
  }
}

class DiagramHitboxes extends DiagramThingKind<[bigint, mmTypes.Hitbox]> {
  getThingsFromFrame() {
    return this.diagram.curFrame!.hitboxes.entries();
  }
  getWorldID([ptr, hitbox]: [bigint, mmTypes.Hitbox]): bigint {
    return hitbox.worldID;
  }
  renderThing([ptr, hitbox]: [bigint, mmTypes.Hitbox]): void {
    let params = hitbox.params;
    let isCircle = params.is_circle;
    let x = hitbox.rect.min.x;
    let y = hitbox.rect.min.y;
    let w = hitbox.rect.width;
    let h = hitbox.rect.height;

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

class DiagramNormalColliders extends DiagramThingKind<NormalCollider> {
  getThingsFromFrame(): Iterable<NormalCollider> {
    return this.diagram.curFrame!.normalColliders;
  }
  getWorldID(col: NormalCollider): bigint {
    return col.worldID;
  }
  renderThing(col: NormalCollider): void {
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
  renderAsLines(col: NormalCollider) :void {
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

class DiagramScolColliders extends DiagramThingKind<ScolCollider> {
  getThingsFromFrame(): Iterable<ScolCollider> {
    return this.diagram.curFrame!.scolColliders;
  }
  getWorldID(data: ScolCollider): bigint {
    return data.worldID;
  }
  renderThing(data: ScolCollider): void {
    let dia = this.diagram;
    let scol = data.scol;
    if (1) {
      for (let i = 0; i < 4; i++) {
        let fakePtr = data.ptr + BigInt(i);
        let xxy = scol.xxy_ptrs_cur.at(i).pointee;
        if (xxy === null) {
          // TODO (2025-09-28): not sure what null here means, might be I
          // forgot something, but I'm getting it
          continue;
        }

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
      let resultListIdx = scol.result_list_idxs_cur.at(i);
      let resultList = scol.result_lists_cur.at(i);
      let result = resultList.at(resultListIdx);
      if (result.valid) {
        //console.log(result.angle);

      }

    }
  }
}

class DiagramBGCollisionPoints extends DiagramThingKind<BgCollisionPointEvent> {
  getThingsFromFrame() {
    return this.diagram.curFrame!.bgCollisionPointEvents;
  }
  getWorldID(data: BgCollisionPointEvent): bigint {
    return data.worldID;
  }
  renderThing(data: BgCollisionPointEvent): void {
    assert(this.genericElmsCount !== undefined);
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

class DiagramBGCollisionTwoPoints extends DiagramThingKind<BgCollisionTwoPointEvent> {
  getThingsFromFrame() {
    return this.diagram.curFrame!.bgCollisionTwoPointEvents;
  }
  getWorldID(data: BgCollisionTwoPointEvent): bigint {
    return data.worldID;
  }
  renderThing(data: BgCollisionTwoPointEvent): void {
    assert(this.genericElmsCount !== undefined);
    const hit = data.ret;
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
class Diagram extends RefCounted {
  lastRenderedFrame?: HoseFrame;
  zoomLevel = 1;
  panX = 0;
  panY = 400;
  lastWheelTimeStamp = 0;

  zoomMomentum = 0;
  zoomMomentumTimeoutID?: number;

  zoomMomentumStartZoomLevel?: number;
  zoomMomentumStartPanX?: number;
  zoomMomentumStartPanY?: number;
  zoomMomentumStartTime?: number;
  zoomMomentumCursorRealX?: number;
  zoomMomentumCursorRealY?: number;
  zoomMomentumRAFID?: number;
  mouseDownClientX?: number;
  mouseDownClientY?: number;

  readonly kinds: DiagramThingKind<unknown>[];
  readonly hitboxes = new DiagramHitboxes(this);
  readonly normalColliders = new DiagramNormalColliders(this);
  readonly scolColliders = new DiagramScolColliders(this);
  readonly bgCollisionPoints = new DiagramBGCollisionPoints(this);
  readonly bgCollisionTwoPoints = new DiagramBGCollisionTwoPoints(this);

  worldID = 0n;

  readonly onMouseMoveBound = this.onMouseMove.bind(this);
  readonly onMouseUpBound = this.onMouseUp.bind(this);

  curFrame?: HoseFrame;

  constructor(public svg: SVGSVGElement) {
    super();
    this.kinds = [
      this.hitboxes,
      this.normalColliders,
      this.scolColliders,
      this.bgCollisionPoints,
      this.bgCollisionTwoPoints,
    ];

    this.useEventListener(this.svg, 'wheel', this.onWheel.bind(this) as EventListener,
                          {passive: false});
    this.useEventListener(this.svg, 'mousedown', this.onMouseDown.bind(this) as EventListener);
  }
  _destructor() {
    this.svg.innerHTML = '';
    delete this.lastRenderedFrame;
    this.removeMouseMoveUp();
  }
  renderFrame(frame: HoseFrame): void {
    this.curFrame = frame;
    for (let kind of this.kinds) {
      kind.renderThings();
    }
    this.lastRenderedFrame = frame;
    delete this.curFrame;
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
    if (this.lastRenderedFrame === undefined) {
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
      if (this.zoomMomentumTimeoutID !== undefined) {
        clearTimeout(this.zoomMomentumTimeoutID);
      }
      let startTime = timelineTime();
      this.zoomMomentumTimeoutID = setTimeout(() => {
        this.zoomMomentumTimeoutID = undefined;
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
    if (this.isDead() || this.lastRenderedFrame === undefined || Math.abs(this.zoomMomentum) < 0.001) {
      return;
    }
    this.zoomMomentumStartZoomLevel = this.zoomLevel;
    this.zoomMomentumStartPanX = this.panX;
    this.zoomMomentumStartPanY = this.panY;
    this.zoomMomentumStartTime = startTime;
    this.zoomMomentumCursorRealX = cursorRealX;
    this.zoomMomentumCursorRealY = cursorRealY;
    let cb = (endTime: number) => {
      if (this.isDead() || this.lastRenderedFrame === undefined) {
        return;
      }
      this.zoomMomentumRAFID = undefined;
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
        this.zoomMomentumRAFID = undefined;
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
    if (this.zoomMomentumRAFID !== undefined) {
      cancelAnimationFrame(this.zoomMomentumRAFID!);
      this.zoomMomentumRAFID = undefined;
    }
  }
  onMouseDown(e: MouseEvent): void {
    if (this.lastRenderedFrame === undefined) {
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
    if (this.lastRenderedFrame === undefined) {
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

class Scrubbable extends RefCounted {
  scrubber: HTMLInputElement;
  scrubberDisplay: HTMLDivElement;
  frameIdx = 0;
  framesLength = 0;
  frames: HoseFrame[] = undefined as any;
  constructor(public diagram: Diagram, scrubberRow: HTMLElement) {
    super();
    this.scrubber = downcast(HTMLInputElement, scrubberRow.querySelector('#scrubber'));

    this.scrubberDisplay = downcast(HTMLDivElement, scrubberRow.querySelector('#scrubber-display'));

    this.onChangeOrInput = this.onChangeOrInput.bind(this);
    this.useEventListener(
      this.scrubber, 'change', this.onChangeOrInput.bind(this) as EventListener);
    this.useEventListener(
      this.scrubber, 'input', this.onChangeOrInput.bind(this) as EventListener);
    this.useEventListener(
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
    if (this.isDead()) {
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

async function compressBufs(bufs: ArrayBuffer[]): Promise<string> {
  let compression = new CompressionStream('gzip');
  let compressedBufs = readableToArrayAsync(compression.readable);
  let writer = compression.writable.getWriter();
  for (let buf of bufs) {
    await writer.write(buf);
  }
  await writer.close();
  let cb = await compressedBufs;
  let file = new Blob(cb);
  let url = await new Promise<string>((resolve, reject) => {
    const reader = Object.assign(new FileReader(), {
      onload: () => resolve(downcast(String, reader.result)),
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

async function parseWithArrayBuffers(jsonPlus: string): Promise<unknown> {
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


type NormalCollider = {
  ptr: bigint,
  col: mmTypes.Collider,
  extPosCur: mmTypes.Point2D,
  extPosOld: mmTypes.Point2D,
  firstSeenFrameNumber: number,
  segmentsCur: mmTypes.ColliderSegment[],
  segmentsOld: mmTypes.ColliderSegment[],
  rawData: ArrayBuffer,
  worldID: bigint,
};

type ScolCollider = {
  ptr: bigint,
  scol: mmTypes.Scol,
  extPosCur: mmTypes.Point2D,
  extPosOld: mmTypes.Point2D,
  worldID: bigint,
  rawData: ArrayBuffer,
};

type HitboxCollisionEvent = {
  hb1?: mmTypes.Hitbox,
  hb2?: mmTypes.Hitbox,
  ret: bigint,
  worldID: bigint,
};

type BgCollisionPointEvent = {
  worldID: bigint,
  xy: mmTypes.Point2D,
  enable1: bigint,
  enable2: bigint,
  ret: bigint,
  bic: mmTypes.CollisionBIC,
};

type BgCollisionTwoPointEvent = {
  worldID: bigint,
  xy1: mmTypes.Point2D,
  xy2: mmTypes.Point2D,
  shapemask: bigint,
  enable1: bigint,
  enable2: bigint,
  ret: bigint,
  cp?: mmTypes.CollisionPoint,
  bic?: mmTypes.CollisionBIC,
};

type SavedHoseFrame = {
  number: number,
  relevantHoseMessages: ArrayBuffer[],
};

class HoseFrame {
  readonly hitboxes = new Map<bigint, mmTypes.Hitbox>();
  relevantHoseMessages: ArrayBuffer[] = [];
  normalColliders: NormalCollider[] = [];
  scolColliders: ScolCollider[] = [];
  hitboxCollisionEvents: HitboxCollisionEvent[] = [];
  bgCollisionPointEvents: BgCollisionPointEvent[] = [];
  bgCollisionTwoPointEvents: BgCollisionTwoPointEvent[] = [];
  constructor(public number: number) {}
  toString() {
    return `frame[${this.number}]`;
  }

  save(isFirst: boolean): SavedHoseFrame {
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

  static loadInto(frameInfo: SavedHoseFrame, hoseParser: HoseParser): void {
    for (let [i, buffer] of frameInfo.relevantHoseMessages.entries()) {
      let tag = hoseParser.parse(buffer);
      if (i == 0) {
        assert(tag == 'do_many');
        hoseParser.curFrame.number = frameInfo.number;
      }
    }
  }

  static async loadFramesInto(frames: SavedHoseFrame[], hoseParser: HoseParser) {
    for (let [i, frame] of frames.entries()) {
      hoseParser.dontUpdateDiagram = i != frames.length - 1;
      HoseFrame.loadInto(frame, hoseParser);
      await yieldMe();
    }
    hoseParser.finalizeFrame();
  }
};

class HoseParser {
  savedFrames: HoseFrame[] = [];
  curFrame: HoseFrame = new HoseFrame(0);
  normalColliderMap: Map<bigint, NormalCollider> = new Map();
  scolColliderMap: Map<bigint, ScolCollider> = new Map();
  tagStats: { [tag: string]: [bigint, number] } = {};
  dontUpdateDiagram = false;
  constructor(public scrubbable: Scrubbable) {
  }
  parse(buffer: ArrayBuffer): string {
    assert(buffer instanceof ArrayBuffer);
    let r = new Reader(buffer);
    let tag = r.read(Tag8);

    this.tagStats[tag] ??= [0n, 0];
    this.tagStats[tag][0] += r.size; // total length
    this.tagStats[tag][1]++; // message count

    let bufferToSave: ArrayBuffer | undefined = undefined;

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

    if (bufferToSave === undefined) {
      // either not scol, or parseScolCollider returned null
      bufferToSave = buffer;
    }
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

  parseDoMany(r: Reader): void {
    let areaSys = r.read(u64);
    let worldID = r.read(u64);
    if (worldID == 0n) {
      this.finalizeFrame();
    };
  }
  parseCollision(r: Reader): void {
    let hb1 = r.read(u64);
    let hb2 = r.read(u64);
    let ret = r.read(u64);
    let data: HitboxCollisionEvent = {
      hb1: this.curFrame.hitboxes.get(hb1),
      hb2: this.curFrame.hitboxes.get(hb2),
      ret: ret,
      worldID: -1n,
    };
    if (hb1 && data.hb1 !== undefined) {
      data.worldID = data.hb1.worldID;
    } else if (hb2 && data.hb2 !== undefined) {
      data.worldID = data.hb2.worldID;
    }
    this.curFrame.hitboxCollisionEvents.push(data);
  }
  parseHitbox(r: Reader): void {
    let ptr = r.read(u64);
    let hb = r.read(mmTypes.Hitbox, r.remaining() - 1n);
    hb.worldID = r.read(s8);
    this.curFrame.hitboxes.set(ptr, hb);
  }
  parseNormalCollider(r: Reader): void {
    let data: NormalCollider = {
      ptr: r.read(u64),
      col: r.read(mmTypes.Collider, 0x3E0n),
      extPosCur: r.read(mmTypes.Point2D),
      extPosOld: r.read(mmTypes.Point2D),
      firstSeenFrameNumber: this.curFrame.number,
      segmentsCur: [],
      segmentsOld: [],
      rawData: downcast(ArrayBuffer, r.dv.buffer),
      worldID: -1n, // to be overwritten
    };
    for (let i = 0, count = data.col.segments_cur.count; i < count; i++) {
      data.segmentsCur.push(r.read(mmTypes.ColliderSegment));
    }
    for (let i = 0, count = data.col.segments_old.count; i < count; i++) {
      data.segmentsOld.push(r.read(mmTypes.ColliderSegment));
    }
    data.worldID = r.read(s8);
    this.normalColliderMap.set(data.ptr, data);
    this.curFrame.normalColliders.push(data);
  }
  parseNormalColliderGone(r: Reader): void {
    let ptr = r.read(u64);
    this.normalColliderMap.delete(ptr);
  }
  parseScolCollider(r: Reader): void {
    const ptr = r.read(u64);
    let data: ScolCollider = {
      ptr,
      scol: r.read(mmTypes.Scol, 0x103Cn, ptr), // uses DataViewWithAddr
      extPosCur: r.read(mmTypes.Point2D),
      extPosOld: r.read(mmTypes.Point2D),
      worldID: r.read(s8),
      rawData: downcast(ArrayBuffer, r.dv.buffer),
    };
    this.scolColliderMap.set(data.ptr, data);
    this.curFrame.scolColliders.push(data);
  }
  parseHashedScolCollider(r: Reader): ArrayBuffer | undefined {
    let ptr = r.read(u64);
    let data = this.scolColliderMap.get(ptr);
    if (data !== undefined) {
      this.curFrame.scolColliders.push(data);
      // return the message that needs to be saved if we save to file
      return data.rawData;
    } else {
      // this could happen right when connecting
      addStatsLine(`!! missing scol for ${ptr}`);
      return undefined;
    }
  }
  parseBGCollisionTwoPoint(r: Reader): void {
    let data: BgCollisionTwoPointEvent = {
      worldID: r.read(u8),
      xy1: r.read(mmTypes.Point2D),
      xy2: r.read(mmTypes.Point2D),
      shapemask: r.read(u32),
      enable1: r.read(u8),
      enable2: r.read(u8),
      ret: r.read(u8),
      cp: r.read(u8) ? r.read(mmTypes.CollisionPoint) : undefined,
    };
    data.bic = data.cp?.bic;
    this.curFrame.bgCollisionTwoPointEvents.push(data);
  }
  parseBGCollisionPoint(r: Reader): void {
    let data: BgCollisionPointEvent = {
      worldID: r.read(u8),
      xy: r.read(mmTypes.Point2D),
      enable1: r.read(u8),
      enable2: r.read(u8),
      ret: r.read(u8),
      bic: r.read(mmTypes.CollisionBIC),
    };
    this.curFrame.bgCollisionPointEvents.push(data);
  }
  parseOverrun(r: Reader): void {
    document.getElementById('overrun')!.innerHTML = 'got overrun!';
  }
}

class HoseConn extends RefCounted implements WSWrapperDelegate {
  wsw: WSWrapper;
  constructor(baseURL: string, public hoseParserPromise: Promise<HoseParser>) {
    super();
    this.wsw = this.use(new WSWrapper(`${baseURL}/ws/hose`, this));
    this.wsw.setWantOpen(true);
    this.hoseParserPromise = hoseParserPromise;
  }
  onOpen() {
  }
  async onMessage(e: MessageEvent<any>) {
    (await this.hoseParserPromise).parse(e.data);
  }
  onClose() {
  }
}

type Save = {
  frames: SavedHoseFrame[],
  helloInfo: ArrayBuffer,
};

abstract class BaseConns extends RefCounted {
  abstract readonly helloInfoPromise: Promise<HelloModInfo>;
  readonly hoseParserPromise: Promise<HoseParser>;
  readonly mmTypesPromise: Promise<void>;
  readonly stillAlivePR: PromiseWithResolvers<never>;
  diagram?: Diagram;
  scrubbable?: Scrubbable;
  _hoseParser?: HoseParser;
  loadingSig: Signal<boolean>;
  constructor(hash: string) {
    super();
    location.hash = hash;
    this.hoseParserPromise = this.doHoseParser();
    this.mmTypesPromise = this.doMMTypes();
    this.stillAlivePR = this.useRejectable(Promise.withResolvers());
    this.loadingSig = this.use(Signal.create(true));
  }

  async doHoseParser(): Promise<HoseParser> {
    await yieldMe();
    await this.mmTypesPromise;
    let diagram = this.use(new Diagram(downcast(SVGSVGElement, document.getElementById('diagram'))));
    this.diagram = diagram; // mostly for debugging
    let scrubbable = this.use(new Scrubbable(diagram, downcast(HTMLElement, document.getElementById('scrubber-row'))));
    this.scrubbable = scrubbable; // ditto
    let parser = new HoseParser(scrubbable);
    this._hoseParser = parser; // ditto
    return parser;
  }

  async doMMTypes(): Promise<void> {
    await yieldMe();
    const helloInfo = await Promise.any([
      this.helloInfoPromise,
      this.stillAlivePR.promise,
    ]);
    const buildId = helloInfo[0].buildId;
    console.log(`buildId: ${buildId}`);
    const setOffsets = mmTypes.setOffsets[buildId];
    if (!setOffsets) {
      throw new Error(`Unknown main buildId ${buildId}`);
    }
    return;
  }

  async saveRaw(nframes: number): Promise<Save> {
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
  async save(nframes: number): Promise<string> {
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
  helloInfoPromise: Promise<HelloModInfo>;
  rpc: RPCConn;
  hose: HoseConn;
  constructor(public baseURL: string) {
    super('live');

    this.rpc = this.use(new RPCConn(baseURL));
    this.helloInfoPromise = this.rpc.helloInfoPR.promise;

    this.hose = this.use(new HoseConn(baseURL, this.hoseParserPromise));

    downcast(HTMLElement, document.getElementById('controls-left')).removeAttribute('inert');

    /*this.lifecycle.pushCatchKill*/(this.doStats());
    /*this.lifecycle.pushCatchKill*/(this.doCheckboxes());

    this.loadingSig.set(false);
  }
  async doStats() {
    let lastFrameNum = 0;
    while (!this.isDead()) {
      if (this.rpc.isOpenSig.maybeCur) {
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

  async updateFlags(vals: { [_: string]: boolean }): Promise<void> {
    let flags: [string, bigint][] = [
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
      assert(vals[flagName] !== undefined);
      if (vals[flagName]) {
        setFlagVals |= flagVal;
      }
    }
    await this.rpc.reqSetFlags(allFlagVals & ~setFlagVals, setFlagVals);
  }

  async doCheckboxes() {
    using holder = new RefCounted;
    const checkboxes = Array.of(document.querySelectorAll('#controls input[type=checkbox]'))
      .map(input => downcast(HTMLInputElement, input));
    const checkboxSigs: { [name: string]: Signal<boolean> } = {};
    for (const input of checkboxes) {
      const id = input.id;
      input.checked = localStorage[id] === '1';
      const sig = holder.use(Signal.create(input.checked));
      input.onchange = (e: Event) => {
        localStorage[id] = input.checked ? '1' : '0';
        sig.set(input.checked);
      }
      checkboxSigs[id] = sig;
    }
    using checkboxSig = Signal.combine(checkboxSigs).used();
    using combined2 = Signal.combine({
      checkboxVals: checkboxSig,
      isOpen: this.rpc.isOpenSig,
    }).used();
    let lastSetCheckboxVals = '';
    this.use(combined2.map(res => {
      let checkboxValsStr;
      if (res.isOpen && (checkboxValsStr = res.checkboxVals.toString()) != lastSetCheckboxVals) {
        (async () => {
          await this.updateFlags(res.checkboxVals);
          lastSetCheckboxVals = checkboxValsStr;
        })();
      }
    }));
  }
}

class LoadedConns extends BaseConns {
  helloInfoPR: PromiseWithResolvers<HelloModInfo>;
  helloInfoPromise: Promise<HelloModInfo>;
  loadStartTime: number;
  constructor(savedText: string) {
    super('loaded');
    this.helloInfoPR = this.useRejectable(Promise.withResolvers());
    this.helloInfoPromise = this.helloInfoPR.promise;

    document.getElementById('controls-left')!.setAttribute('inert', '');
    addStatsLine('loading...');
    this.loadStartTime = new Date().getTime();

    this.load(savedText);
  }
  async load(savedText: string) {
    let stuff = await parseWithArrayBuffers(savedText) as Save;
    await this.loadRaw(stuff);
    let loadTime = new Date().getTime() - this.loadStartTime;
    addStatsLine(`loaded in ${loadTime}ms.`);
    this.loadingSig.set(false);
  }
  async loadRaw({frames, helloInfo}: Save) {
    this.helloInfoPR.resolve(readHelloModInfo(new Reader(helloInfo)));
    let hoseParser = await this.hoseParserPromise;
    await HoseFrame.loadFramesInto(frames, hoseParser);
  }

}

function jsMemCallback() {
  let text;
  try {
    let mem = (performance as any).memory.usedJSHeapSize / 1048576;
    text = ''+Math.floor(mem);
  } catch (e) {
    document.getElementById('js-mem-container')!.style.display = 'none';
    return;
  }
  document.getElementById('js-mem')!.textContent = text;
}
setInterval(jsMemCallback, 2000);
jsMemCallback();

class ButtonWrapper extends RefCounted {
  elm: HTMLButtonElement;
  clicks: Signal<number>;
  constructor(id: string, {enabled}: {enabled: SignalRO<boolean> | boolean | undefined}) {
    super();
    this.elm = downcast(HTMLButtonElement, document.getElementById(id));
    this.clicks = this.use(Signal.withoutInitialValue<number>());
    this.useEventListener(this.elm, 'click', this.onClick.bind(this));
    using enabledSig = Signal.of<boolean | undefined>(enabled).used();
    this.use(enabledSig.map(e => {
      this.elm.disabled = !e;
    }));
  }
  onClick() {
    this.clicks.set((this.clicks.maybeCur ?? 0) + 1);
  }
}

class UI extends RefCounted {
  notLoadingSig: SignalRO<boolean>;
  constructor(connsSig: Signal<BaseConns>) {
    super();
    this.notLoadingSig = this.use(
      connsSig.bind(conns =>
        !conns ? true : conns.loadingSig.map(loading => !loading))
    );

    this.use(this.use(new ButtonWrapper('load-button', {
      enabled: this.use(Signal.of(true)),//this.notLoadingSig,
    })).clicks.map(() => {
      connsSig.maybeCur?.unref();
      connsSig.set(new LoadedConns(localStorage.zw));
    }));

    this.use(this.use(new ButtonWrapper('live-button', {
      enabled: true,
    })).clicks.map(async () => {
      g_connsSig.maybeCur?.unref();
      g_connsSig.set(new LiveConns('ws://neb-solidus:8000'));
    }));

    this.use(this.use(new ButtonWrapper('disconnect-button', {
      enabled: true,
    })).clicks.map(async () => {
      g_connsSig.maybeCur?.unref();
      // XXX this is bad
    }));

    this.use(this.use(new ButtonWrapper('save-button', {
      enabled: this.notLoadingSig,
    })).clicks.map(async () => {
      addStatsLine('saving...');
      localStorage.zw = await connsSig.cur.save(1000);
      addStatsLine('saved.');
    }));
  }
}

using g_connsSig = Signal.withoutInitialValue<BaseConns>().used();
var g_ui = new UI(g_connsSig);


function loadByHash() {
  if (location.hash.length < 2) {
    return;
  }
  let hash = location.hash.substr(1);
  if (hash == 'live') {
    downcast(HTMLButtonElement, document.getElementById('live-button')).click();
  } else if (hash == 'loaded') {
    // todo: more than one save
    downcast(HTMLButtonElement, document.getElementById('load-button')).click();
  }
}
loadByHash();

