import {
  OcgLocation,
  OcgMessageType,
  OcgQueryFlags,
  type OcgCardQueryInfo,
  type OcgMessage,
} from "ocgcore-wasm";
import type { CardView, DuelEvent, DuelPrompt, DuelSnapshot, Place, PlayerView, PromptCard } from "@duelcards/shared";
import type { CardDB } from "./data";
import type { Duel, SelectMessage } from "./engine";

const FACEDOWN = 0x2 | 0x8;
const FLAGS = OcgQueryFlags.CODE | OcgQueryFlags.POSITION | OcgQueryFlags.ATTACK | OcgQueryFlags.DEFENSE;

/** Estado que el servidor sigue aparte porque ocgcore no lo expone en consultas. */
export interface DuelTrack {
  lp: [number, number];
  turn: number;
  turnPlayer: number;
  phase: number;
}

export function applyToTrack(t: DuelTrack, messages: OcgMessage[]) {
  for (const m of messages) {
    switch (m.type) {
      case OcgMessageType.NEW_TURN:
        t.turn++;
        t.turnPlayer = m.player;
        break;
      case OcgMessageType.NEW_PHASE:
        t.phase = m.phase;
        break;
      case OcgMessageType.DAMAGE:
      case OcgMessageType.PAY_LPCOST:
        t.lp[m.player] = Math.max(0, t.lp[m.player] - m.amount);
        break;
      case OcgMessageType.RECOVER:
        t.lp[m.player] += m.amount;
        break;
      case OcgMessageType.LPUPDATE:
        t.lp[m.player] = m.lp;
        break;
    }
  }
}

/**
 * El tablero tal y como lo ve `viewer`. Nunca salen del servidor la mano del rival ni sus
 * cartas boca abajo: el cliente recibe code = 0 (si no, cualquiera podría leerlas en la red).
 */
export function snapshot(duel: Duel, t: DuelTrack, viewer: number, names: [string, string], time: [number, number]): DuelSnapshot {
  const { core, handle } = duel.raw;
  const loc = (p: 0 | 1, location: number) =>
    // Las flags se combinan con OR aunque el tipo de la librería solo admita una
    core.duelQueryLocation(handle, { flags: FLAGS as never, controller: p, location: location as never });
  const view = (c: Partial<OcgCardQueryInfo> | null, hidden: boolean): CardView | null => {
    if (!c || !c.code) return null;
    const pos = Number(c.position ?? 0);
    return { code: hidden ? 0 : c.code, position: pos, attack: hidden ? undefined : c.attack, defense: hidden ? undefined : c.defense };
  };
  const players = ([0, 1] as const).map((p): PlayerView => {
    const mine = p === viewer;
    const field = (c: Partial<OcgCardQueryInfo> | null) => view(c, !mine && (Number(c?.position ?? 0) & FACEDOWN) !== 0);
    return {
      lp: t.lp[p],
      deck: core.duelQueryCount(handle, p, OcgLocation.DECK),
      extra: core.duelQueryCount(handle, p, OcgLocation.EXTRA),
      hand: loc(p, OcgLocation.HAND).map((c) => view(c, !mine)!).filter(Boolean),
      monsters: padded(loc(p, OcgLocation.MZONE).map(field), 7),
      spells: padded(loc(p, OcgLocation.SZONE).map(field), 8),
      grave: loc(p, OcgLocation.GRAVE).map((c) => view(c, false)!).filter(Boolean),
      banished: loc(p, OcgLocation.REMOVED).map((c) => view(c, !mine && (Number(c?.position ?? 0) & FACEDOWN) !== 0)!).filter(Boolean),
    };
  }) as [PlayerView, PlayerView];
  return { you: viewer, names, turn: t.turn, turnPlayer: t.turnPlayer, phase: t.phase, players, time };
}

function padded<T>(xs: (T | null)[], n: number): (T | null)[] {
  return Array.from({ length: n }, (_, i) => xs[i] ?? null);
}

const place = (p: { controller: number; location: number; sequence: number; position?: number }): Place => ({
  controller: p.controller,
  location: p.location,
  sequence: p.sequence,
  position: Number(p.position ?? 0),
});

/**
 * Traduce los mensajes del core a eventos con toda la información (códigos incluidos). Antes de
 * mandarlos hay que pasarlos por `eventsFor`, que esconde lo que cada jugador no puede ver.
 * `chain` = pila de cartas activadas (para saber cuál se niega); se mantiene entre llamadas.
 */
export function buildEvents(messages: OcgMessage[], t: DuelTrack, chain: Place[]): DuelEvent[] {
  const out: DuelEvent[] = [];
  // Cartas destruidas en el último combate: su MOVE al cementerio es «battle»
  let battleDead: Place[] = [];
  for (const m of messages) {
    switch (m.type) {
      case OcgMessageType.NEW_TURN:
        out.push({ k: "turn", player: m.player, turn: t.turn });
        break;
      case OcgMessageType.NEW_PHASE:
        out.push({ k: "phase", phase: m.phase });
        break;
      case OcgMessageType.DRAW:
        out.push({ k: "draw", player: m.player, count: m.drawn.length, codes: m.drawn.map((d) => d.code) });
        break;
      case OcgMessageType.SUMMONING:
      case OcgMessageType.SPSUMMONING:
      case OcgMessageType.FLIPSUMMONING: {
        const how = m.type === OcgMessageType.SUMMONING ? "normal" : m.type === OcgMessageType.SPSUMMONING ? "special" : "flip";
        // Lo que acaba de ir del campo de quien invoca al cementerio sin combate = sacrificio
        if (how === "normal") {
          for (let i = out.length - 1; i >= 0; i--) {
            const e = out[i];
            if (e.k !== "move") break;
            if (e.from.controller === m.controller && e.from.location === OcgLocation.MZONE && e.to.location === OcgLocation.GRAVE && !e.why) e.why = "tribute";
          }
        }
        out.push({ k: "summon", player: m.controller, code: m.code, how, place: place(m) });
        break;
      }
      case OcgMessageType.SET:
        out.push({ k: "set", player: m.controller, place: place(m) });
        break;
      case OcgMessageType.CHAINING: {
        const p = place(m);
        chain[m.chain_size - 1] = p;
        out.push({ k: "chain", player: m.controller, code: m.code, place: p });
        break;
      }
      case OcgMessageType.CHAIN_NEGATED:
      case OcgMessageType.CHAIN_DISABLED:
        out.push({ k: "negated", place: chain[m.chain_size - 1] ?? null });
        break;
      case OcgMessageType.CHAIN_END:
        chain.length = 0;
        break;
      case OcgMessageType.DAMAGE:
        out.push({ k: "damage", player: m.player, amount: m.amount });
        break;
      case OcgMessageType.RECOVER:
        out.push({ k: "recover", player: m.player, amount: m.amount });
        break;
      case OcgMessageType.ATTACK:
        out.push({ k: "attack", player: m.card.controller, from: place(m.card), to: m.target ? place(m.target) : null });
        break;
      case OcgMessageType.BATTLE:
        battleDead = [m.card, m.target].filter((c): c is NonNullable<typeof c> => !!c && c.destroyed).map(place);
        break;
      case OcgMessageType.MOVE: {
        const from = place(m.from);
        const to = place(m.to);
        const battle = battleDead.some((d) => d.controller === from.controller && d.location === from.location && d.sequence === from.sequence);
        out.push({ k: "move", code: m.card, from, to, ...(battle && to.location === OcgLocation.GRAVE ? { why: "battle" as const } : {}) });
        break;
      }
      case OcgMessageType.POS_CHANGE:
        out.push({ k: "pos", code: m.code, place: place({ ...m, position: m.position }), prev: m.prev_position });
        break;
      case OcgMessageType.SHUFFLE_DECK:
        out.push({ k: "shuffle", player: m.player });
        break;
      case OcgMessageType.WIN:
        out.push({ k: "win", player: m.player });
        break;
    }
  }
  return out;
}

/** ¿Ve `viewer` el anverso de una carta en este sitio? */
function visibleAt(p: Place, viewer: number): boolean {
  if (p.location === OcgLocation.GRAVE) return true;
  if (p.location === OcgLocation.DECK || p.location === OcgLocation.EXTRA) return false;
  if (p.location === OcgLocation.HAND) return p.controller === viewer;
  // Campo o desterradas: boca arriba la ven los dos; boca abajo, solo su dueño
  return (p.position & FACEDOWN) === 0 || p.controller === viewer;
}

/** Los eventos tal y como los puede ver `viewer`: código 0 si no puede saber qué carta es. */
export function eventsFor(events: DuelEvent[], viewer: number): DuelEvent[] {
  return events.map((e) => {
    if (e.k === "move") {
      const known = visibleAt(e.from, viewer) || visibleAt(e.to, viewer);
      return known ? e : { ...e, code: 0 };
    }
    if (e.k === "pos") return visibleAt(e.place, viewer) ? e : { ...e, code: 0 };
    if (e.k === "draw") return e.player === viewer ? e : { ...e, codes: [] };
    return e;
  });
}

/**
 * Convierte el mensaje de selección del core en una pregunta para el cliente.
 * null = esta decisión no merece preguntar (no hay nada que elegir, o es un detalle como en qué
 * zona exacta colocar): DuelRoom la contesta solo, como hace el bot.
 */
export function toPrompt(m: SelectMessage, db: CardDB, hide: (c: PromptCard) => boolean, hint?: number): DuelPrompt | null {
  // Pista que el core manda justo antes de la pregunta ("Elige el objetivo del ataque"...)
  const hintText = (fallback: string) => (hint ? db.describe(hint) : fallback);
  const card = (c: { code: number; controller: number; location: number; sequence: number; position?: number }, text?: string): PromptCard => {
    const pc: PromptCard = { code: c.code, controller: c.controller, location: c.location, sequence: c.sequence, position: c.position, text };
    if (hide(pc)) pc.code = 0;
    return pc;
  };
  switch (m.type) {
    case OcgMessageType.SELECT_IDLECMD:
      return {
        kind: "idle",
        summons: m.summons.map((c) => card(c)),
        specialSummons: m.special_summons.map((c) => card(c)),
        reposition: m.pos_changes.map((c) => card(c)),
        monsterSets: m.monster_sets.map((c) => card(c)),
        spellSets: m.spell_sets.map((c) => card(c)),
        activates: m.activates.map((c) => card(c, db.describe(c.description))),
        toBattle: m.to_bp,
        toEnd: m.to_ep,
      };
    case OcgMessageType.SELECT_BATTLECMD:
      return {
        kind: "battle",
        activates: m.chains.map((c) => card(c, db.describe(c.description))),
        attacks: m.attacks.map((c) => card(c)),
        toMain2: m.to_m2,
        toEnd: m.to_ep,
      };
    case OcgMessageType.SELECT_EFFECTYN:
      return { kind: "yesno", card: card(m), text: db.describe(m.description, m.code), effect: true };
    case OcgMessageType.SELECT_YESNO:
      return { kind: "yesno", text: db.describe(m.description), effect: false };
    case OcgMessageType.SELECT_OPTION:
      return { kind: "option", options: m.options.map((o) => db.describe(o)) };
    case OcgMessageType.SELECT_CARD:
      return { kind: "cards", min: m.min, max: m.max, cancel: m.can_cancel, cards: m.selects.map((c) => card(c)), text: hintText("Elige cartas"), tribute: false };
    case OcgMessageType.SELECT_TRIBUTE:
      return { kind: "cards", min: m.min, max: m.max, cancel: m.can_cancel, cards: m.selects.map((c) => card(c)), text: hintText("Elige qué sacrificar"), tribute: true };
    case OcgMessageType.SELECT_UNSELECT_CARD:
      return {
        kind: "unselect",
        min: m.min,
        max: m.max,
        finish: m.can_finish,
        cancel: m.can_cancel,
        select: m.select_cards.map((c) => card(c)),
        unselect: m.unselect_cards.map((c) => card(c)),
      };
    case OcgMessageType.SELECT_CHAIN:
      // Sin nada que encadenar no se pregunta (son la mayoría: cientos por duelo)
      if (!m.selects.length) return null;
      return { kind: "chain", cards: m.selects.map((c) => card(c, db.describe(c.description))), forced: m.forced };
    case OcgMessageType.SELECT_POSITION:
      return { kind: "position", code: m.code, positions: m.positions };
    case OcgMessageType.SELECT_SUM:
      return {
        kind: "sum",
        amount: m.amount,
        must: m.selects_must.map((c) => card(c)),
        cards: m.selects.map((c) => ({ ...card(c), amount: c.amount })),
      };
    default:
      return null;
  }
}
