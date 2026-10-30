"""Rutas del servidor, TODAS calculadas desde tools/yo2emu (REESTRUCTURA.md §4, riesgo R3).

Nada se calcula desde la carpeta de este módulo hacia abajo ni desde el directorio de trabajo: `EMU` es la carpeta que
contiene a `yo2srv/`, es decir, la misma que hoy es `HERE` en run.py. Así una ruta resuelve igual en la worktree, en el
árbol principal y cuando el módulo se mueva de sitio (basta con cambiar `_EMU_DESDE_AQUI`).

Las fórmulas son las de run.py/panel.py de hoy, una a una (la prueba test_rutas las compara con ellas):

    HERE        = dirname(abspath(run.py))                                              -> EMU
    GAME        = normpath(HERE/../../_original/yo2_2008-11)                           -> GAME
    STATE_PATH  = HERE/state.json, o abspath($YO2_STATE)                                -> state_path()
    LOGDIR      = $YO2_LOGDIR, o HERE/logs[/prueba si hay YO2_PORT_OFFSET != 0]       -> logdir()
    consola     = ($LOCALAPPDATA o HERE)/YO2Online/consola_<puerto>.txt                 -> tokens_consola()
    panel       = ($LOCALAPPDATA o ~/AppData/Local)/YO2Online/admin_token.txt           -> tokens_panel()

OJO: la carpeta de tokens de la consola (run.py) y la del panel (panel.py) tienen hoy un respaldo distinto cuando no hay
LOCALAPPDATA (HERE frente a ~/AppData/Local). Se conservan las dos tal cual; unificarlas sería un cambio de
comportamiento (no ocurre en Windows normal, donde LOCALAPPDATA existe).

Empaquetado (P1, 11-10; paquete/RUTAS.md): el servidor puede arrancar desde un PAQUETE (Python embeddable con solo la
stdlib + node.exe + duelcore.mjs) en cualquier PC. Todo lo que cambia va detrás de YO2_PAQUETE=1; sin ella (desarrollo,
el vivo del owner) cada función de aquí devuelve EXACTAMENTE lo de antes. Variables (un solo sitio: este módulo):

    YO2_PAQUETE       1 = modo paquete (estado y logs en %LOCALAPPDATA%, sin cliente, OCG obligatorio, logs con tope)
    YO2_JUEGO_DATOS   raíz de los datos del juego = la carpeta data/ del merge.dat del cliente 2008 (cada entrada
                      data/… sin ese data/: lo que escribe paquete/extraer_juego.py de P2). Hoy:
                      <root>/_original/extracted/merge2008/data
    YO2_VENDOR        cards.cdb, strings.conf y CardScripts/. Hoy: <root>/_vendor (duelcore la recibe como VENDOR_DIR)
    YO2_NODE          node.exe. Hoy: el `node` del PATH; si no está (app de macOS sin nvm/Homebrew en el PATH),
                      nvm / Homebrew / /usr/local (≥ 18, para tsx y duelcore)
    YO2_DUELCORE      el motor: un .mjs (esbuild, un solo fichero) se lanza con `node <mjs>`; un .mts, con tsx como hoy.
                      Hoy: <emu>/duelcore/duelcore.mts
    YO2_STATE / YO2_LOGDIR   como siempre; en modo paquete, sin ellas: %LOCALAPPDATA%\\YO2Online\\servidor\\<5730+offset>\\
                      state.json y …\\logs
"""
import os
import shutil
import subprocess
from typing import NamedTuple, Optional, Tuple

# Este fichero está en <EMU>/yo2srv/rutas.py. Si el módulo cambia de sitio, se cambia SOLO esto.
_EMU_DESDE_AQUI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

EMU = os.path.normpath(_EMU_DESDE_AQUI)                       # tools/yo2emu (HERE de run.py)
ROOT = os.path.normpath(os.path.join(EMU, "..", ".."))        # raíz del repo (DuelCards)


class Rutas(NamedTuple):
    """Las rutas que no dependen del entorno. Las que sí (estado, log, tokens) son funciones de este módulo."""
    emu: str
    root: str
    original: str        # <root>/_original
    game: str            # <root>/_original/yo2_2008-11 (el cliente de 2008; GAME de run.py)
    packinfo: str        # PACKINFO de run.py (catálogo de sobres del original)
    www: str             # <emu>/www: lo que sirve el HTTP de run.py (8730)
    panel_www: str       # <emu>/research/admin/www: el frontend del panel (hasta el paso A5)
    net_hook: str        # <emu>/net_hook.js (Frida)


def desde(emu: Optional[str] = None) -> Rutas:
    """Rutas derivadas de una carpeta `tools/yo2emu` (por defecto, la de este paquete)."""
    emu = os.path.normpath(emu or EMU)
    root = os.path.normpath(os.path.join(emu, "..", ".."))
    original = os.path.join(root, "_original")
    return Rutas(
        emu=emu,
        root=root,
        original=original,
        game=os.path.normpath(os.path.join(emu, "..", "..", "_original", "yo2_2008-11")),
        packinfo=os.path.normpath(os.path.join(emu, "..", "..", "_original", "extracted", "merge2008", "data",
                                               "cardpack", "packinfo_s.txt")),
        www=os.path.join(emu, "www"),
        panel_www=os.path.join(emu, "research", "admin", "www"),
        net_hook=os.path.join(emu, "net_hook.js"),
    )


def _entorno(env):
    return os.environ if env is None else env


def port_offset(env=None) -> int:
    """YO2_PORT_OFFSET como lo lee run.py (vacío = 0; un valor que no es entero lanza ValueError, como allí)."""
    return int(_entorno(env).get("YO2_PORT_OFFSET", "0") or 0)


def paquete(env=None) -> bool:
    """¿Modo paquete? (YO2_PAQUETE con un valor que no sea vacío ni 0)."""
    return _entorno(env).get("YO2_PAQUETE", "") not in ("", "0")


def datos_paquete(env=None) -> str:
    """Carpeta de los datos de un servidor en modo paquete: %LOCALAPPDATA%\\YO2Online\\servidor\\<5730 + offset> (sin
    LOCALAPPDATA, ~/AppData/Local, como el panel). Un servidor por puerto: dos instancias no se pisan, y la de siempre
    (offset 0) es «servidor\\5730» aunque cambie el perfil de banderas (las cuentas no dependen del perfil)."""
    e = _entorno(env)
    base = e.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "YO2Online", "servidor", str(5730 + port_offset(e)))


def state_path(emu: Optional[str] = None, env=None) -> str:
    """STATE_PATH de run.py: <emu>/state.json, o la ruta absoluta de YO2_STATE (instancias de prueba). En modo paquete y
    sin YO2_STATE: <datos_paquete>/state.json."""
    emu = os.path.normpath(emu or EMU)
    e = _entorno(env)
    ruta = os.path.join(datos_paquete(e) if paquete(e) else emu, "state.json")
    pedido = e.get("YO2_STATE")
    if pedido:
        ruta = os.path.abspath(pedido)
    return ruta


def datos_dir(emu: Optional[str] = None, env=None) -> str:
    """Carpeta de los datos de juego (junto a state.json): admin_*.json, admin_eventos/, copias de arranque…"""
    return os.path.dirname(os.path.abspath(state_path(emu, env)))


def logdir(emu: Optional[str] = None, env=None) -> str:
    """LOGDIR de run.py: YO2_LOGDIR, o <emu>/logs (y <emu>/logs/prueba con YO2_PORT_OFFSET != 0). En modo paquete y sin
    YO2_LOGDIR: <datos_paquete>/logs."""
    emu = os.path.normpath(emu or EMU)
    e = _entorno(env)
    if e.get("YO2_LOGDIR"):
        return e["YO2_LOGDIR"]
    if paquete(e):
        return os.path.join(datos_paquete(e), "logs")
    extra = ["prueba"] if port_offset(e) else []
    return os.path.join(emu, "logs", *extra)


def trazas_ocg(emu: Optional[str] = None, env=None) -> str:
    """TRACE_DIR de ocg_duel.py (trazas del motor, un .jsonl por duelo): <YO2_LOGDIR>/ocg, o <emu>/logs/ocg. OJO: sin
    YO2_LOGDIR van a logs/ocg aunque haya YO2_PORT_OFFSET (no a logs/prueba/ocg): es así desde el 06-10 y las pruebas
    (test_translator) buscan ahí sus trazas por nombre. En modo paquete y sin YO2_LOGDIR: <logdir()>/ocg."""
    emu = os.path.normpath(emu or EMU)
    e = _entorno(env)
    if e.get("YO2_LOGDIR"):
        return os.path.join(e["YO2_LOGDIR"], "ocg")
    if paquete(e):
        return os.path.join(logdir(emu, e), "ocg")
    return os.path.join(emu, "logs", "ocg")


# ------------------------------------------------------------------------------- datos del juego, vendor, node, motor
def juego_datos(env=None) -> str:
    """YO2_JUEGO_DATOS (raíz de los datos del juego, ver paquete/RUTAS.md) o <root>/_original/extracted/merge2008/data."""
    v = _entorno(env).get("YO2_JUEGO_DATOS")
    return os.path.abspath(v) if v else os.path.join(ROOT, "_original", "extracted", "merge2008", "data")


def juego(*partes, env=None) -> str:
    """Ruta bajo la raíz de los datos del juego: juego("bin#", "card_prop.bin")."""
    return os.path.join(juego_datos(env), *partes)


def vendor(env=None) -> str:
    """YO2_VENDOR (cards.cdb, strings.conf, CardScripts/) o <root>/_vendor."""
    v = _entorno(env).get("YO2_VENDOR")
    return os.path.abspath(v) if v else os.path.join(ROOT, "_vendor")


# tsx 4 / duelcore.mts: Node ≥ 18. Una app abierta desde el Finder no hereda nvm ni Homebrew.
_NODE_MIN = (18, 0, 0)


def _version_de_nombre_node(nombre: str) -> Optional[Tuple[int, int, int]]:
    """'v20.20.2' / '20.20.2' -> (20, 20, 2). None si no es una versión."""
    s = nombre[1:] if nombre[:1] in "vV" else nombre
    try:
        parts = [int(p) for p in s.split(".")[:3]]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def _version_node(exe: str) -> Optional[Tuple[int, int, int]]:
    try:
        out = subprocess.check_output([exe, "-v"], stderr=subprocess.DEVNULL, timeout=5, text=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return _version_de_nombre_node(out.strip())


def _node_conocido(home: Optional[str] = None) -> Optional[str]:
    """node fuera del PATH: nvm (más nuevo ≥ 18), Homebrew, /usr/local, volta, fnm."""
    if home is None:
        home = os.path.expanduser("~")
    cands = []  # (versión, ruta)

    def add(path, ver=None):
        if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
            return
        if ver is None:
            ver = _version_node(path)
        if ver is None:
            return
        cands.append((ver, path))

    nvm = os.path.join(home, ".nvm", "versions", "node")
    if os.path.isdir(nvm):
        for name in os.listdir(nvm):
            add(os.path.join(nvm, name, "bin", "node"), _version_de_nombre_node(name))
    add("/opt/homebrew/bin/node")
    add("/usr/local/bin/node")
    add(os.path.join(home, ".volta", "bin", "node"))
    add(os.path.join(home, ".local", "share", "fnm", "aliases", "default", "bin", "node"))
    add(os.path.join(home, "Library", "Application Support", "fnm", "aliases", "default", "bin", "node"))

    buenos = [c for c in cands if c[0] >= _NODE_MIN]
    pool = buenos or cands
    if not pool:
        return None
    pool.sort(reverse=True)
    return pool[0][1]


def node(env=None) -> Optional[str]:
    """YO2_NODE (node.exe a usar) o el `node` del PATH (None si no hay).

    Una app de macOS abierta desde el Finder no hereda el PATH del shell: si `which node`
    falla o el del PATH es < 18, se miran nvm / Homebrew / /usr/local.
    """
    v = _entorno(env).get("YO2_NODE")
    if v:
        return os.path.abspath(v) if os.path.dirname(v) else (shutil.which(v) or v)
    found = shutil.which("node")
    if found:
        ver = _version_node(found)
        if ver is not None and ver >= _NODE_MIN:
            return found
    return _node_conocido() or found


def packinfo(env=None) -> str:
    """PACKINFO de la tienda (catálogo de sobres): <juego_datos>/cardpack/packinfo_s.txt. Sin YO2_JUEGO_DATOS es la
    misma ruta que desde().packinfo (la fórmula de run.py)."""
    return juego("cardpack", "packinfo_s.txt", env=env)


def duelcore(env=None) -> str:
    """YO2_DUELCORE (un .mjs empaquetado o un .mts) o <emu>/duelcore/duelcore.mts."""
    v = _entorno(env).get("YO2_DUELCORE")
    return os.path.abspath(v) if v else os.path.join(EMU, "duelcore", "duelcore.mts")


# Lo que el SERVIDOR lee de la raíz de los datos del juego (paquete/RUTAS.md lo explica fichero a fichero; la prueba
# test_rutas comprueba que RUTAS.md y esta lista dicen lo mismo). Relativo a juego_datos(), con «/».
#   JUEGO_FICHEROS: ficheros concretos; sin ellos el servidor no tiene cartas, tienda o avatar (modo paquete: no arranca).
#   JUEGO_CARPETAS: carpetas de las que se leen ficheros sueltos según lo que pida el juego (sobres, mazos de estructura).
JUEGO_FICHEROS = (
    "bin#/card_prop.bin",               # cardmap: id YO2 -> id interno (cartas del motor)
    "bin#/card_name_e.bin",             # cardmap: nombres en inglés (cruce con cards.cdb)
    "bin#/card_indx_e.bin",             # cardmap: índice de esos nombres
    "common/yo2pbinmap.dat",            # avatar + persistencia: bits PBIN (ropa, títulos)
    "cardpack/packinfo_s.txt",          # tienda: catálogo de sobres
)
JUEGO_CARPETAS = (
    "cardpack",                         # <id>.iid (sobres: tienda) y <id>.ydc (mazos de estructura: tienda, persistencia)
)


# Tablas del servidor que salen del cliente y genera paquete/generar_tablas.py en el PC del anfitrión (relativas a EMU;
# generar_tablas.TABLAS dice lo mismo: la prueba test_paquete lo compara).
TABLAS_GENERADAS = (
    "research/ia/cards_yo2.csv",                # cardmap, lobby 0x5600, mazo.py, persistencia, npcreglas
    "research/ia/npc_decks.json",               # recetas de las NPC (ocg_duel, npcreglas)
    "research/ia/npc_profiles.json",            # perfil de la IA de cada NPC (brain)
    "research/persistencia/valid_cards.json",   # ids con dibujo (B05: baúl, sobres, mazos)
    "research/persistencia/titles.json",        # tabla de títulos del exe (persistencia)
)


def faltan_datos(env=None) -> list:
    """Lo que falta para arrancar en modo paquete: [ruta absoluta] de JUEGO_FICHEROS, cards.cdb, strings.conf,
    CardScripts/, el motor (duelcore) y node. Vacía = todo está."""
    falta = [juego(*f.split("/"), env=env) for f in JUEGO_FICHEROS if not os.path.isfile(juego(*f.split("/"), env=env))]
    v = vendor(env)
    falta += [os.path.join(v, f) for f in ("cards.cdb", "strings.conf") if not os.path.isfile(os.path.join(v, f))]
    if not os.path.isdir(os.path.join(v, "CardScripts")):
        falta.append(os.path.join(v, "CardScripts"))
    # P5/PUB: tablas que salen del cliente: ni el zip ni el código abierto las llevan, las rehace paquete/generar_tablas.py
    falta += [os.path.join(EMU, *t.split("/")) for t in TABLAS_GENERADAS if not os.path.isfile(os.path.join(EMU, *t.split("/")))]
    if not os.path.isfile(duelcore(env)):
        falta.append(duelcore(env))
    n = node(env)
    if not n or not os.path.isfile(n):
        falta.append(n or "node (YO2_NODE o el PATH)")
    return falta


def tokens_consola(emu: Optional[str] = None, env=None) -> str:
    """Carpeta de consola_<puerto>.txt (run.py: `admin()`): %LOCALAPPDATA%\\YO2Online, o <emu>\\YO2Online sin ella."""
    emu = os.path.normpath(emu or EMU)
    return os.path.join(_entorno(env).get("LOCALAPPDATA") or emu, "YO2Online")


def tokens_panel(env=None) -> str:
    """Carpeta de admin_token.txt (panel.py `_token_paths`): %LOCALAPPDATA%\\YO2Online, o ~/AppData/Local sin ella."""
    base = _entorno(env).get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "YO2Online")


def token_consola(puerto: int, emu: Optional[str] = None, env=None) -> str:
    return os.path.join(tokens_consola(emu, env), f"consola_{int(puerto)}.txt")
