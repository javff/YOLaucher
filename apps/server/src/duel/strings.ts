import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Textos del sistema de EDOPro (_vendor/strings.conf, "!system <id> <texto>"): son los que
 * manda ocgcore como "descripción" o pista de selección. Los más habituales van traducidos a
 * mano; el resto sale en inglés. "%ls" = nombre de carta: se deja como {{code}} y el cliente
 * pone el nombre en español.
 */
const ES: Record<number, string> = {
  30: "Se ha repetido el ataque. ¿Seguir atacando?",
  31: "¿Atacar directamente?",
  32: "¿Hacer otra Fase de Batalla?",
  90: "¿Invocar de forma normal sin sacrificios?",
  91: "¿Usar tu Invocación Normal adicional?",
  92: "¿Usar un monstruo del rival?",
  93: "¿Seguir eligiendo materiales?",
  94: "¿Activar ahora el efecto de esta carta?",
  95: "¿Usar el efecto de «%ls»?",
  96: "¿Usar el efecto de «%ls» para evitar la destrucción?",
  97: "¿Colocar «%ls» en la Zona de Magia y Trampa?",
  98: "¿Aplicar un efecto que sustituye el coste?",
  200: "¿Usar el efecto de «%ls» desde [%ls]?",
  203: "¿Activar una carta o efecto?",
  205: "Elige el orden (de arriba abajo)",
  206: "Elige el orden de la cadena",
  210: "¿Seguir eligiendo cartas?",
  221: "¿Activar el efecto de activación de «%ls» desde [%ls]?",
  222: "¿Activar un efecto de activación?",
  500: "Elige qué sacrificar",
  501: "Elige qué descartar",
  502: "Elige qué destruir",
  503: "Elige qué desterrar",
  504: "Elige qué mandar al cementerio",
  505: "Elige qué devolver a la mano",
  506: "Elige qué añadir a tu mano",
  507: "Elige qué devolver al mazo",
  508: "Elige qué invocar de forma normal",
  509: "Elige qué invocar de forma especial",
  510: "Elige qué colocar en el campo",
  511: "Elige los materiales de fusión",
  514: "Elige una carta boca arriba",
  515: "Elige una carta boca abajo",
  516: "Elige un monstruo en posición de ataque",
  517: "Elige un monstruo en posición de defensa",
  518: "Elige a qué equipar",
  520: "Elige el monstruo del que tomar el control",
  522: "Elige un monstruo boca arriba en ataque",
  523: "Elige un monstruo boca arriba en defensa",
  526: "Elige qué cartas mostrar",
  527: "Elige qué colocar en el campo",
  528: "Elige un monstruo para cambiar su posición",
  529: "Elige una carta tuya",
  530: "Elige una carta del rival",
  531: "Elige los sacrificios",
  549: "Elige el objetivo del ataque",
  550: "Elige el efecto que quieres activar",
  551: "Elige el objetivo del efecto",
  552: "Elige cara o cruz",
  555: "Elige una opción",
  556: "Elige el efecto que se aplica",
  560: "Elige",
  561: "Elige la posición de batalla",
  566: "Elige el efecto que activar",
  568: "Elige la carta que se resuelve",
  572: "Elige dónde poner un contador",
  573: "Añadir a tu mano",
  574: "Mandar al cementerio",
};

export class SystemStrings {
  private en = new Map<number, string>();

  constructor(path: string) {
    if (!existsSync(path)) return;
    for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
      const m = /^!system (\d+) (.*)$/.exec(line);
      if (m) this.en.set(Number(m[1]), m[2]);
    }
  }

  /** Texto del sistema con %ls sustituido por {{code}} (o por «esta carta» si no hay carta). */
  get(id: number, code?: number): string | undefined {
    const s = ES[id] ?? this.en.get(id);
    if (!s) return undefined;
    // Solo el primer %ls es una carta; el segundo (si hay) es una zona: se quita
    let first = true;
    return s.replace(/%ls/g, () => {
      if (!first) return "";
      first = false;
      return code ? `{{${code}}}` : "esta carta";
    }).replace(/ desde \[\]| from \[\]/g, "");
  }

  static load(vendor: string) {
    return new SystemStrings(join(vendor, "strings.conf"));
  }
}
