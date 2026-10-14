import { randomBytes, randomInt } from "node:crypto";
import createCore, {
  OcgDuelMode,
  OcgLocation,
  OcgLogType,
  OcgMessageType,
  OcgPosition,
  OcgProcessResult,
  type OcgCoreSync,
  type OcgDuelHandle,
  type OcgMessage,
  type OcgResponse,
} from "ocgcore-wasm";
import { CardDB, ScriptStore } from "./data";

let shared: Promise<{ core: OcgCoreSync; db: CardDB; scripts: ScriptStore }> | undefined;

/** El core WASM, la base de datos y los scripts se cargan una vez y los comparten todos los duelos. */
export function loadEngine() {
  shared ??= (async () => {
    const core = await createCore({ sync: true });
    return { core, db: new CardDB(), scripts: new ScriptStore() };
  })();
  return shared;
}

/** Mensajes que piden una decisión a un jugador: el core se para hasta recibir la respuesta. */
export const SELECT_TYPES = new Set<number>([
  OcgMessageType.SELECT_BATTLECMD,
  OcgMessageType.SELECT_IDLECMD,
  OcgMessageType.SELECT_EFFECTYN,
  OcgMessageType.SELECT_YESNO,
  OcgMessageType.SELECT_OPTION,
  OcgMessageType.SELECT_CARD,
  OcgMessageType.SELECT_CHAIN,
  OcgMessageType.SELECT_PLACE,
  OcgMessageType.SELECT_POSITION,
  OcgMessageType.SELECT_TRIBUTE,
  OcgMessageType.SORT_CHAIN,
  OcgMessageType.SELECT_COUNTER,
  OcgMessageType.SELECT_SUM,
  OcgMessageType.SELECT_DISFIELD,
  OcgMessageType.SORT_CARD,
  OcgMessageType.SELECT_UNSELECT_CARD,
  OcgMessageType.ROCK_PAPER_SCISSORS,
  OcgMessageType.ANNOUNCE_RACE,
  OcgMessageType.ANNOUNCE_ATTRIB,
  OcgMessageType.ANNOUNCE_CARD,
  OcgMessageType.ANNOUNCE_NUMBER,
]);

export type SelectMessage = OcgMessage & { player: number };

export interface StepResult {
  messages: OcgMessage[];
  /** Si el duelo espera una decisión: el mensaje que la pide (con su jugador) */
  waiting: SelectMessage | null;
  ended: boolean;
  /** Ganador (0/1) o 2 = empate, si ha terminado */
  winner?: number;
}

/**
 * Un duelo de ocgcore (reglas oficiales, Master Rule 5). Solo sabe de cartas y respuestas:
 * quién es humano o bot y qué ve cada uno lo decide DuelRoom.
 */
export class Duel {
  private handle: OcgDuelHandle;
  private last: SelectMessage | null = null;
  readonly errors: string[] = [];

  private constructor(
    private core: OcgCoreSync,
    handle: OcgDuelHandle,
  ) {
    this.handle = handle;
  }

  static async create(decks: [number[], number[]], opts: { startingLP?: number } = {}): Promise<Duel> {
    const { core, db, scripts } = await loadEngine();
    const team = { startingLP: opts.startingLP ?? 8000, startingDrawCount: 5, drawCountPerTurn: 1 };
    const errors: string[] = [];
    const handle = core.createDuel({
      flags: OcgDuelMode.MODE_MR5,
      seed: seed(),
      team1: team,
      team2: team,
      cardReader: (code) => db.get(code),
      scriptReader: (name) => scripts.read(name),
      errorHandler: (type, text) => {
        // Los monstruos normales no tienen script: el core lo intenta igual y avisa. No es un error.
        if (type === OcgLogType.ERROR && /c\d+\.lua/.test(text) && /not found|cannot open/i.test(text)) return;
        errors.push(`[${type}] ${text}`);
      },
    });
    if (!handle) throw new Error("ocgcore no pudo crear el duelo");
    const duel = new Duel(core, handle);
    (duel as { errors: string[] }).errors = errors;

    // Los scripts comunes van antes que cualquier carta (como hacen EDOPro y compañía)
    for (const s of ["constant.lua", "utility.lua"]) {
      const src = scripts.read(s);
      if (!src || !core.loadScript(handle, s, src)) throw new Error(`no se pudo cargar ${s}`);
    }
    // El core NO baraja: los mazos entran en el orden dado (como en EDOPro, baraja el servidor).
    // Sin esto, la mano inicial era siempre la misma
    decks.forEach((deck, team) => {
      for (const code of shuffled(deck)) {
        core.duelNewCard(handle, {
          team: team as 0 | 1,
          duelist: 0,
          code,
          controller: team as 0 | 1,
          location: OcgLocation.DECK,
          sequence: 0,
          position: OcgPosition.FACEDOWN_DEFENSE,
        });
      }
    });
    core.startDuel(handle);
    return duel;
  }

  /** Avanza el duelo hasta que alguien tenga que decidir algo o se acabe. */
  step(): StepResult {
    const messages: OcgMessage[] = [];
    for (let guard = 0; guard < 10_000; guard++) {
      const status = this.core.duelProcess(this.handle);
      const batch = this.core.duelGetMessage(this.handle);
      messages.push(...batch);
      // El core NO se para solo al decidir el duelo: emite WIN y sigue. Cortar aquí, como EDOPro
      const win = batch.find((m) => m.type === OcgMessageType.WIN);
      if (win) {
        this.last = null;
        const cut = messages.indexOf(win) + 1;
        return { messages: messages.slice(0, cut), waiting: null, ended: true, winner: win.player };
      }
      if (status === OcgProcessResult.END) {
        this.last = null;
        return { messages, waiting: null, ended: true };
      }
      if (status === OcgProcessResult.WAITING) {
        const sel = [...batch].reverse().find((m) => SELECT_TYPES.has(m.type)) as SelectMessage | undefined;
        // RETRY = la respuesta anterior no era válida: se vuelve a pedir lo mismo
        if (batch.some((m) => m.type === OcgMessageType.RETRY) && this.last) {
          return { messages, waiting: this.last, ended: false };
        }
        this.last = sel ?? this.last;
        return { messages, waiting: this.last, ended: false };
      }
    }
    throw new Error("el duelo no avanza (bucle de proceso sin fin)");
  }

  respond(response: OcgResponse) {
    this.core.duelSetResponse(this.handle, response);
  }

  get raw() {
    return { core: this.core, handle: this.handle };
  }

  destroy() {
    this.core.destroyDuel(this.handle);
  }
}

/** Fisher-Yates con números criptográficos (Math.random no vale para barajar en un juego online). */
function shuffled<T>(xs: T[]): T[] {
  const out = [...xs];
  for (let i = out.length - 1; i > 0; i--) {
    const j = randomInt(i + 1);
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

function seed(): [bigint, bigint, bigint, bigint] {
  const b = randomBytes(32);
  const s = [0, 1, 2, 3].map((i) => b.readBigUInt64LE(i * 8)) as [bigint, bigint, bigint, bigint];
  // Xoshiro256** no admite la semilla todo a ceros
  if (s.every((x) => x === 0n)) s[0] = 1n;
  return s;
}
