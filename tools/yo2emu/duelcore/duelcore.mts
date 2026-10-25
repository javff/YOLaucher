/**
 * duelcore: motor de reglas (ocgcore) para el emulador de YO2, como proceso aparte.
 *
 * Protocolo: una línea JSON por comando en stdin, una línea JSON por evento en stdout.
 * Todo lo demás (avisos de Node, logs) va a stderr para no ensuciar el protocolo.
 * Ver README.md para el formato completo.
 *
 * Reutiliza tal cual apps/server/src/duel/{engine,data,bot,view}.ts (no se modifican).
 * Lo único propio es este adaptador: semilla reproducible, orden del primer jugador,
 * traducción a JSON y el bucle bot/humano.
 *
 * Arranque (lo hace duelcore.py):
 *   node --import <apps/server/node_modules/tsx/dist/loader.mjs> duelcore.mts
 */
import { createInterface } from "node:readline";
import { randomBytes } from "node:crypto";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Duel, loadEngine } from "../../../apps/server/src/duel/engine.ts";
import { Bot } from "../../../apps/server/src/duel/bot.ts";
import { applyToTrack, type DuelTrack } from "../../../apps/server/src/duel/view.ts";
import { elegirSuma, instalarParcheSelectSum, type SelectSum } from "./parche_ocgcore.mts";
import {
  OcgDuelMode,
  OcgLocation,
  OcgLogType,
  OcgMessageType,
  OcgPosition,
  OcgQueryFlags,
  OcgResponseType,
  SelectBattleCMDAction,
  SelectIdleCMDAction,
  type OcgMessage,
  type OcgResponse,
} from "../../../apps/server/node_modules/ocgcore-wasm/dist/index.js";

// stdout es del protocolo: cualquier console.log de los módulos reutilizados va a stderr
console.log = (...a: unknown[]) => console.error(...a);

type SelectMessage = OcgMessage & { player: number };
type Json = Record<string, unknown>;

const loadStart = Date.now();
// Core WASM + cards.cdb + índice de CardScripts: se cargan una vez para todos los duelos
const { core, db, scripts } = await loadEngine();
// ocgcore-wasm 0.1.2 lee mal MSG_SELECT_SUM (rituales): parche en parche_ocgcore.mts (YO2_PARCHE_SUM=0 lo apaga)
const parcheSum = instalarParcheSelectSum(core);
const loadMs = Date.now() - loadStart;

// ---------------------------------------------------------------------------
// Scripts de 2008: cNNNNN.lua que sustituyen a los de CardScripts (ProjectIgnis, textos de HOY) para las cartas cuyo
// texto en el cliente 2008 era otro (erratas posteriores). Carpeta `scripts_2008/` junto a este fichero (en el paquete,
// junto a duelcore.mjs) o la de YO2_SCRIPTS_2008_DIR. YO2_SCRIPTS_2008=0 los apaga (vuelve el comportamiento moderno).
// Se leen UNA vez al arrancar. El vendor no se toca. Ver scripts_2008/LEEME.md.
// ---------------------------------------------------------------------------
const scripts2008Activos = !["0", "false", "off", "no"].includes((process.env.YO2_SCRIPTS_2008 ?? "1").trim().toLowerCase());
const scripts2008 = new Map<string, string>();
if (scripts2008Activos) {
  const dir = process.env.YO2_SCRIPTS_2008_DIR || join(dirname(fileURLToPath(import.meta.url)), "scripts_2008");
  if (existsSync(dir)) {
    for (const f of readdirSync(dir)) if (/^c\d+\.lua$/.test(f)) scripts2008.set(f, readFileSync(join(dir, f), "utf8"));
  }
}
/** scriptReader del core: primero el de 2008 (si hay), luego CardScripts */
const leerScript = (name: string): string | null => {
  const file = name.split(/[\\/]/).pop()!;
  return scripts2008.get(file) ?? scripts.read(name);
};

// ---------------------------------------------------------------------------
// Compat CHAININFO. El ocgcore-wasm 0.1.2 no conoce los CHAININFO_TRIGGERING_LINK/LSCALE/RSCALE (31, 32, 33) que usa el
// chain.lua de CardScripts (2026): Duel.GetChainInfo lanza "Passed invalid CHAININFO flag". chain.lua envuelve
// Duel.RegisterEffect y, si hay un efecto resolviéndose (Duel.GetReasonEffect), pide TODAS las propiedades de su carta
// (incluidas esas) => la RegisterEffect de CUALQUIER carta que registre un efecto desde su operación lanza un error
// de Lua y la operación se corta ahí (Strike Ninja: se destierra pero no se registra su vuelta en la End Phase y no
// vuelve nunca; avisos «chain.lua:85» de los duelos NPC). Las banderas desconocidas devuelven nil (chain.lua ya prevé
// nil en Setcode). YO2_COMPAT_CHAININFO=0 lo apaga (diagnóstico). Se carga tras constant.lua/utility.lua.
// ---------------------------------------------------------------------------
const compatChainInfo = !["0", "false", "off", "no"].includes((process.env.YO2_COMPAT_CHAININFO ?? "1").trim().toLowerCase());
const COMPAT_CHAININFO_LUA = `
do
  local viejo=Duel.GetChainInfo
  local sin_soporte={[31]=true,[32]=true,[33]=true}
  Duel.GetChainInfo=function(ch,...)
    local n=select('#',...)
    local hay=false
    for i=1,n do if sin_soporte[(select(i,...))] then hay=true end end
    if not hay then return viejo(ch,...) end
    local r={}
    for i=1,n do
      local f=select(i,...)
      if not sin_soporte[f] then r[i]=viejo(ch,f) end
    end
    return table.unpack(r,1,n)
  end
end
`;

// ---------------------------------------------------------------------------
// Utilidades
// ---------------------------------------------------------------------------

/** Nombre del tipo de mensaje: DRAW, SELECT_IDLECMD... (enum con mapeo inverso) */
const msgName = (t: number) => (OcgMessageType as unknown as Record<number, string>)[t] ?? `MSG_${t}`;

/** splitmix64: de una semilla (número/cadena) a los 4 u64 de xoshiro256** del core */
function seedWords(seed: bigint): [bigint, bigint, bigint, bigint] {
  const M = (1n << 64n) - 1n;
  let x = seed & M;
  const next = () => {
    x = (x + 0x9e3779b97f4a7c15n) & M;
    let z = x;
    z = ((z ^ (z >> 30n)) * 0xbf58476d1ce4e5b9n) & M;
    z = ((z ^ (z >> 27n)) * 0x94d049bb133111ebn) & M;
    return (z ^ (z >> 31n)) & M;
  };
  const w = [next(), next(), next(), next()] as [bigint, bigint, bigint, bigint];
  if (w.every((v) => v === 0n)) w[0] = 1n;
  return w;
}

/** sfc32: PRNG pequeño para barajar y para el Math.random del bot (reproducible por duelo) */
function sfc32(a: number, b: number, c: number, d: number) {
  return () => {
    a |= 0; b |= 0; c |= 0; d |= 0;
    const t = (((a + b) | 0) + d) | 0;
    d = (d + 1) | 0;
    a = b ^ (b >>> 9);
    b = (c + (c << 3)) | 0;
    c = (c << 21) | (c >>> 11);
    c = (c + t) | 0;
    return (t >>> 0) / 4294967296;
  };
}

function shuffled<T>(xs: T[], rnd: () => number): T[] {
  const out = [...xs];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

function withRandom<T>(rnd: () => number, f: () => T): T {
  const old = Math.random;
  Math.random = rnd;
  try {
    return f();
  } finally {
    Math.random = old;
  }
}

const PLAYER_KEYS = new Set(["player", "controller", "triggering_controller"]);

/**
 * Objeto del core → JSON plano. bigint → number (o cadena si no cabe).
 * Con swap = true se cambian los índices de jugador (el humano siempre es el 0 hacia fuera).
 * Además añade desc_text a todo lo que lleva `description` (texto de la opción/efecto).
 */
function plain(v: unknown, swap: boolean, key = ""): unknown {
  if (typeof v === "bigint") return Number.isSafeInteger(Number(v)) ? Number(v) : v.toString();
  if (Array.isArray(v)) {
    const arr = v.map((x) => plain(x, swap));
    if (swap && arr.length === 2 && (key === "players" || key === "results")) arr.reverse();
    return arr;
  }
  if (v && typeof v === "object") {
    const o: Json = {};
    const src = v as Json;
    for (const [k, x] of Object.entries(src)) {
      if (swap && PLAYER_KEYS.has(k) && (x === 0 || x === 1)) o[k] = 1 - (x as number);
      else o[k] = plain(x, swap, k);
      if (k === "description" && (typeof x === "bigint" || typeof x === "number") && Number(x) !== 0) {
        o.desc_text = db.describe(x, typeof src.code === "number" ? src.code : undefined);
      }
    }
    return o;
  }
  return v;
}

function swapHalves(m: number) {
  return (((m & 0xffff) << 16) | (m >>> 16)) >>> 0;
}

/** Mensaje del core → evento JSON (type = nombre, type_id = número) */
function messageJson(m: OcgMessage, swap: boolean): Json {
  const o = plain(m, swap) as Json;
  o.type_id = m.type;
  o.type = msgName(m.type);
  // FIELD_DISABLED lleva la máscara absoluta (bits 0-15 = jugador 0). La de SELECT_PLACE/DISFIELD
  // es relativa al que elige (bits 0-15 = sus zonas), así que esa no se toca.
  if (swap && m.type === OcgMessageType.FIELD_DISABLED && typeof o.field_mask === "number") {
    o.field_mask = swapHalves(o.field_mask);
  }
  if (m.type === OcgMessageType.SELECT_OPTION) {
    o.options_text = (m as { options: bigint[] }).options.map((d) => db.describe(d));
  }
  if (m.type === OcgMessageType.HINT) {
    const h = m as { hint_type: number; hint: bigint };
    // 3 = SELECTMSG («elige un monstruo para...»), 2 = MESSAGE; 10 = CARD (code)
    if (h.hint_type === 2 || h.hint_type === 3 || h.hint_type === 5) o.hint_text = db.describe(h.hint);
  }
  return o;
}

/** Tipo de respuesta esperado para cada mensaje de selección */
const RESPONSE_FOR: Record<number, number[]> = {
  [OcgMessageType.SELECT_BATTLECMD]: [OcgResponseType.SELECT_BATTLECMD],
  [OcgMessageType.SELECT_IDLECMD]: [OcgResponseType.SELECT_IDLECMD],
  [OcgMessageType.SELECT_EFFECTYN]: [OcgResponseType.SELECT_EFFECTYN],
  [OcgMessageType.SELECT_YESNO]: [OcgResponseType.SELECT_YESNO],
  [OcgMessageType.SELECT_OPTION]: [OcgResponseType.SELECT_OPTION],
  [OcgMessageType.SELECT_CARD]: [OcgResponseType.SELECT_CARD, OcgResponseType.SELECT_CARD_CODES],
  [OcgMessageType.SELECT_CHAIN]: [OcgResponseType.SELECT_CHAIN],
  [OcgMessageType.SELECT_PLACE]: [OcgResponseType.SELECT_PLACE],
  [OcgMessageType.SELECT_POSITION]: [OcgResponseType.SELECT_POSITION],
  [OcgMessageType.SELECT_TRIBUTE]: [OcgResponseType.SELECT_TRIBUTE],
  [OcgMessageType.SORT_CHAIN]: [OcgResponseType.SORT_CARD],
  [OcgMessageType.SELECT_COUNTER]: [OcgResponseType.SELECT_COUNTER],
  [OcgMessageType.SELECT_SUM]: [OcgResponseType.SELECT_SUM],
  [OcgMessageType.SELECT_DISFIELD]: [OcgResponseType.SELECT_DISFIELD],
  [OcgMessageType.SORT_CARD]: [OcgResponseType.SORT_CARD],
  [OcgMessageType.SELECT_UNSELECT_CARD]: [OcgResponseType.SELECT_UNSELECT_CARD],
  [OcgMessageType.ROCK_PAPER_SCISSORS]: [OcgResponseType.ROCK_PAPER_SCISSORS],
  [OcgMessageType.ANNOUNCE_RACE]: [OcgResponseType.ANNOUNCE_RACE],
  [OcgMessageType.ANNOUNCE_ATTRIB]: [OcgResponseType.ANNOUNCE_ATTRIB],
  [OcgMessageType.ANNOUNCE_CARD]: [OcgResponseType.ANNOUNCE_CARD],
  [OcgMessageType.ANNOUNCE_NUMBER]: [OcgResponseType.ANNOUNCE_NUMBER],
};

function enumValue(e: object, v: unknown, what: string): number {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = (e as Record<string, unknown>)[v.toUpperCase()];
    if (typeof n === "number") return n;
  }
  throw new Error(`${what} no válido: ${JSON.stringify(v)}`);
}

/**
 * SORT_CARD / SORT_CHAIN con un orden explícito. El escritor de respuestas de ocgcore-wasm 0.1.2 pone un byte con el
 * NÚMERO de cartas delante (`i8(order.length)`, luego cada `i8(order[i])`), pero el core lee `order` crudo (n bytes, uno por
 * carta, una permutación de 0..n-1; 0xff = orden por defecto): con el byte de más, CUALQUIER orden daba RETRY (medido con
 * Spellbook Organization: [0,1,2], [1,2,0]…; solo valía `order: null`). La librería solo recorre el array con `.length` y
 * `for…of`: un objeto cuyo `.length` es el primer valor y cuyo iterador da los demás escribe justo los n bytes crudos.
 */
function respuestaOrden(orden: number[]): number[] {
  const [primero, ...resto] = orden;
  return {
    get length() {
      return primero;
    },
    *[Symbol.iterator]() {
      yield* resto;
    },
  } as unknown as number[];
}

/** Respuesta JSON del humano → OcgResponse (nombres o números; jugadores de vuelta a índices del core) */
function parseResponse(r: Json, swap: boolean): OcgResponse {
  if (!r || typeof r !== "object") throw new Error("response debe ser un objeto");
  const out: Json = { ...r };
  out.type = enumValue(OcgResponseType, r.type, "response.type");
  if (out.type === OcgResponseType.SELECT_IDLECMD) out.action = enumValue(SelectIdleCMDAction, r.action, "action");
  if (out.type === OcgResponseType.SELECT_BATTLECMD) out.action = enumValue(SelectBattleCMDAction, r.action, "action");
  if (out.type === OcgResponseType.SELECT_IDLECMD || out.type === OcgResponseType.SELECT_BATTLECMD || out.type === OcgResponseType.SELECT_CHAIN || out.type === OcgResponseType.SELECT_UNSELECT_CARD) {
    out.index = r.index ?? null;
  }
  if (out.type === OcgResponseType.SELECT_POSITION) out.position = enumValue(OcgPosition, r.position, "position");
  if (Array.isArray(r.places)) {
    out.places = (r.places as Json[]).map((p) => ({
      player: swap ? 1 - Number(p.player) : Number(p.player),
      location: enumValue(OcgLocation, p.location, "location"),
      sequence: Number(p.sequence),
    }));
  }
  if (out.type === OcgResponseType.SORT_CARD && Array.isArray(r.order) && r.order.length) out.order = respuestaOrden((r.order as unknown[]).map(Number));
  if (Array.isArray(r.races)) out.races = (r.races as unknown[]).map((x) => BigInt(x as number));
  if (Array.isArray(r.attributes)) out.attributes = (r.attributes as unknown[]).map(Number);
  return out as unknown as OcgResponse;
}

// ---------------------------------------------------------------------------
// Duelos
// ---------------------------------------------------------------------------

interface Session {
  id: unknown;
  duel: Duel;
  /** true = el jugador 1 del protocolo es el jugador 0 del core (empieza él) */
  swap: boolean;
  /** bots por índice del CORE */
  bots: (Bot | null)[];
  track: DuelTrack;
  rnd: () => number;
  waiting: SelectMessage | null;
  /** jugadores (índice del PROTOCOLO) cuyas decisiones toma un cerebro externo: su select lleva `view` */
  brain: [boolean, boolean];
  /** respuestas rechazadas (RETRY) por jugador del protocolo */
  retries: [number, number];
  humanRetries: number;
  decisions: number;
  autoChain: boolean;
  autoPassed: number;
}

const sessions = new Map<string, Session>();
const key = (id: unknown) => JSON.stringify(id);

/** Índice del protocolo ↔ índice del core */
const toProto = (s: Session, p: number) => (s.swap && (p === 0 || p === 1) ? 1 - p : p);

class Out {
  lines: string[] = [];
  emit(ev: Json) {
    this.lines.push(JSON.stringify(ev));
  }
  flush() {
    if (this.lines.length) process.stdout.write(this.lines.join("\n") + "\n");
    this.lines = [];
  }
}

function createDuel(
  decks: { main: number[]; extra: number[] }[],
  swap: boolean,
  seed: bigint,
  lp: number,
  shuffle: boolean,
  rnd: () => number,
  flags: bigint,
): Duel {
  const team = { startingLP: lp, startingDrawCount: 5, drawCountPerTurn: 1 };
  const errors: string[] = [];
  const handle = core.createDuel({
    flags,
    seed: seedWords(seed),
    team1: team,
    team2: team,
    cardReader: (code) => db.get(code),
    scriptReader: leerScript,
    errorHandler: (type, text) => {
      // Igual que engine.ts: los monstruos normales no tienen script y no es un error
      if (type === OcgLogType.ERROR && /c\d+\.lua/.test(text) && /not found|cannot open/i.test(text)) return;
      errors.push(`[${type}] ${text}`);
    },
  });
  if (!handle) throw new Error("ocgcore no pudo crear el duelo");
  // El constructor de Duel es `private` solo para TypeScript: en ejecución se puede usar.
  // Así se reutiliza su step() (corte en WIN, RETRY...) con nuestra semilla y nuestro orden.
  const duel = new (Duel as unknown as new (c: unknown, h: unknown) => Duel)(core, handle);
  (duel as unknown as { errors: string[] }).errors = errors;
  for (const s of ["constant.lua", "utility.lua"]) {
    const src = scripts.read(s);
    if (!src || !core.loadScript(handle, s, src)) throw new Error(`no se pudo cargar ${s}`);
  }
  if (compatChainInfo && !core.loadScript(handle, "yo2_compat.lua", COMPAT_CHAININFO_LUA)) throw new Error("no se pudo cargar yo2_compat.lua");
  // El jugador 0 del core es siempre el que empieza
  const coreDecks = swap ? [decks[1], decks[0]] : [decks[0], decks[1]];
  coreDecks.forEach((deck, t) => {
    const main = shuffle ? shuffled(deck.main, rnd) : [...deck.main];
    for (const code of main) {
      core.duelNewCard(handle, { team: t as 0 | 1, duelist: 0, code, controller: t as 0 | 1, location: OcgLocation.DECK, sequence: 0, position: OcgPosition.FACEDOWN_DEFENSE });
    }
    for (const code of deck.extra ?? []) {
      core.duelNewCard(handle, { team: t as 0 | 1, duelist: 0, code, controller: t as 0 | 1, location: OcgLocation.EXTRA, sequence: 0, position: OcgPosition.FACEDOWN_DEFENSE });
    }
  });
  core.startDuel(handle);
  return duel;
}

function endSession(s: Session, out: Out, ev: Json) {
  out.emit({ ev: "end", id: s.id, ...ev, turns: s.track.turn, lp: lpProto(s), decisions: s.decisions, human_retries: s.humanRetries, retries: s.retries, auto_passed: s.autoPassed, core_errors: s.duel.errors.slice(0, 10) });
  try {
    s.duel.destroy();
  } catch {
    /* ya destruido */
  }
  sessions.delete(key(s.id));
}

function lpProto(s: Session) {
  return s.swap ? [s.track.lp[1], s.track.lp[0]] : [s.track.lp[0], s.track.lp[1]];
}

const BOT_LIMIT = 5000;

/** bot.ts decide, salvo SELECT_SUM con el parche: su pickSum solo busca sumas exactas (parche_ocgcore.mts, elegirSuma) */
function decideBot(bot: Bot, w: SelectMessage): OcgResponse {
  if (parcheSum && w.type === OcgMessageType.SELECT_SUM) {
    const idx = elegirSuma(w as unknown as SelectSum);
    if (idx) return { type: OcgResponseType.SELECT_SUM, indicies: idx } as OcgResponse;
  }
  return bot.choose(w);
}

/** Avanza el core: emite los mensajes, deja jugar a los bots y para en la primera decisión humana */
function advance(s: Session, out: Out, t0: number) {
  let botSteps = 0;
  let botRetries = 0;
  for (;;) {
    const r = s.duel.step();
    applyToTrack(s.track, r.messages);
    for (const b of s.bots) b?.observe(r.messages);
    let retry = false;
    for (const m of r.messages) {
      if (m.type === OcgMessageType.RETRY) {
        retry = true;
        continue;
      }
      // La petición de decisión sale como evento "select" (humano) o la resuelve el bot
      if (r.waiting && m === r.waiting) continue;
      out.emit({ ev: "msg", id: s.id, ...messageJson(m, s.swap) });
    }
    if (r.ended) {
      const w = r.winner;
      const win = r.messages.find((m) => m.type === OcgMessageType.WIN) as { reason?: number } | undefined;
      endSession(s, out, { winner: w === undefined ? null : toProto(s, w), reason: win?.reason ?? null, ms: Date.now() - t0 });
      return;
    }
    const w = r.waiting;
    if (!w) throw new Error("el core espera respuesta pero no hay mensaje de selección");
    const bot = s.bots[w.player];
    if (bot) {
      if (retry && ++botRetries > 50) throw new Error(`el bot no da una respuesta válida a ${msgName(w.type)}`);
      if (++botSteps > BOT_LIMIT) throw new Error("el bot no termina (más de 5000 decisiones seguidas)");
      s.decisions++;
      s.duel.respond(withRandom(s.rnd, () => decideBot(bot, w)));
      continue;
    }
    // Ventanas de cadena vacías (pasa en cada fase del rival): se pasan solas salvo auto_chain=false
    if (s.autoChain && !retry && w.type === OcgMessageType.SELECT_CHAIN) {
      const ch = w as { forced: boolean; selects: unknown[] };
      if (!ch.forced && ch.selects.length === 0) {
        s.autoPassed++;
        s.duel.respond({ type: OcgResponseType.SELECT_CHAIN, index: null });
        continue;
      }
    }
    s.waiting = w;
    const who = toProto(s, w.player);
    if (retry) {
      s.humanRetries++;
      if (who === 0 || who === 1) s.retries[who]++;
    }
    const ev: Json = {
      ev: "select",
      id: s.id,
      ...messageJson(w, s.swap),
      retry,
      turn: s.track.turn,
      turn_player: toProto(s, s.track.turnPlayer),
      phase: s.track.phase,
      lp: lpProto(s),
      ms: Date.now() - t0,
    };
    // Cerebro externo (p. ej. research/ia/brain.py): el select lleva el tablero visto por ese jugador
    if ((who === 0 || who === 1) && s.brain[who]) ev.view = viewFor(s, who);
    out.emit(ev);
    return;
  }
}

function getSession(id: unknown): Session {
  const s = sessions.get(key(id));
  if (!s) throw new Error(`no hay duelo con id ${JSON.stringify(id)}`);
  return s;
}

function parseSeed(v: unknown): bigint {
  if (v === undefined || v === null) return randomBytes(8).readBigUInt64LE(0) & ((1n << 53n) - 1n);
  if (typeof v === "number" || typeof v === "string") return BigInt(v);
  throw new Error("seed debe ser número o cadena");
}

function cmdNew(c: Json, out: Out, t0: number) {
  if (c.id === undefined) throw new Error("falta id");
  if (sessions.has(key(c.id))) throw new Error(`ya existe un duelo con id ${JSON.stringify(c.id)}`);
  const decks = c.decks as { main: number[]; extra?: number[] }[];
  if (!Array.isArray(decks) || decks.length !== 2) throw new Error("decks debe tener dos mazos");
  const norm = decks.map((d, i) => {
    const main = (d?.main ?? []).map(Number);
    const extra = (d?.extra ?? []).map(Number);
    if (!main.length) throw new Error(`el mazo ${i} está vacío`);
    const unknown = [...main, ...extra].filter((code) => !db.get(code));
    if (unknown.length) throw new Error(`mazo ${i}: passcodes que no están en cards.cdb: ${[...new Set(unknown)].join(", ")}`);
    return { main, extra };
  });
  const first = Number(c.first ?? 0);
  if (first !== 0 && first !== 1) throw new Error("first debe ser 0 o 1");
  const swap = first === 1;
  const seed = parseSeed(c.seed);
  const lp = Number(c.lp ?? 8000);
  const botFlags = [...((c.bot as boolean[] | undefined) ?? [false, true])];
  // brain: "python" (= el jugador 1) o [j0, j1]. Ese jugador no lo juega bot.ts: recibe select con `view`
  const brainRaw = c.brain;
  let brain: [boolean, boolean] = [false, false];
  if (brainRaw === undefined || brainRaw === null || brainRaw === false) brain = [false, false];
  else if (typeof brainRaw === "string" || brainRaw === true) brain = [false, true];
  else if (Array.isArray(brainRaw) && brainRaw.length === 2) brain = [Boolean(brainRaw[0]), Boolean(brainRaw[1])];
  else throw new Error('brain debe ser "python" o [j0, j1]');
  brain.forEach((b, p) => {
    if (b) botFlags[p] = false;
  });
  const words = seedWords(seed ^ 0x5eed5eedn);
  const rnd = sfc32(Number(words[0] & 0xffffffffn), Number(words[0] >> 32n), Number(words[1] & 0xffffffffn), Number(words[1] >> 32n));
  // Reglas: MR5 por defecto (igual que DuelCards). "MR1" = la época de YO2 (2008), "MR2".."MR5", "GOAT"...
  const rule = String(c.rule ?? "MR5").toUpperCase();
  const flags = (OcgDuelMode as unknown as Record<string, bigint>)[`MODE_${rule}`];
  if (typeof flags !== "bigint") throw new Error(`rule no válida: ${rule} (MR1..MR5, GOAT, SPEED, RUSH)`);
  const duel = createDuel(norm, swap, seed, lp, c.shuffle !== false, rnd, flags);
  const bots: (Bot | null)[] = [0, 1].map((coreP) => {
    const protoP = swap ? 1 - coreP : coreP;
    return botFlags[protoP] ? new Bot((code) => db.get(code)) : null;
  });
  const s: Session = { id: c.id, duel, swap, bots, track: { lp: [lp, lp], turn: 0, turnPlayer: 0, phase: 0 }, rnd, waiting: null, brain, retries: [0, 0], humanRetries: 0, decisions: 0, autoChain: c.auto_chain !== false, autoPassed: 0 };
  sessions.set(key(c.id), s);
  out.emit({ ev: "created", id: c.id, first, seed: seed.toString(), lp, bot: [Boolean(botFlags[0]), Boolean(botFlags[1])], brain, auto_chain: s.autoChain, rule });
  advance(s, out, t0);
}

function cmdRespond(c: Json, out: Out, t0: number, auto = false) {
  const s = getSession(c.id);
  const w = s.waiting;
  if (!w) throw new Error("el duelo no está esperando ninguna respuesta");
  // Opcional: "player" en respond/auto = comprobar que la decisión pendiente es de ese jugador
  if (c.player !== undefined && c.player !== null && Number(c.player) !== toProto(s, w.player)) {
    throw new Error(`la decisión pendiente es del jugador ${toProto(s, w.player)}, no del ${c.player}`);
  }
  let resp: OcgResponse;
  if (auto) {
    // Que decida bot.ts por el humano (p. ej. si se le acaba el tiempo en el cliente)
    resp = withRandom(s.rnd, () => decideBot(new Bot((code) => db.get(code)), w));
  } else {
    resp = parseResponse(c.response as Json, s.swap);
    const ok = RESPONSE_FOR[w.type];
    if (ok && !ok.includes(resp.type)) {
      throw new Error(`respuesta de tipo ${OcgResponseType[resp.type]} para ${msgName(w.type)}; se espera ${ok.map((t) => OcgResponseType[t]).join(" o ")}`);
    }
  }
  s.waiting = null;
  s.decisions++;
  try {
    s.duel.respond(resp);
  } catch (e) {
    // La librería no pudo codificarla (campos que faltan): se sigue esperando lo mismo
    s.waiting = w;
    throw e;
  }
  advance(s, out, t0);
}

const LOCS: [string, number][] = [
  ["hand", OcgLocation.HAND],
  ["mzone", OcgLocation.MZONE],
  ["szone", OcgLocation.SZONE],
  ["grave", OcgLocation.GRAVE],
  ["removed", OcgLocation.REMOVED],
  ["extra", OcgLocation.EXTRA],
];

/** Boca abajo: ni el bit 1 (boca arriba ATK) ni el 4 (boca arriba DEF) */
const isFaceDown = (pos: unknown) => typeof pos === "number" && (pos & (OcgPosition.FACEUP_ATTACK | OcgPosition.FACEUP_DEFENSE)) === 0;

/**
 * Tablero tal como lo ve el jugador `me` (índice del protocolo), para un cerebro externo.
 * Información honesta: del rival solo se ve lo público (mano = nº de cartas, boca abajo = code 0,
 * sin ATK/DEF). Lo propio se ve entero (mano y cartas colocadas).
 */
function viewFor(s: Session, me: number): Json {
  const { handle } = s.duel.raw;
  const flags = OcgQueryFlags.CODE | OcgQueryFlags.POSITION | OcgQueryFlags.ATTACK | OcgQueryFlags.DEFENSE | OcgQueryFlags.LEVEL | OcgQueryFlags.OWNER;
  const players = [0, 1].map((protoP) => {
    const coreP = s.swap ? 1 - protoP : protoP;
    const mine = protoP === me;
    const q = (loc: number) =>
      core.duelQueryLocation(handle, { flags: flags as never, controller: coreP as 0 | 1, location: loc as never }).map((x) => {
        if (!x || !x.code) return null;
        const o = plain(x, false) as Json;
        // owner en índices del protocolo (p. ej. un monstruo robado con Change of Heart: owner = el rival)
        if (typeof o.owner === "number") o.owner = toProto(s, o.owner);
        return o;
      });
    const hide = (cards: (Json | null)[]) =>
      cards.map((x) => (x && !mine && isFaceDown(x.position) ? { code: 0, position: x.position } : x));
    const hand = q(OcgLocation.HAND);
    return {
      deck_count: core.duelQueryCount(handle, coreP, OcgLocation.DECK),
      hand_count: hand.length,
      hand: mine ? hand.map((x) => x?.code ?? 0) : [],
      mzone: hide(q(OcgLocation.MZONE)),
      szone: hide(q(OcgLocation.SZONE)),
      grave: q(OcgLocation.GRAVE).map((x) => x?.code ?? 0),
      removed: q(OcgLocation.REMOVED).map((x) => (x && (mine || !isFaceDown(x.position)) ? x.code : 0)),
      extra: mine ? q(OcgLocation.EXTRA).map((x) => x?.code ?? 0) : [],
      extra_count: core.duelQueryCount(handle, coreP, OcgLocation.EXTRA),
    };
  });
  return { me, players };
}

function cmdQuery(c: Json, out: Out) {
  const s = getSession(c.id);
  const { handle } = s.duel.raw;
  const flags = OcgQueryFlags.CODE | OcgQueryFlags.POSITION | OcgQueryFlags.ATTACK | OcgQueryFlags.DEFENSE | OcgQueryFlags.LEVEL;
  const players = [0, 1].map((coreP) => {
    const o: Json = { deck_count: core.duelQueryCount(handle, coreP, OcgLocation.DECK) };
    for (const [name, loc] of LOCS) {
      const cards = core.duelQueryLocation(handle, { flags: flags as never, controller: coreP as 0 | 1, location: loc as never });
      o[name] = cards.map((x) => (x && x.code ? plain(x, false) : null));
    }
    return o;
  });
  if (s.swap) players.reverse();
  out.emit({
    ev: "state",
    id: s.id,
    turn: s.track.turn,
    turn_player: toProto(s, s.track.turnPlayer),
    phase: s.track.phase,
    lp: lpProto(s),
    waiting: s.waiting ? msgName(s.waiting.type) : null,
    players,
  });
}

function cmdCardInfo(c: Json, out: Out) {
  const codes = (c.codes as number[]) ?? [];
  const cards = codes.map((code) => {
    const d = db.get(Number(code));
    return d ? { ...(plain(d, false) as Json), name: db.name(d.code) } : { code, unknown: true };
  });
  out.emit({ ev: "cardinfo", cards });
}

function handle(line: string) {
  const out = new Out();
  const t0 = Date.now();
  let c: Json = {};
  try {
    c = JSON.parse(line);
    switch (c.cmd) {
      case "new":
        cmdNew(c, out, t0);
        break;
      case "respond":
        cmdRespond(c, out, t0);
        break;
      case "auto":
        cmdRespond(c, out, t0, true);
        break;
      case "surrender": {
        const s = getSession(c.id);
        const loser = Number(c.player ?? 0);
        endSession(s, out, { winner: 1 - loser, reason: "surrender", ms: Date.now() - t0 });
        break;
      }
      case "end": {
        const s = getSession(c.id);
        endSession(s, out, { winner: null, reason: "ended", ms: Date.now() - t0 });
        break;
      }
      case "query":
        cmdQuery(c, out);
        break;
      case "cardinfo":
        cmdCardInfo(c, out);
        break;
      case "list":
        out.emit({ ev: "list", ids: [...sessions.values()].map((s) => s.id) });
        break;
      case "ping":
        out.emit({ ev: "pong", duels: sessions.size });
        break;
      default:
        throw new Error(`comando desconocido: ${JSON.stringify(c.cmd)}`);
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    out.emit({ ev: "error", id: c.id ?? null, cmd: c.cmd ?? null, error: msg });
    // Si el core se rompió a mitad (no un error de formato de la respuesta), el duelo no sigue
    const s = c.id !== undefined ? sessions.get(key(c.id)) : undefined;
    if (s && !s.waiting && (c.cmd === "new" || c.cmd === "respond" || c.cmd === "auto")) {
      endSession(s, out, { winner: null, reason: "error", error: msg });
    }
  }
  if (c.req !== undefined) for (let i = 0; i < out.lines.length; i++) out.lines[i] = out.lines[i].replace(/^\{/, `{"req":${JSON.stringify(c.req)},`);
  out.flush();
}

const startOut = new Out();
startOut.emit({ ev: "ready", cards: db.size, load_ms: loadMs, pid: process.pid, parche_sum: parcheSum, compat_chaininfo: compatChainInfo, scripts_2008: [...scripts2008.keys()].map((f) => Number(f.slice(1, -4))).sort((a, b) => a - b) });
startOut.flush();

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  if (line.trim()) handle(line);
});
rl.on("close", () => {
  for (const s of sessions.values()) {
    try {
      s.duel.destroy();
    } catch {
      /* nada */
    }
  }
  process.exit(0);
});
