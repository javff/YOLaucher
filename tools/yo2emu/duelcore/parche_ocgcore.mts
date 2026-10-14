/**
 * Parches del LECTOR de mensajes de ocgcore-wasm 0.1.2 (sin tocar node_modules; vale igual con tsx y con el
 * duelcore.mjs de esbuild). Los fallos se encontraron comparando lo que lee la librería con lo que escribe el core de
 * EDOPro (`auditar_lector.py`: cada mensaje tiene que leerse entero) y con los bytes reales (`test_parche_sum.py`):
 *
 * 1. MSG_SELECT_SUM (23; rituales y demás selecciones «por suma»). La librería (`case 23` de readMessage) lee
 *      player u8, select_max u8, amount u32, min u32, max u32,
 *      selects[u32] {code u32, controller u8, location u8, sequence u32, amount u32}, selects_must[u32] {ídem}
 *    pero el core escribe PRIMERO las obligatorias y cada carta con su loc_info entero (controller u8, location u8,
 *    sequence u32, position u32) antes del u32 de la suma:
 *      ..., must[u32] {code u32, loc_info 10 B, sum_param u32}, selects[u32] {code u32, loc_info 10 B, sum_param u32}
 *    Con 0 obligatorias (lo normal en un ritual) salía `selects: []` y `selects_must` con basura (14 B por carta en
 *    vez de 18): ninguna respuesta era válida y el duelo se quedaba pidiendo lo mismo. Con obligatorias, el segundo
 *    contador cae en mitad de los datos: «eof» y se pierde el lote entero. El byte que llama `select_max` es el MODO
 *    (0 = suma exacta, 1 = «al menos»).
 * 2. MSG_SHUFFLE_SET_CARD (36; barajar monstruos colocados). La librería lee el n.º de cartas como u32 y las parejas
 *    (from, to) intercaladas; el core escribe location u8, n u8, n × loc_info de las cartas y n × loc_info de sus
 *    materiales de Xyz (0 si no tienen; visto en los duelos: siempre 0). Se devuelve con la forma de la librería:
 *    `from` = la carta, `to` = ese segundo loc_info.
 *    El u32 sale enorme: «eof» y el duelo se cerraba con error (3 de cada 600 duelos NPC contra NPC).
 * 3. MSG_MOVE (50): la librería no lee el u32 `reason` del final (destruida, sacrificada, coste…). Se añade.
 *
 * Cómo: se envuelve `core.duelGetMessage`. Durante esa llamada (síncrona) `globalThis.DataView` es una subclase que
 * ve la PRIMERA vista que crea la librería: el lote crudo del core en la memoria WASM. Ahí se lee cada mensaje roto
 * con el formato bueno y, si su lector se saldría, se ponen a 0 sus contadores en el propio lote (caben siempre), para
 * que la librería saque listas vacías sin salirse. Después se sustituye (o completa) cada uno, en orden. El búfer es
 * el de mensajes del core: lo rehace en la siguiente llamada, así que escribirlo no cambia nada del duelo.
 *
 * `YO2_PARCHE_SUM=0` lo apaga entero (vuelve el comportamiento roto; solo para diagnosticar).
 * `YO2_AUDITAR_LECTOR=1` apunta en stderr cada tipo cuyo lector no lee exactamente el mensaje (auditar_lector.py).
 */

const MSG_SELECT_SUM = 23;
const MSG_SHUFFLE_SET_CARD = 36;
const MSG_MOVE = 50;
const LOCATION_OVERLAY = 0x80;
const CABECERA_SUM = 1 + 1 + 1 + 4 + 4 + 4; // tipo, player, select_max, amount, min, max
const LOC_INFO = 10;

export interface LocInfo {
  controller: 0 | 1;
  location: number;
  sequence: number;
  position: number;
  overlay_sequence?: number;
}

export interface CartaSuma extends LocInfo {
  code: number;
  /** sum_param del core: valor bajo (u16) y, si no es 0, valor alternativo (u16 alto) */
  amount: number;
}

export interface SelectSum {
  type: number;
  player: number;
  /** Byte que la librería llama «select_max»: 0 = la suma debe ser EXACTA, 1 = «al menos» (select_mode) */
  select_max: number;
  amount: number;
  min: number;
  max: number;
  selects_must: CartaSuma[];
  selects: CartaSuma[];
}

class Lector {
  p: number;
  constructor(private dv: DataView, off: number, private fin: number) {
    this.p = off;
  }
  private need(n: number) {
    if (this.p + n > this.fin) throw new Error("mensaje corto");
  }
  u8() {
    this.need(1);
    return this.dv.getUint8(this.p++);
  }
  u32() {
    this.need(4);
    const v = this.dv.getUint32(this.p, true);
    this.p += 4;
    return v;
  }
  /** loc_info de EDOPro (como parseInfoLocation de la librería: un material de Xyz lleva su índice en position) */
  loc(): LocInfo {
    const controller = this.u8() as 0 | 1;
    const location = this.u8();
    const sequence = this.u32();
    const position = this.u32();
    if (location & LOCATION_OVERLAY) {
      return { controller, location: location & ~LOCATION_OVERLAY, sequence, position: 1, overlay_sequence: position };
    }
    return { controller, location, sequence, position };
  }
}

/** Lee un MSG_SELECT_SUM con el formato del core de EDOPro. `off` apunta al byte del tipo. */
export function leerSelectSum(dv: DataView, off: number, len: number): SelectSum {
  const r = new Lector(dv, off + 1, off + len);
  const carta = (): CartaSuma => {
    const code = r.u32();
    const loc = r.loc();
    return { code, ...loc, amount: r.u32() };
  };
  const player = r.u8();
  const select_max = r.u8();
  const amount = r.u32();
  const min = r.u32();
  const max = r.u32();
  const selects_must = Array.from({ length: r.u32() }, carta);
  const selects = Array.from({ length: r.u32() }, carta);
  return { type: MSG_SELECT_SUM, player, select_max, amount, min, max, selects_must, selects };
}

/** MSG_SHUFFLE_SET_CARD: location u8, n u8, n × loc_info (cartas), n × loc_info (material de Xyz o 0). */
export function leerShuffleSetCard(dv: DataView, off: number, len: number) {
  const r = new Lector(dv, off + 1, off + len);
  const location = r.u8();
  const n = r.u8();
  const from = Array.from({ length: n }, () => r.loc());
  const to = Array.from({ length: n }, () => r.loc());
  return { type: MSG_SHUFFLE_SET_CARD, location, cards: from.map((f, i) => ({ from: f, to: to[i] })) };
}

/** Arreglo de un mensaje del lote: `obj` sustituye al de la librería; `extra` se le añade. */
type Arreglo = { tipo: number; obj?: Record<string, unknown>; extra?: Record<string, unknown> };

/**
 * Recorre un lote del core ([i32 largo][mensaje]...) y devuelve los arreglos, en orden. Con `neutralizar`, deja a 0
 * en el propio lote los contadores que el lector roto leería mal (para que no se salga).
 */
export function corregirLote(dv: DataView, neutralizar: boolean): Arreglo[] {
  const out: Arreglo[] = [];
  let p = 0;
  while (p + 4 <= dv.byteLength) {
    const len = dv.getInt32(p, true);
    p += 4;
    if (len <= 0 || p + len > dv.byteLength) break;
    const tipo = dv.getUint8(p);
    if (tipo === MSG_SELECT_SUM && len >= CABECERA_SUM + 8) {
      out.push({ tipo, obj: leerSelectSum(dv, p, len) as unknown as Record<string, unknown> });
      if (neutralizar) {
        dv.setUint32(p + CABECERA_SUM, 0, true);
        dv.setUint32(p + CABECERA_SUM + 4, 0, true);
      }
    } else if (tipo === MSG_SHUFFLE_SET_CARD && len >= 6) {
      out.push({ tipo, obj: leerShuffleSetCard(dv, p, len) });
      if (neutralizar) dv.setUint32(p + 2, 0, true); // la librería lee aquí un u32 de cartas
    } else if (tipo === MSG_MOVE && len === 1 + 4 + 2 * LOC_INFO + 4) {
      out.push({ tipo, extra: { reason: dv.getUint32(p + len - 4, true) } });
    }
    p += len;
  }
  return out;
}

/** Posición (desde el inicio del lote) -> largo de cada mensaje del lote. */
function posiciones(dv: DataView): Map<number, number> {
  const out = new Map<number, number>();
  let p = 0;
  while (p + 4 <= dv.byteLength) {
    const len = dv.getInt32(p, true);
    p += 4;
    if (len <= 0 || p + len > dv.byteLength) break;
    out.set(p, len);
    p += len;
  }
  return out;
}

// ---------------------------------------------------------------------------------------------------------------------
// SELECT_SUM para un bot: bot.ts (apps/server, compartido) solo busca sumas EXACTAS y no sabe del modo «al menos»;
// con un ritual «nivel 8 o más» sin suma exacta contestaba algo ilegal hasta «el bot no da una respuesta válida».
// ---------------------------------------------------------------------------------------------------------------------
const valores = (a: number) => {
  const v1 = a & 0xffff;
  const v2 = (a >>> 16) & 0xffff;
  return v2 && v2 !== v1 ? [v1, v2] : [v1];
};

/** ¿Vale la selección como la comprueba ocgcore? Exacta: alguna elección de valores suma `target`. Al menos: suma >=
 * target sin carta sobrante (quitando la que menos vale ya no llega: check_with_sum_greater_limit). */
function valida(vals: number[][], target: number, exacta: boolean, lo: number, hi: number): boolean {
  if (vals.length < Math.max(lo, 1) || (hi && vals.length > hi)) return false;
  let estados: [number, number][] = [[0, Infinity]];
  for (const opts of vals) estados = estados.flatMap(([s, m]) => opts.map((v) => [s + v, Math.min(m, v)] as [number, number]));
  return estados.some(([s, m]) => (exacta ? s === target : s >= target && s - m < target));
}

/** Índices de respuesta (obligatorias primero, como bot.ts/brain.py) o null si no hay ninguna selección válida.
 * Prefiere las que menos cartas gastan; busca en profundidad con poda (las listas de un ritual son cortas). */
export function elegirSuma(w: SelectSum): number[] | null {
  const must = w.selects_must.map((c) => valores(c.amount));
  const sel = w.selects.map((c) => valores(c.amount));
  const target = w.amount & 0xffff;
  const exacta = !w.select_max;
  const lo = w.min || 0;
  const hi = w.max || 0;
  const n = must.length;
  let mejor: number[] | null = null;
  let pasos = 0;
  const dfs = (i: number, elegidas: number[], suma: number) => {
    if (++pasos > 200000 || (mejor && elegidas.length >= mejor.length)) return;
    const vals = [...must, ...elegidas.map((k) => sel[k])];
    if (elegidas.length + n > 0 && valida(vals, target, exacta, lo, hi)) {
      mejor = [...elegidas];
      return;
    }
    // `suma` = lo mínimo que suman las elegidas (con valores alternativos, el menor): si ya llega, añadir más no sirve
    // en «al menos» (sobraría) y en «exacta» si ya se pasa
    if (i >= sel.length || (!exacta && suma >= target) || (exacta && suma > target)) return;
    dfs(i + 1, [...elegidas, i], suma + Math.min(...sel[i]));
    dfs(i + 1, elegidas, suma);
  };
  dfs(0, [], must.reduce((t, o) => t + Math.min(...o), 0));
  if (mejor === null) return null;
  return [...Array(n).keys(), ...(mejor as number[]).map((k) => k + n)];
}

// ---------------------------------------------------------------------------------------------------------------------
interface CoreConMensajes {
  duelGetMessage(handle: unknown): { type: number }[];
}

const MARCA = Symbol.for("yo2.parche_select_sum");

/** Instala el parche en el core (idempotente). Devuelve false si está apagado por entorno. */
export function instalarParcheSelectSum(core: CoreConMensajes): boolean {
  if (process.env.YO2_PARCHE_SUM === "0") return false;
  const c = core as CoreConMensajes & { [MARCA]?: boolean };
  if (c[MARCA]) return true;
  const original = c.duelGetMessage;
  const DV = globalThis.DataView;
  let arreglos: Arreglo[] | null = null;
  // Auditoría (YO2_AUDITAR_LECTOR=1, solo para buscar más fallos del lector; ver auditar_lector.py): cada vista
  // que la librería crea para UN mensaje apunta hasta qué byte se ha leído; si no es el largo del mensaje, el
  // lector de ese tipo no casa con lo que escribe el core. Va a stderr una vez por (tipo, largo, leído).
  const auditar = process.env.YO2_AUDITAR_LECTOR === "1";
  let vistas: Espia[] = [];
  let lote: { inicio: number; mensajes: Map<number, number> } | null = null;
  const vistos = new Set<string>();
  class Espia extends DV {
    leido = 0;
    constructor(buffer: ArrayBufferLike, byteOffset?: number, byteLength?: number) {
      super(buffer, byteOffset, byteLength);
      if (arreglos === null) {
        arreglos = [];
        try {
          if (auditar) lote = { inicio: this.byteOffset, mensajes: posiciones(this) };
          arreglos = corregirLote(this, true);
        } catch (e) {
          process.stderr.write(`[parche lector] no pude leer el lote: ${(e as Error).message}\n`);
        }
      } else if (auditar) {
        vistas.push(this);
      }
    }
  }
  if (auditar) {
    const tam: [string, number][] = [["getUint8", 1], ["getInt8", 1], ["getUint16", 2], ["getInt16", 2],
      ["getUint32", 4], ["getInt32", 4], ["getFloat32", 4], ["getFloat64", 8], ["getBigUint64", 8], ["getBigInt64", 8]];
    for (const [nombre, n] of tam) {
      const f = (DV.prototype as unknown as Record<string, (o: number, le?: boolean) => unknown>)[nombre];
      (Espia.prototype as unknown as Record<string, unknown>)[nombre] = function (this: Espia, o: number, le?: boolean) {
        if (o + n > this.leido) this.leido = o + n;
        return f.call(this, o, le);
      };
    }
  }
  const tipoDe = (v: DataView) => new DV(v.buffer, v.byteOffset, 1).getUint8(0);
  const informar = () => {
    if (!lote) return;
    for (const v of vistas) {
      const len = lote.mensajes.get(v.byteOffset - lote.inicio);
      if (len === undefined || v.byteLength !== len) continue;
      const tipo = tipoDe(v);
      if (!vistos.has(`t${tipo}`)) {
        vistos.add(`t${tipo}`);
        process.stderr.write(`[lector-visto] tipo=${tipo}` + String.fromCharCode(10));
      }
      if (v.leido === len) continue;
      const k = `${tipo}/${len}/${v.leido}`;
      if (vistos.has(k)) continue;
      vistos.add(k);
      process.stderr.write(`[lector] tipo=${tipo} largo=${len} leido=${v.leido}\n`);
    }
  };
  c.duelGetMessage = function (handle: unknown) {
    arreglos = null;
    vistas = [];
    lote = null;
    (globalThis as { DataView: typeof DataView }).DataView = Espia as unknown as typeof DataView;
    let msgs: { type: number }[];
    try {
      msgs = original.call(this, handle);
    } catch (e) {
      if (auditar && vistas.length) {
        const v = vistas[vistas.length - 1];
        process.stderr.write(`[lector] tipo=${tipoDe(v)} largo=${v.byteLength} leido=-1 (${(e as Error).message})\n`);
      }
      throw e;
    } finally {
      (globalThis as { DataView: typeof DataView }).DataView = DV;
    }
    if (auditar) informar();
    const fijos = arreglos as Arreglo[] | null;
    arreglos = null;
    if (!fijos || !fijos.length) return msgs;
    const cola = new Map<number, Arreglo[]>();
    for (const a of fijos) cola.set(a.tipo, [...(cola.get(a.tipo) ?? []), a]);
    return msgs.map((m) => {
      const a = cola.get(m.type)?.shift();
      if (!a) return m;
      return (a.obj ?? { ...m, ...a.extra }) as { type: number };
    });
  };
  c[MARCA] = true;
  return true;
}
