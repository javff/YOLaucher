"""Archivo de repeticiones del SERVIDOR (07-10, OK del owner; NUESTRO: el oficial no guardaba nada, el registro lo
escribía el cliente en su PC). Contrato con el panel (research/admin/sec_duelos_equipos.py, cabecera; no cambiarlo):

- <datos>/repeticiones/AAAA-MM/<pvp|npc>_<AAAAMMDD-HHMMSS>_<n>.log, con <datos> = dirname(STATE_PATH) (en el vivo,
  tools/yo2emu) o YO2_REPETICIONES_DIR si está puesta (la misma variable que lee el panel, para pruebas). <n> = el primer
  número libre para esa fecha (1, 2, …): dos duelos que acaban en el mismo segundo no chocan.
- Contenido: el .log EXACTO que escribe el cliente (cliente_sim.Client = FUN_00694840 reescrito: u16 0x80a, u16 0xd8,
  u32 0x28, 32 B a 0, u32 tamaño decodificado, RLE del blob). PvP: los mismos mensajes 0x6010/0x6011/0x6016/0x6012 que
  se mandan a los clientes (duellog.messages). NPC: el mismo formato con el humano como jugador 0 y la NPC como 1, y
  solo los 0x6202 del lado 0 (los que recibió el humano; la NPC no tiene cliente).
- <datos>/repeticiones/indice.jsonl: una línea JSON por duelo, SOLO se añade (candado del proceso + candado del
  fichero; una sola escritura por línea). Campos: id (= fichero), tipo, fecha, fichero, jugadores [{nombre, pid,
  acct}], npc {id, nombre} (solo NPC; null en PvP), ganador (0/1 = índice en jugadores, -1 empate, null sin
  resultado), motivo (normal, rendición, desconexión, tiempo, empate, error), causa (WHY_* de duelfin: lp, deckout,
  exodia, surrender…), turnos, bytes, comandos y duelo (nombre interno: pvp-3, npc-ocg-7).
- Flag: pvp_match.REPLAY_ARCHIVE (encendido; YO2_REPLAY_ARCHIVE=0 lo apaga). El panel lo lee de ahí.
- Nunca rompe el fin del duelo: todo va en un hilo aparte y cualquier fallo solo va al log.
"""
import datetime
import json
import os
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
INDICE = "indice.jsonl"
CARPETA = "repeticiones"
EMPTY = 0x2C                   # cabecera 0x28 + u32: un .log de 44 B no lleva nada
MAX_N = 9999                   # <n> del nombre (el panel acepta 1-4 cifras)
LOCK = threading.Lock()        # índice y nombres dentro de este proceso
LOCK_AT = 1 << 30              # byte del candado del fichero en Windows (más allá del final: no estorba al que lee)
STATS = {"guardadas": 0, "fallos": 0, "saltadas": 0, "ultima": None}

# WHY_* de research/duelfin -> motivo del índice (el panel lo enseña tal cual)
_MOTIVO = {"surrender": "rendición", "disconnect": "desconexión", "timeout": "tiempo", "draw": "empate",
           "abort": "error", "error": "error"}


def activo():
    """REPLAY_ARCHIVE de pvp_match si está cargado; si no, la variable YO2_REPLAY_ARCHIVE (encendido por defecto)."""
    if os.environ.get("YO2_REPLAY_ARCHIVE", "1") in ("", "0"):
        return False
    m = sys.modules.get("pvp_match")
    if m is not None and hasattr(m, "REPLAY_ARCHIVE"):
        return bool(m.REPLAY_ARCHIVE)
    return True


def raiz(state_path):
    """<datos>/repeticiones (datos = carpeta de STATE_PATH) o YO2_REPETICIONES_DIR. None sin STATE_PATH."""
    forced = os.environ.get("YO2_REPETICIONES_DIR")
    if forced:
        return os.path.abspath(forced)
    if not state_path:
        return None
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), CARPETA)


def _mods():
    """duellog y cliente_sim (esta misma carpeta; los mismos módulos que usa pvp_match._DL)."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import cliente_sim
    import duellog
    return duellog, cliente_sim


def motivo(why, winner):
    """(motivo, causa) para el índice. why = WHY_* de duelfin (o el motivo de ocgcore ya traducido)."""
    w = str(why or "")
    if w in _MOTIVO:
        return _MOTIVO[w], w
    if winner is None:
        return "empate", w or "draw"
    return "normal", w or None


def ganador(winner, why):
    """0/1 = índice en jugadores, -1 empate, None sin resultado (motor caído / anulado)."""
    if str(why or "") in ("abort", "error"):
        return None
    return -1 if winner is None else int(winner)


def log_de_mensajes(msgs):
    """Los bytes del <título>.log que escribe el cliente al recibir estos mensajes (cliente_sim = FUN_00694840)."""
    _dl, cs = _mods()
    c = cs.Client()
    for mid, data in msgs:
        c.on(mid, data)
    logs = [k for k in c.files if k.endswith(".log")]
    if len(logs) != 1:
        raise ValueError(f"el cliente simulado no ha escrito un .log ({sorted(c.files)})")
    return c.files[logs[0]]


def mensajes_npc(human, npc, entries, when, first, hands, room_kind):
    """Mensajes de registro de un duelo contra NPC: el humano es el jugador 0 (su pid, nombre, mazo, piezas) y la NPC
    el 1 (su id, nombre, mazo); entries = los 0x6202 que recibió el humano (lado 0). first = quién empezó (0 el humano,
    1 la NPC); hands = (mano del humano, mano de la NPC) que decidieron, o None (replay_start rellena: NOTES §7)."""
    DL, _cs = _mods()
    h, f, avisos = DL.replay_start(hands, first)
    players = [dict(human, hand=h[0]), dict(npc, hand=h[1])]
    msgs = DL.messages(players, [(0, bytes(e)) for e in entries], title=time.strftime("npc%Y%m%d%H%M%S",
                       time.localtime(when)), when=when, games=(f, 0xFF, 0xFF), room_kind=room_kind)
    return msgs, h, f, avisos


def _append(path, line):
    """Una línea al final del índice, de una sola escritura, con candado del fichero (msvcrt/fcntl) además del del
    proceso: el panel y el importador de otro proceso también añaden."""
    data = (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        locked = None
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, LOCK_AT, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)          # un byte lejos del final: nadie lee ahí
                locked = ("nt", msvcrt)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
                locked = ("posix", fcntl)
        except OSError:
            locked = None                                     # sin candado del fichero: sigue el del proceso
        try:
            os.write(fd, data)                                # O_APPEND: siempre al final
        finally:
            if locked and locked[0] == "nt":
                os.lseek(fd, LOCK_AT, os.SEEK_SET)
                locked[1].locking(fd, locked[1].LK_UNLCK, 1)
            elif locked:
                locked[1].flock(fd, locked[1].LOCK_UN)
    finally:
        os.close(fd)


def guardar(root, tipo, when, data, jugadores, npc=None, ganador_=None, motivo_=None, causa=None, turnos=0,
            comandos=None, duelo=None):
    """Escribe el .log (fichero temporal + enlace duro con el primer <n> libre: nunca pisa) y añade su línea al
    índice. Devuelve la línea."""
    if tipo not in ("pvp", "npc"):
        raise ValueError(f"tipo {tipo!r}")
    if not data or len(data) <= EMPTY:
        raise ValueError("registro vacío")
    dt = datetime.datetime.fromtimestamp(int(when))
    mes = f"{dt:%Y-%m}"
    folder = os.path.join(root, mes)
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, f".tmp-{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}")
    with open(tmp, "xb") as f:
        f.write(data)
    try:
        with LOCK:
            name = None
            for n in range(1, MAX_N + 1):
                cand = f"{tipo}_{dt:%Y%m%d-%H%M%S}_{n}.log"
                try:
                    os.link(tmp, os.path.join(folder, cand))      # falla si existe: nunca pisa
                except FileExistsError:
                    continue
                name = cand
                break
            if name is None:
                raise RuntimeError(f"más de {MAX_N} repeticiones en el mismo segundo")
            rid = f"{mes}/{name}"
            line = {"id": rid, "tipo": tipo, "fecha": f"{dt:%Y-%m-%d %H:%M:%S}", "fichero": rid,
                    "jugadores": jugadores, "npc": npc, "ganador": ganador_, "motivo": motivo_, "causa": causa,
                    "turnos": int(turnos or 0), "bytes": len(data), "comandos": comandos, "duelo": duelo}
            _append(os.path.join(root, INDICE), line)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    STATS["guardadas"] += 1
    STATS["ultima"] = line["id"]
    return line


def archivar(root, tipo, when, build, jugadores, npc, winner, why, turnos, log, duelo="", sync=False):
    """build() -> (mensajes de registro, comandos, texto extra para el log) se llama en un hilo aparte (RLE y disco
    fuera del hilo del duelo). sync=True: en este hilo (pruebas). Nunca lanza; devuelve el hilo (o la línea si sync)."""
    def work():
        try:
            if not root:
                STATS["saltadas"] += 1
                return None
            msgs, ncmd, extra = build()
            if not ncmd:
                STATS["saltadas"] += 1
                log(f"[repeticiones] {duelo}: nada grabado, no se archiva")
                return None
            data = log_de_mensajes(msgs)
            mot, causa = motivo(why, winner)
            line = guardar(root, tipo, when, data, jugadores, npc, ganador(winner, why), mot, causa, turnos,
                           comandos=ncmd, duelo=duelo)
            log(f"[repeticiones] {duelo}: archivada {line['id']} ({line['bytes']} B, {ncmd} comandos, {mot}"
                + (f", gana {line['ganador']}" if line["ganador"] in (0, 1) else "") + ")"
                + (f"; {extra}" if extra else ""))
            return line
        except Exception as e:                            # noqa: BLE001  (nunca rompe el fin del duelo)
            STATS["fallos"] += 1
            try:
                log(f"[EXC] [repeticiones] {duelo}: no se ha archivado: {e!r} | "
                    + traceback.format_exc().replace(chr(10), " | "))
            except Exception:                             # noqa: BLE001
                pass
            return None
    if sync:
        return work()
    t = threading.Thread(target=work, daemon=True, name=f"archivo-{duelo or tipo}")
    t.start()
    return t
