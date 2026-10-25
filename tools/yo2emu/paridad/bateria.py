"""Batería de pruebas de la reestructura (REESTRUCTURA.md §3.2): la misma antes y después de cada paso.

    python paridad/bateria.py --banda 1900 --etiqueta base       # línea base -> paridad/_out/base.json
    python paridad/bateria.py --banda 1000 --etiqueta E1a        # un paso -> _out/E1a.json + tabla IGUAL/DISTINTO
    python paridad/bateria.py --banda 1300 --etiqueta x --solo T1,T5 --sin-paridad
    python paridad/bateria.py --listar

Bandas (§3.1): A = 1000, B = 1300, C = 1600, integrador = 1900; cada prueba con puertos usa banda + 10·k (k < 20) y
las que piden un offset >= 5000 (test_sin_sesion) usan 5000 + banda + 10·k. La paridad (T9) usa SIEMPRE 2200 con
cerrojo (grabar.py). Nunca offset 0 ni 3000; nada contra el servidor vivo.
Cada prueba corre con TEMP/TMP propios (_out/<etiqueta>/tmp/<id>), YO2_PRUEBA_DIR propio (logs/prueba/bat-<id>) y su
salida en _out/<etiqueta>/salidas/<id>.txt.
«Igual» = mismo código de salida, mismo nº de OK y de fallos y mismos nombres en fallo que la base (los fallos que ya
tenía la base son «fallos conocidos» y deben seguir siendo exactamente esos).
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun  # noqa: E402

T2_ENV = {"YO2_LIMIT_LIST": "1", "YO2_WATCH": "1", "YO2_ROOM_REOPEN": "1", "YO2_LOGIN_ESTRICTO": "0"}
FC_MODOS = ["prueba", "pvp", "npc", "kill", "desconexion", "dpcoste", "dpcero", "pvpcarta", "pvpcarta0"]

# id, grupo, argv (relativo a tools/yo2emu), opciones: puertos (añade --offset), min_offset, env, tiempo (s)
PRUEBAS = [
    ("T1", "T1", ["research/traductor/test_translator.py", "30"], {"tiempo": 1800}),
    # 24-10 (rx/OPT, NOTES §40): SELECT_OPTION con aux.Stringid = código << 20 | n y SELECT_CARD en la mano del rival
    ("T1.opciones_mano_rival", "T1", ["research/traductor/test_opciones_mano_rival.py"], {"tiempo": 600}),
    # 24-10 (rx/OPT2, NOTES §41): SÍ/NO de una carta (código << 20 | n), Rain Storm / Yubel / Yu-Jo, anuncios con ocgcore real,
    # SORT_CARD (la librería escribía un byte de más) y la mano del rival con 0x52
    ("T1.decisiones_opt2", "T1", ["research/traductor/test_decisiones_opt2.py"], {"tiempo": 900}),
] + [
    (f"T2.{m}", "T2", ["fake_client.py", m], {"puertos": True, "env": T2_ENV, "tiempo": 900}) for m in FC_MODOS
] + [
    # T8.repeticion va ANTES que T3: reescribe logs/repeticion/pvptraza.log (desde la traza *duelopvp* congelada) y
    # test_sec_visor lee ese fichero; así sec_visor ve siempre el mismo, pase lo que pase en pasadas anteriores
    ("T8.repeticion", "T8", ["research/repeticion/test_repeticion.py"], {}),
    ("T3.panel", "T3", ["research/admin/test_panel.py"], {"puertos": True}),
    ("T3.sec_duelos_equipos", "T3", ["research/admin/test_sec_duelos_equipos.py", "--sin-capturas"], {"puertos": True}),
    ("T3.sec_duelpass", "T3", ["research/admin/test_sec_duelpass.py"], {"puertos": True}),
    ("T3.sec_economia", "T3", ["research/admin/test_sec_economia.py"], {"puertos": True}),
    # 10-10: ROOM_LANG_MERGE (master 526d16ac, por defecto) junta las salas de todos los idiomas en un bloque; esta prueba
    # espera el reparto por idioma de antes (Domino City español, puerta azul). Se fija el valor viejo, sin tocar su lógica.
    ("T3.sec_mundo", "T3", ["research/admin/test_sec_mundo.py", "--sin-capturas"], {"puertos": True, "env": {"YO2_ROOM_LANG_MERGE": "0"}}),
    ("T3.sec_npc_eventos", "T3", ["research/admin/test_sec_npc_eventos.py", "--sin-capturas"], {"puertos": True}),
    ("T3.sec_ranking_balance", "T3", ["research/admin/test_sec_ranking_balance.py"], {"puertos": True}),
    ("T3.sec_sistema", "T3", ["research/admin/test_sec_sistema.py"], {"puertos": True}),
    ("T3.sec_visor", "T3", ["research/admin/test_sec_visor.py", "--sin-capturas"], {"puertos": True}),
    ("T3.donde", "T3", ["research/admin/test_donde.py"], {}),
    ("T3.logES", "T3", ["research/admin/test_logES.py"], {"puertos": True}),
    ("T3.frontend", "T3", ["research/admin/test_frontend.py"], {"puertos": True}),
    ("T3.partidas_vivo", "T3", ["research/admin/test_partidas_vivo.py"], {"puertos": True}),
    ("T4.lobby_autoprueba", "T4", ["research/lobby/proposal.py", "--test"], {}),
    ("T4.amigos", "T4", ["research/lobby/test_amigos.py"], {}),
    ("T4.autoduelo", "T4", ["research/lobby/test_autoduelo.py"], {"puertos": True}),
    # --rev = el commit ANTERIOR a la pose/«Mirar» NPC (0dc56f73): la prueba comprueba que esa copia NO la trae
    ("T4.hot_npc_mirar", "T4", ["research/lobby/test_hot_npc_mirar.py", "--rev", "0dc56f73~1"], {"puertos": True}),
    # sala tras el PvP (07-10, 53975a84): la prueba directa y la carga en caliente sobre 20925cea (el código del vivo antes)
    ("T4.sala_tras_pvp", "T4", ["research/lobby/test_sala_tras_pvp.py"], {"puertos": True}),
    # 24-10 (rx/B45): el mismo usuario vuelve a entrar con su conexión vieja aún abierta (Hub._supersede)
    ("T4.reconexion", "T4", ["research/lobby/test_reconexion.py"], {"puertos": True}),
    ("T4.hot_sala_tras_pvp", "T4", ["research/lobby/test_hot_sala_tras_pvp.py", "--rev", "20925cea"], {"puertos": True}),
    ("T5.cifrado", "T5", ["research/cuentas/test_cifrado.py"], {}),
    ("T5.login", "T5", ["research/cuentas/test_login.py"], {"puertos": True}),
    ("T5.logseguro", "T5", ["research/cuentas/test_logseguro.py"], {"puertos": True}),
    ("T5.sin_sesion", "T5", ["research/cuentas/test_sin_sesion.py"], {"puertos": True, "min_offset": 5000}),
    ("T5.en_uso", "T5", ["research/cuentas/test_en_uso.py"], {"puertos": True}),
    ("T6.tienda2", "T6", ["research/persistencia/test_tienda2.py"], {"puertos": True}),
    ("T6.duelpass_cifrado", "T6", ["research/persistencia/test_duelpass_cifrado.py"], {}),
    # 24-10 (rx/MIS): contadores del perfil recogidos durante el duelo (DuelTally) + apply_duel_stats; la parte en vivo usa duelcore
    ("T6.duel_stats", "T6", ["research/persistencia/test_duel_stats.py"], {"tiempo": 600}),
    # --ref/--rev = el run.py de ANTES del cambio que carga el hot (con HEAD la 1.ª comprobación falla por diseño); los
    # ficheros «nuevos» que carga salen de git, de su propio commit (--nuevo 75a07f07 por defecto, 12-10), no del disco
    ("T6.hot_pvp_carta", "T6", ["research/persistencia/test_hot_pvp_carta.py", "--ref", "75a07f07~1"], {"puertos": True}),
    ("T7.units_b25", "T7", ["research/duelfin/test_units_b25.py"], {}),
    ("T7.units_b50", "T7", ["research/duelfin/test_units_b50.py"], {}),
    ("T7.units_desconexion", "T7", ["research/duelfin/test_units_desconexion.py"], {}),
    # 24-10 (rx/MIS): reglas de rendición del oficial (turno 10 / sala «Rendirse OFF»), unitarias
    ("T7.units_rendicion", "T7", ["research/duelfin/test_units_rendicion.py"], {}),
    ("T8.storage", "T8", ["storage/test_storage.py"], {}),
    ("T8.equipos", "T8", ["research/equipos/test_equipos.py"], {}),
    ("T8.intercambio", "T8", ["research/intercambio/test_intercambio.py"], {}),
    ("T8.torneos", "T8", ["research/torneos/test_torneos.py"], {}),
    # test_gm crea cuentas nuevas y no pone YO2_LOGIN_ESTRICTO=0 (las demás sí): sin él, −240 y conexión cerrada
    ("T8.gm", "T8", ["research/mundo/test_gm.py"], {"puertos": True, "env": {"YO2_LOGIN_ESTRICTO": "0"}}),
    ("T8.mundo_autoprueba", "T8", ["research/mundo/proposal.py"], {}),
    ("T8.brain", "T8", ["research/ia/test_brain.py"], {"tiempo": 1800}),
    # 10-10, entra con la fusión de master a604a02f: reglas de la torpeza y Return from the Different Dimension (unitarias)
    ("T8.reglas_torpeza", "T8", ["research/ia/test_reglas_torpeza.py"], {}),
    # 13-10 (agente D, rx/NPC): los 26 mazos propios de NPC (npc_decks_extra.json): reglas de cada mazo, bandera y duelos NPC vs NPC
    ("T8.npc_mazos_extra", "T8", ["research/ia/test_npc_mazos_extra.py"], {"tiempo": 900}),
    ("T8.ocg_test", "T8", ["ocg_test.py"], {}),
    ("T8.pvp_test", "T8", ["pvp_test.py"], {}),
    # 24-10 (rx/B45): reloj global (0x6461 quién = 3) del side deck entre partidas de un match (pvp_match.SWAP_TIME)
    ("T8.swap_reloj", "T8", ["research/duelfin/test_swap_reloj.py"], {"puertos": True}),
    ("T8.tickets", "T8", ["research/seguridad_duelo/test_tickets.py"], {}),
    # juega duelos hasta ver empate, gana el jugador y gana la NPC (azar): el nº de OK cambia de una pasada a otra
    ("T8.janken", "T8", ["research/lockstep/test_janken.py"], {"puertos": True, "tiempo": 1200, "ok_variable": True}),
    ("T8.hot_reserva", "T8", ["research/seguridad_duelo/test_hot_reserva.py", "--rev", "f52838d3~1"], {"puertos": True}),
    ("T8.poc_secuestro_on", "T8", ["research/seguridad_duelo/poc_secuestro.py", "--modo", "on"], {"puertos": True}),
    # archivo de repeticiones del servidor (07-10): con --offset también la instancia de prueba
    ("T8.repeticion_archivo", "T8", ["research/repeticion/test_archivo.py"], {"puertos": True}),
    # cargas en caliente del traductor (07-10, NOTES §20-§23): --ref = el run.py de ANTES de cada cambio (con HEAD, que ya
    # lo lleva, la 1.ª comprobación falla por diseño): descarte b1d36fb0, mano en cadena 7c15a1b5, invocaciones 944b2125.
    # El translator.py y el hot que se cargan encima salen de ESE commit (--nuevo, por defecto en cada prueba; 12-10):
    # con los del disco, cada cambio posterior de los métodos que vigila el hot rompía sus huellas (fallos «conocidos»).
    # test_hot_contador_turnos (§22, --ref 7c15a1b5) NO entra: es inestable por azar del duelo (el mismo --ref falló en
    # 2 de 2 pasadas con comprobaciones distintas, p. ej. «fin None» = el duelo no acabó a tiempo); avisado en la entrega.
    ("T8.hot_descarte", "T8", ["research/traductor/test_hot_descarte.py", "--ref", "b1d36fb0~1"], {"puertos": True}),
    ("T8.hot_mano_cadena", "T8", ["research/traductor/test_hot_mano_cadena.py", "--ref", "7c15a1b5~1"], {"puertos": True}),
    ("T8.hot_invocaciones", "T8", ["research/traductor/test_hot_invocaciones.py", "--ref", "944b2125~1"], {"puertos": True}),
    # 10-10, entran con la fusión de master 39636b95: el comprobador de mazos (08-10) y el SÍ/NO del sistema (§25)
    ("T8.mazo", "T8", ["research/seguridad_duelo/test_mazo.py"], {}),
    ("T8.mazo_vivo", "T8", ["research/seguridad_duelo/test_mazo_vivo.py"], {"puertos": True}),
    ("T8.hot_yesno_sys", "T8", ["research/traductor/test_hot_yesno_sys.py", "--ref", "c5665412~1"], {"puertos": True}),
    ("T8.duelcore_errores", "T8", ["duelcore/test_errores.py"], {}),
    ("T8.duelcore_cerebro", "T8", ["duelcore/test_cerebro.py"], {}),
    ("T8.duelcore_duelo", "T8", ["duelcore/test_duelo.py"], {}),
    ("T8.duelcore_bucle", "T8", ["duelcore/test_bucle.py"], {}),
    ("T8.duelcore_volcado", "T8", ["duelcore/test_volcado.py"], {}),
    # paquete yo2srv (S1, 08-10): las unitarias (sin puertos) y la viva (instancia de prueba por su consola). La base de
    # estas dos se graba en el paso S1 (antes no existía el paquete); desde entonces se comparan como todas.
    ("T10.yo2srv", "T10", ["-m", "yo2srv.tests"], {}),
    ("T10.yo2srv_vivo", "T10", ["yo2srv/tests/test_vivo.py"], {"puertos": True}),
    # R4 (recarga en caliente genérica): unitarias con servidor falso (hot_unit.py, fuera de `python -m yo2srv.tests` para no
    # cambiar el recuento de T10.yo2srv) y la prueba en una instancia de prueba (los 4 casos del plan, test_hot.py --vivo)
    ("T10.hot_unit", "T10", ["yo2srv/tests/hot_unit.py"], {}),
    ("T10.hot_vivo", "T10", ["yo2srv/tests/test_hot.py", "--vivo"], {"puertos": True, "tiempo": 1200}),
    # 11-10, moderación del anfitrión (yo2srv/admin/moderacion.py y gestion.py, pestaña «Manage» del lanzador): las unitarias
    # entran en T10.yo2srv (python -m yo2srv.tests); esta es la prueba en una instancia SIN panel y con cuentas cerradas
    ("T10.gestion_vivo", "T10", ["yo2srv/tests/test_gestion.py", "--vivo"], {"puertos": True, "tiempo": 600}),
]
# T9 (paridad) y T10 (compilar/importar/contrato) se montan aparte

# Por qué fallan los fallos conocidos de la base (van a base.json). 12-10 (rx/BAT): NINGUNO. Los 5 que había (base 11-10)
# eran de las pruebas, no del servidor, y están arreglados:
# - T2.prueba: state.json está en git con la carta 3255 en el baúl y master (7d4a8913) la quitó de valid_cards; la
#   instancia la purga al cargar (sanitize_trunk) y «perfil del owner intacto» comparaba con el baúl SIN purgar. Ahora
#   fake_client compara con el baúl saneado igual que al cargar.
# - T6.hot_pvp_carta, T8.hot_mano_cadena, T8.hot_invocaciones, T8.hot_yesno_sys: cargas en caliente de un commit viejo
#   que copiaban encima los ficheros del DISCO: el código de después (match al mejor de 3 49751d65; _chain_priority,
#   _yesno, _s_SELECT_CHAIN de §24-§28) ya no es lo que el hot sabe parchear (huellas). Ahora los toman de su commit
#   (--nuevo), como test_hot_descarte (e5774450).
NOTAS_FALLOS = {}

OK_RE = re.compile(r"^\s*(?:OK|ok)\b\s*(.*)$")
MAL_RE = re.compile(r"^\s*(?:MAL|FALLO)\b(?!S)\s*(.*)$")


def _nombre(txt):
    """Nombre estable de una comprobación: sin el detalle variable ([...], (...), -> ...)."""
    for sep in ("  [", " [", " -> ", "  (", ": ["):
        i = txt.find(sep)
        if i > 0:
            txt = txt[:i]
    return re.sub(r"\s+", " ", txt).strip()[:160]


FALLOS_RE = re.compile(r"^(prueba [^:]+|[^:]+ correctos?):.*?(\d+) (?:fallos?|con tamaño incorrecto)\s*$")
RES_RE = re.compile(r"RESULTADO[^:]*:\s*(\d+)/(\d+)(?: bien)?(?:; mal: (\[.*\]))?")


def analizar(salida, rc):
    """OK/fallos de la salida de una prueba. Si imprime «RESULTADO ...: a/b bien; mal: [...]» (fake_client y la
    mayoría), manda esa línea (hay comprobaciones que no se imprimen una a una, p. ej. las del replay); si no, se
    cuentan las líneas «OK ...» y «MAL/FALLO ...». Con varias líneas RESULTADO (test_hot_*), se suman."""
    import ast
    ok, mal = 0, []
    r_ok, r_mal, hay_res = 0, [], False
    resumen = ""
    for line in salida.splitlines():
        if OK_RE.match(line):
            ok += 1
        else:
            m = MAL_RE.match(line)
            if m:
                mal.append(_nombre(m.group(1)))
        m = FALLOS_RE.match(line)                  # research/lobby: «prueba X: N fallos» (sin líneas OK)
        if m:
            if int(m.group(2)):
                mal.append(_nombre(m.group(1)))
            else:
                ok += 1
        m = RES_RE.search(line)
        if m:
            hay_res = True
            a, b = int(m.group(1)), int(m.group(2))
            r_ok += a
            nombres = []
            if m.group(3):
                try:
                    nombres = [_nombre(str(x)) for x in ast.literal_eval(m.group(3))]
                except (ValueError, SyntaxError):
                    nombres = []
            r_mal += nombres + ["?"] * max(0, b - a - len(nombres))
        if "RESULTADO" in line or line.strip().startswith(("TODO OK", "TODO BIEN", "FALLOS")):
            resumen = line.strip()[:300]
    if hay_res:
        ok, mal = r_ok, r_mal
    return {"rc": rc, "ok": ok, "mal": len(mal), "nombres_mal": sorted(mal), "resumen": resumen}


def reanalizar(etiqueta):
    """Vuelve a contar desde _out/<etiqueta>/salidas/*.txt (sin repetir las pruebas) y reescribe su json."""
    destino = os.path.join(comun.OUT, "base.json" if etiqueta == "base" else f"{etiqueta}.json")
    datos = comun.leer_json(destino)
    if not datos:
        sys.exit(f"no hay {destino}")
    for pid, v in datos["pruebas"].items():
        f = os.path.join(comun.OUT, etiqueta, "salidas", pid.replace("/", "_") + ".txt")
        if pid.startswith(("T9", "T10")) or not os.path.exists(f):
            continue
        so = open(f, encoding="utf-8").read()
        v.update(analizar(so.split("\n\n", 1)[1] if "\n\n" in so else so, v["rc"]))
    if etiqueta == "base":
        datos["fallos_conocidos"] = {k: v["nombres_mal"] or [f"rc={v['rc']}"] for k, v in datos["pruebas"].items()
                                     if v["rc"] != 0 or v["mal"]}
    comun.escribir_json(destino, datos)
    return datos


def _env(etiqueta, pid, extra=None):
    tmp = os.path.join(comun.OUT, etiqueta, "tmp", pid)
    os.makedirs(tmp, exist_ok=True)
    env = dict(os.environ, TEMP=tmp, TMP=tmp, PYTHONIOENCODING="utf-8",
               YO2_PRUEBA_DIR=os.path.join(comun.EMU, "logs", "prueba", f"bat-{pid}"))
    for k in ("YO2_TAP", "YO2_PORT_OFFSET", "YO2_STATE"):
        env.pop(k, None)
    # log y trazas (logs/ocg) de lo que corre run.py dentro del propio proceso (ocg_test, pvp_test) a su carpeta: si no,
    # irían a logs/ y test_repeticion elegiría otra traza *duelopvp* según qué pruebas hayan pasado antes
    env["YO2_LOGDIR"] = os.path.join(env["YO2_PRUEBA_DIR"], "logs")
    env.update(extra or {})
    return env


def ejecutar(etiqueta, pid, argv, env, tiempo):
    out_dir = os.path.join(comun.OUT, etiqueta, "salidas")
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable] + argv, cwd=comun.EMU, env=env, capture_output=True, timeout=tiempo)
        rc, so = p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        rc, so = "timeout", ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace")
    seg = round(time.time() - t0, 1)
    with open(os.path.join(out_dir, pid.replace("/", "_") + ".txt"), "w", encoding="utf-8") as f:
        f.write(f"$ python {' '.join(argv)}\n# rc={rc} {seg} s\n\n{so}")
    r = analizar(so, rc)
    r.update(segundos=seg, argv=argv)
    return r


def _replay_fijo():
    """fake_client prueba repite el log más reciente de logs/ con un cliente: se le pone delante el CONGELADO."""
    src = os.path.join(comun.FIX, "replay.log")
    if not os.path.exists(src):
        return None
    dst = os.path.join(comun.EMU, "logs", "zz-paridad-replay.log")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    os.utime(dst, None)
    return dst


def correr(banda, etiqueta, solo=None, sin_paridad=False):
    motivo = comun.offset_valido(banda) or comun.offset_valido(banda + 190)
    if motivo:
        sys.exit(f"banda {banda}: {motivo}")
    if banda == comun.OFFSET_PARIDAD or abs(banda - comun.OFFSET_PARIDAD) < 200:
        sys.exit(f"banda {banda}: choca con la de la paridad ({comun.OFFSET_PARIDAD})")
    res = {}
    t_total = time.time()
    k = 0
    lista = [p for p in PRUEBAS if not solo or p[0] in solo or p[1] in solo]
    for pid, grupo, argv, op in lista:
        argv = list(argv)
        extra = dict(op.get("env", {}))
        if op.get("puertos"):
            for _ in range(40):
                # k < 20 (§3.1): las pruebas van de una en una, así que el offset se reutiliza 20 pruebas después
                off = banda + 10 * (k % 20) + (5000 if op.get("min_offset") else 0)
                k += 1
                if not comun.offset_valido(off) and not comun.ocupados(off):
                    break
                print(f"   (offset {off} ocupado o prohibido; siguiente)")
            else:
                sys.exit("no queda ningún offset libre en la banda")
            argv += ["--offset", str(off)]
        replay = _replay_fijo() if pid == "T2.prueba" else None
        r = ejecutar(etiqueta, pid, argv, _env(etiqueta, pid, extra), op.get("tiempo", 900))
        if replay and os.path.exists(replay):
            os.remove(replay)
        r["grupo"] = grupo
        res[pid] = r
        print(f"  {pid:28s} {'OK   ' if r['rc'] == 0 else 'FALLO'} ok={r['ok']:<4} mal={r['mal']:<3} rc={r['rc']!s:<7} "
              f"{r['segundos']:7.1f} s" + (f"  {r['nombres_mal'][:3]}" if r["nombres_mal"] else ""), flush=True)
    if not solo or "T9" in solo:
        if not sin_paridad:
            res["T9.paridad"] = paridad(etiqueta)
            print(f"  {'T9.paridad':28s} {'OK   ' if res['T9.paridad']['rc'] == 0 else 'FALLO'} "
                  f"{res['T9.paridad']['resumen']}  {res['T9.paridad']['segundos']:7.1f} s", flush=True)
    if not solo or "T10" in solo:
        res["T10.compilar"] = compilar(etiqueta, banda)
        print(f"  {'T10.compilar':28s} {'OK   ' if res['T10.compilar']['rc'] == 0 else 'FALLO'} "
              f"{res['T10.compilar']['resumen']}", flush=True)
    return res, round(time.time() - t_total, 1)


def paridad(etiqueta):
    t0 = time.time()
    g = os.path.join(comun.AQUI, "grabar.py")
    c = os.path.join(comun.AQUI, "comparar.py")
    env = _env(etiqueta, "T9")
    if etiqueta == "base":
        # la base de la batería NO rehace la base de paridad (3 pasadas, máscaras): eso es grabar.py --base aparte
        argv_g = [g, "--etiqueta", "bateria-base"]
        argv_c = [c, "bateria-base"]
    else:
        argv_g = [g, "--etiqueta", etiqueta]
        argv_c = [c, etiqueta]
    r1 = subprocess.run([sys.executable] + argv_g, cwd=comun.EMU, env=env, capture_output=True)
    r2 = subprocess.run([sys.executable] + argv_c, cwd=comun.EMU, env=env, capture_output=True)
    so = (r1.stdout + r1.stderr + r2.stdout + r2.stderr).decode("utf-8", "replace")
    out_dir = os.path.join(comun.OUT, etiqueta, "salidas")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "T9.paridad.txt"), "w", encoding="utf-8") as f:
        f.write(so)
    lin = [x for x in so.splitlines() if x.startswith("== paridad")]
    return {"rc": r2.returncode if r1.returncode == 0 else r1.returncode, "ok": sum("IGUAL" in x for x in lin),
            "mal": sum("DISTINTO" in x for x in lin), "nombres_mal": [x for x in lin if "DISTINTO" in x],
            "resumen": "; ".join(lin)[:300], "segundos": round(time.time() - t0, 1), "grupo": "T9"}


def compilar(etiqueta, banda):
    t0 = time.time()
    dirs = [d for d in ("yo2srv", "research", "storage", "paridad") if os.path.isdir(os.path.join(comun.EMU, d))]
    top = sorted(glob.glob(os.path.join(comun.EMU, "*.py")))
    so, rc = "", 0
    for argv in ([sys.executable, "-m", "compileall", "-q"] + dirs + top,
                 [sys.executable, "-c", "import yo2srv"] if os.path.isdir(os.path.join(comun.EMU, "yo2srv")) else None,
                 [sys.executable, os.path.join(comun.AQUI, "test_contrato.py"), "--banda", str(banda)]
                 if os.path.exists(os.path.join(comun.AQUI, "test_contrato.py")) else None):
        if not argv:
            continue
        p = subprocess.run(argv, cwd=comun.EMU, capture_output=True, env=_env(etiqueta, "T10"))
        so += f"$ {' '.join(os.path.basename(a) for a in argv)}\n" + (p.stdout + p.stderr).decode("utf-8", "replace")
        rc = rc or p.returncode
    out_dir = os.path.join(comun.OUT, etiqueta, "salidas")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "T10.compilar.txt"), "w", encoding="utf-8") as f:
        f.write(so)
    r = analizar(so, rc)
    r.update(resumen=f"compileall {'+ import yo2srv ' if 'import yo2srv' in so else ''}{'+ contrato ' if 'contrato' in so else ''}"
                     f"rc={rc}", segundos=round(time.time() - t0, 1), grupo="T10")
    return r


# Ficheros EN GIT que algunas pruebas reescriben al pasar (salidas de ejemplo): si estaban limpios antes de la batería,
# se devuelven a su versión de git al acabar (si no, cada pasada dejaría la copia de trabajo sucia).
SALIDAS_EN_GIT = ["duelcore/out/ejemplos_por_tipo.json", "duelcore/out/volcado_3turnos.jsonl",
                  "research/ia/brain_out/resultados.json"]


def _sucios(rutas):
    r = subprocess.run(["git", "-C", comun.EMU, "status", "--porcelain", "--"] + rutas, capture_output=True, text=True)
    return {line[3:].strip() for line in r.stdout.splitlines()}


def restaurar_salidas(limpias_antes):
    if limpias_antes:
        subprocess.run(["git", "-C", comun.EMU, "checkout", "--"] + sorted(limpias_antes), capture_output=True)


OK_VARIABLE = {pid for pid, _, _, op in PRUEBAS if op.get("ok_variable")}


def igual(a, b, pid=None):
    """Mismo rc, mismos fallos y (salvo en las pruebas con nº de comprobaciones al azar) mismo nº de OK."""
    return a and b and a["rc"] == b["rc"] and (pid in OK_VARIABLE or a["ok"] == b["ok"]) and a["mal"] == b["mal"] \
        and a["nombres_mal"] == b["nombres_mal"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--banda", type=int)
    ap.add_argument("--etiqueta", default="actual")
    ap.add_argument("--solo", default="", help="ids o grupos separados por comas (T1,T2.pvp,T3...)")
    ap.add_argument("--sin-paridad", action="store_true")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--reanalizar", metavar="ETIQUETA", help="recuenta OK/fallos desde las salidas guardadas")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.listar:
        for pid, grupo, argv, op in PRUEBAS:
            print(f"{pid:28s} python {' '.join(argv)}" + (" --offset N" if op.get("puertos") else "")
                  + (f"   ({' '.join(f'{k}={v}' for k, v in op['env'].items())})" if op.get("env") else ""))
        print("T9.paridad                   python paridad/grabar.py --etiqueta E && python paridad/comparar.py E")
        print("T10.compilar                 python -m compileall -q yo2srv research storage paridad *.py; import yo2srv; "
              "paridad/test_contrato.py (si existen)")
        return 0
    if a.reanalizar:
        d = reanalizar(a.reanalizar)
        for k, v in d["pruebas"].items():
            print(f"  {k:28s} rc={v['rc']!s:<7} ok={v['ok']:<4} mal={v['mal']:<3} {v['nombres_mal'][:3] if v['nombres_mal'] else ''}")
        return 0
    if a.banda is None:
        ap.error("--banda N (A = 1000, B = 1300, C = 1600, integrador = 1900)")
    solo = [x for x in a.solo.split(",") if x] or None
    print(f"== batería {a.etiqueta} (banda {a.banda}) en {comun.EMU}; cerrojo {comun.ruta_cerrojo()}")
    comun.asegurar_datos()                         # _fixtures, base.json y paridad-base que falten, de la carpeta común
    sucios = {os.path.normpath(p) for p in _sucios(SALIDAS_EN_GIT)}
    limpias = [p for p in SALIDAS_EN_GIT if not any(os.path.normpath(p) in s or s.endswith(os.path.normpath(p))
                                                    for s in sucios)]
    try:
        res, seg = correr(a.banda, a.etiqueta, solo, a.sin_paridad)
    finally:
        restaurar_salidas(limpias)
    datos = {"etiqueta": a.etiqueta, "banda": a.banda, "fecha": time.strftime("%Y-%m-%d %H:%M"), "segundos": seg,
             "commit": subprocess.run(["git", "-C", comun.EMU, "rev-parse", "--short", "HEAD"], capture_output=True,
                                      text=True).stdout.strip(), "pruebas": res}
    if a.etiqueta == "base":
        datos["fallos_conocidos"] = {k: v["nombres_mal"] or [f"rc={v['rc']}"] for k, v in res.items()
                                     if v["rc"] != 0 or v["mal"]}
        datos["notas_fallos"] = {k: NOTAS_FALLOS.get(k, "SIN EXPLICAR") for k in datos["fallos_conocidos"]}
        destino = os.path.join(comun.OUT, "base.json")
        if solo and os.path.exists(destino):           # una base parcial se mezcla con la que había
            viejo = comun.leer_json(destino)
            viejo["pruebas"].update(res)
            viejo["fallos_conocidos"] = {k: v["nombres_mal"] or [f"rc={v['rc']}"] for k, v in viejo["pruebas"].items()
                                         if v["rc"] != 0 or v["mal"]}
            viejo["notas_fallos"] = {k: NOTAS_FALLOS.get(k, "SIN EXPLICAR") for k in viejo["fallos_conocidos"]}
            datos = viejo
    else:
        destino = os.path.join(comun.OUT, f"{a.etiqueta}.json")
    comun.escribir_json(destino, datos)
    print(f"== {len(res)} pruebas en {seg} s -> {destino}")
    base = comun.leer_json(os.path.join(comun.OUT, "base.json"))
    if a.etiqueta != "base" and base:
        dist = []
        for k, v in res.items():
            b = base["pruebas"].get(k)
            if b is None and k != "T9.paridad":        # una prueba nueva, aún sin base: vale si pasa (rc 0 y sin fallos)
                print(f"   NUEVA {k}: sin base; ahora {(v['rc'], v['ok'], v['mal'])}")
                ok = v["rc"] == 0 and not v["mal"]
            elif k == "T9.paridad":
                ok = v["rc"] == 0
            else:
                ok = igual(v, b, k)
            if not ok:
                dist.append(k)
                print(f"   DISTINTO {k}: base {b and (b['rc'], b['ok'], b['mal'])} / ahora {(v['rc'], v['ok'], v['mal'])}"
                      + (f"  fallos nuevos {sorted(set(v['nombres_mal']) - set(b['nombres_mal']))}" if b else ""))
        print("== contra la base:", "IGUAL" if not dist else f"DISTINTO en {len(dist)}: {dist}")
        return 0 if not dist else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
