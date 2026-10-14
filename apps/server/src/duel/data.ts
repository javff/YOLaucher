import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import type { OcgCardData } from "ocgcore-wasm";
import { SystemStrings } from "./strings";

/**
 * Datos de ProjectIgnis (AGPL-3.0) que se bajan aparte a DuelCards/_vendor:
 *   cards.cdb    BabelCDB (sqlite): estadísticas y textos en inglés de cada carta
 *   CardScripts/ un .lua por carta + los comunes (constant.lua, utility.lua, proc_*.lua)
 */
const VENDOR = process.env.VENDOR_DIR ?? resolve(dirname(fileURLToPath(import.meta.url)), "../../../../_vendor");

const TYPE_LINK = 0x4000000;



export class CardDB {
  private cards = new Map<number, OcgCardData>();
  /** Nombre en inglés y las 16 cadenas de efecto (str1..str16) de cada carta */
  private texts = new Map<number, { name: string; strings: string[] }>();
  private system = SystemStrings.load(VENDOR);

  constructor(path = join(VENDOR, "cards.cdb")) {
    const db = new DatabaseSync(path, { readOnly: true });
    const cols = Array.from({ length: 16 }, (_, i) => `str${i + 1}`).join(", ");
    for (const r of db.prepare(`select id, name, ${cols} from texts`).all() as Record<string, string | number>[]) {
      this.texts.set(Number(r.id), {
        name: String(r.name),
        strings: Array.from({ length: 16 }, (_, i) => String(r[`str${i + 1}`] ?? "")),
      });
    }
    const stmt = db.prepare("select id, alias, setcode, type, atk, def, level, race, attribute from datas");
    // setcode y race son de 64 bits: leídos como number perderían precisión
    stmt.setReadBigInts(true);
    for (const r of stmt.all() as Record<string, bigint>[]) {
      const type = Number(r.type);
      const level = Number(r.level);
      const setcodes: number[] = [];
      for (let s = BigInt(r.setcode); s > 0n; s >>= 16n) {
        const v = Number(s & 0xffffn);
        if (v) setcodes.push(v);
      }
      const isLink = (type & TYPE_LINK) !== 0;
      this.cards.set(Number(r.id), {
        code: Number(r.id),
        alias: Number(r.alias),
        setcodes,
        type,
        // El campo level empaqueta nivel (byte bajo) y escalas de péndulo (bytes altos)
        level: level & 0xff,
        attribute: Number(r.attribute),
        race: BigInt(r.race),
        attack: Number(r.atk),
        // En los monstruos de enlace "def" guarda las flechas
        defense: isLink ? 0 : Number(r.def),
        lscale: (level >> 24) & 0xff,
        rscale: (level >> 16) & 0xff,
        link_marker: isLink ? Number(r.def) : 0,
      });
    }
    db.close();
  }

  get(code: number): OcgCardData | null {
    return this.cards.get(code) ?? null;
  }

  name(code: number): string {
    return this.texts.get(code)?.name ?? `#${code}`;
  }

  /**
   * Texto de una "descripción" de ocgcore. En EDOPro, aux.Stringid(code, i) = code << 20 | i:
   * la cadena i de esa carta. Las descripciones pequeñas son textos del sistema (strings.conf).
   */
  describe(desc: bigint | number, card?: number): string {
    const d = BigInt(desc);
    const code = Number(d >> 20n);
    const idx = Number(d & 0xfffffn);
    if (code > 0) return this.texts.get(code)?.strings[idx] || `{{${code}}} (${idx + 1})`;
    return this.system.get(idx, card) ?? `Opción ${idx}`;
  }

  get size() {
    return this.cards.size;
  }
}

/** Busca scripts por nombre de fichero en todo CardScripts (official/, goat/, raíz...). */
export class ScriptStore {
  private paths = new Map<string, string>();
  private cache = new Map<string, string>();

  constructor(root = join(VENDOR, "CardScripts")) {
    if (!existsSync(root)) throw new Error(`no está CardScripts en ${root} (ver README: _vendor)`);
    const walk = (d: string) => {
      for (const f of readdirSync(d)) {
        const p = join(d, f);
        if (statSync(p).isDirectory()) walk(p);
        // official/ manda sobre el resto si un nombre se repite (p. ej. pre-errata)
        else if (f.endsWith(".lua") && (!this.paths.has(f) || p.includes("official"))) this.paths.set(f, p);
      }
    };
    walk(root);
  }

  read(name: string): string | null {
    const file = name.split(/[\\/]/).pop()!;
    const hit = this.cache.get(file);
    if (hit !== undefined) return hit;
    const p = this.paths.get(file);
    if (!p) return null;
    const src = readFileSync(p, "utf8");
    this.cache.set(file, src);
    return src;
  }
}
