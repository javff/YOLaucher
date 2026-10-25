r"""Monta el paquete del Discord: un zip con el lanzador y TODO lo que hace falta para hospedar un servidor en un PC que
solo tiene el cliente 2008 (sin instalar Python ni Node). P5, 11-10.

    python paquete/empaquetar.py --lanzador <YO2OnlineLauncher.exe compilado con build.ps1 -Salida>
    python paquete/empaquetar.py ... --version 20261011120000 --sin-zip
    python paquete/empaquetar.py --lanzador launcher/dist-app/YO2OnlineLauncher.exe --firmar     (VERSIÓN para publicar)

--firmar (UPD, 12-10): el paquete entero es una VERSIÓN firmada por el editor. Se escribe el manifiesto
(paquete.manifiesto.json: versión, versión del lanzador y cada fichero con su parte, sha256 y tamaño; formato en
launcher/sala/paquete_man.go), firmar.exe -paquete lo comprueba contra el árbol y lo firma (paquete.json; la clave
privada solo existe en el PC del editor) y el árbol firmado se copia a %LOCALAPPDATA%\YO2Online\semilla: desde ahí lo
reparte el lanzador del editor al hospedar (por la lista y al hacer Join), y cada lanzador que se actualiza lo reparte
a su vez. El exe va con su .firma (la de app\build.ps1 -Firmar, o la pone firmar.exe aquí), así los lanzadores
anteriores (que solo saben bajar el exe) también suben. Sin --firmar el zip sale sin versión firmada: nadie lo
actualiza solo.

Deja en paquete/_build/ (ignorado en git):
    YO2Server/                        el árbol (raíz del zip: carpeta «YO2OnlineLauncher»)
        paquete.json                  (con --firmar) la versión firmada: manifiesto + firma del editor
        YO2OnlineLauncher.exe.firma   (con --firmar) firma de editor del exe (autoactualización de lanzadores viejos)
        YO2OnlineLauncher.exe         el lanzador (sala/paquete.go lo reconoce por server\ y python\ a su lado). NUNCA
                                      «YO2Launcher.exe»: Windows no distingue mayúsculas y pisaría el Yo2Launcher.exe de
                                      Konami si alguien lo descomprime en la carpeta del juego (12-10)
        README.txt, THIRD-PARTY.txt
        python/                       Python 3.12 embeddable; python312._pth añade ..\\server\\yo2emu y ..\\server
        node/node.exe                 Node 22 LTS (>= 22.13: node:sqlite)
        engine/duelcore.mjs           el motor (bundle_duelcore.py; el WASM va dentro)
        engine/scripts_2008/cNNNNN.lua  scripts de las cartas que se juegan con su texto de 2008 (duelcore/scripts_2008/LEEME.md)
        vendor/                       cards.cdb, strings.conf, CardScripts/ (sin .git) — ProjectIgnis, AGPL-3.0
        server/yo2emu/...             SOLO lo que el servidor carga (lista SERVIDOR, sacada de una traza real de duelos
                                      NPC/PvP/tienda/salas + RUTAS.md/DATOS_JUEGO.md); server/build_cards.py y kcej_dat.py
    YO2OnlineLauncher-<version>.zip

NADA de Konami va dentro: los datos del juego los saca el lanzador del cliente del anfitrión en cada arranque
(paquete/primer_arranque.py = extraer_juego.py + generar_tablas.py, que rehace las tablas del servidor que salen del
cliente: cards_yo2.csv, npc_decks.json, npc_profiles.json, valid_cards.json y titles.json). Tampoco el panel admin
(research/admin, privado), pruebas, logs, estado, tokens, claves ni datos vivos: la lista es una lista BLANCA de
ficheros del git (git ls-files), y luego se comprueba (NUNCA + escaneo de IPs y de las reglas personales) y se para si
algo no cuadra.

Reglas personales (PUB, 12-10): lo que se sustituye en la copia y lo que el escaneo busca de cada editor (sus nombres,
rutas, cuenta e IPs) NO está en este fichero: va en paquete/empaquetar.local.json (formato:
paquete/empaquetar.local.example.json; --reglas para usar otro). Sin él solo se aplica el escaneo genérico de IPs, con
un aviso.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))               # tools/yo2emu/paquete
EMU = os.path.dirname(AQUI)                                       # tools/yo2emu
TOOLS = os.path.dirname(EMU)                                      # tools
ROOT = os.path.dirname(TOOLS)                                     # raíz del repo (o de la worktree)
BUILD = os.path.join(AQUI, "_build")
ARBOL = os.path.join(BUILD, "YO2Server")
RAIZ_ZIP = "YO2OnlineLauncher"
EXE_LANZADOR = "YO2OnlineLauncher.exe"                          # = sala.ExeLanzadorApp (launcher/sala/actualiza.go)
EXE_KONAMI = "yo2launcher.exe"                                  # el de Konami en la carpeta del cliente: nunca este nombre
MANIFIESTO = "paquete.manifiesto.json"                          # = sala.NombrePaqueteSinFir (sin firmar)
FIRMADO = "paquete.json"                                        # = sala.NombrePaquete (lo escribe firmar.exe -paquete)
PARTES_DIR = ("server", "engine", "vendor", "python", "node")   # = sala.PartesDir
FIRMAR_EXE = os.path.join(EMU, "launcher", "firmar.exe")        # lo deja app\build.ps1 -Firmar
NODOS_RED = os.path.join(EMU, "launcher", "red", "nodos.txt")   # nodos de arranque/relays que se firman (NOTES-red.md §13)


def _principal():
    """Raíz del árbol principal del repo (el primero de `git worktree list`): ahí están _descargas y _vendor reales."""
    try:
        out = subprocess.run(["git", "-C", ROOT, "worktree", "list", "--porcelain"], capture_output=True, text=True,
                             check=True).stdout
        return os.path.normpath(out.splitlines()[0].split(" ", 1)[1])
    except Exception:                                              # noqa: BLE001
        return ROOT


DESCARGAS = os.path.join(_principal(), "tools", "yo2emu", "_descargas")
PY_ZIP = "python-3.12.10-embed-amd64.zip"
PY_SHA = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"      # SPDX de python.org (3.12.10)
NODE_ZIP = "node-v22.23.3-win-x64.zip"
NODE_SHA = "2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71"    # SHASUMS256.txt de nodejs.org

# Lista BLANCA del servidor (rutas relativas a tools/yo2emu, «*» solo dentro de un segmento). Por qué cada una: la traza
# del 11-10 (gancho de auditoría en fake_client npc/pvp/pvpcarta/dpcoste/salas) + yo2srv/arranque.py + RUTAS.md.
SERVIDOR = (
    "run.py", "proto.py", "ocg_duel.py", "pvp_hub.py", "pvp_match.py", "npc_watch.py",
    "auto_replies.json", "npc_ids.json",                       # npc_ids.json se copia SIN diálogos (ver _npc_ids)
    "yo2srv/*.py", "yo2srv/*/*.py",
    "duelcore/duelcore.py",
    "storage/*.py",                                           # YO2_STORAGE=sqlite (el lanzador lo pone)
    "www/yo2/*",                                              # HTTP 8730 (listas de parches del cliente)
    "research/avatar/proposal.py",
    "research/cuentas/logseguro.py", "research/cuentas/cifrado.py",
    "research/duelfin/proposal.py",
    "research/equipos/proposal.py",
    "research/ia/brain.py",                                   # (npc_decks/npc_profiles.json: generar_tablas.py)
    "research/ia/build_ia_tables.py",                         # generar_tablas.py reutiliza su lógica
    "research/ia/npc_decks_extra.json",                       # mazos PROPIOS de 26 NPC (bandera ocg.NPC_DECKS_EXTRA, apagada)
    "research/intercambio/proposal.py",
    "research/lobby/proposal.py", "research/lobby/auto_overrides.json",
    "research/lockstep/proposal.py",
    "research/mundo/proposal.py",
    "research/npcreglas/proposal.py",
    "research/persistencia/proposal.py", "research/persistencia/duelpass.py",  # (titles/valid_cards: generar_tablas)
    "research/repeticion/cliente_sim.py", "research/repeticion/duellog.py", "research/repeticion/rep_archivo.py",
    "research/seguridad_duelo/mazo.py", "research/seguridad_duelo/tickets.py",
    "research/torneos/proposal.py",
    "research/traductor/cardmap.py", "research/traductor/translator.py",
    "paquete/extraer_juego.py", "paquete/generar_tablas.py", "paquete/primer_arranque.py",
)
FUERA = ("yo2srv/tests/*", "yo2srv/*/test_*", "yo2srv/test_*", "yo2srv/gen_banderas.py", "storage/test_*",
         "storage/__main__.py")
DE_TOOLS = ("build_cards.py", "kcej_dat.py")                  # van a server/ (cardmap y extraer_juego los importan)

# Red de seguridad: si algo de esto aparece en la lista final, se para. Las tablas que salen del cliente nunca van
# (las genera primer_arranque.py en el PC del anfitrión), ni las reglas personales.
NUNCA = re.compile(r"(^|/)(state[^/]*\.json|.*\.sqlite[^/]*|admin_[^/]*|.*token[^/]*|.*\.key|.*\.bak[^/]*|logs/|ghidra/|"
                   r"_original/|research/admin/|firmar/|anfitrion/|identidad[^/]*|.*\.log|cards_yo2\.csv|"
                   r"npc_decks\.json|npc_profiles\.json|npc_fuki\.json|valid_cards\.json|titles\.json|"
                   r"empaquetar\.local[^/]*|consola_[^/]*|gestion_[^/]*|\.git/)", re.I)
# Escaneo GENÉRICO del contenido (texto) del servidor y de los .txt: IPs que no son de prueba. Las reglas personales
# (rutas, nombres y cuentas del editor) se le añaden desde empaquetar.local.json (cargar_reglas).
ESCANEO_IP = r"\b(?!127\.)(?!0\.0\.0\.0)(?!255\.)(?:\d{1,3}\.){3}\d{1,3}\b"
ESCANEO = re.compile(ESCANEO_IP)
REGLAS_LOCAL = os.path.join(AQUI, "empaquetar.local.json")
# Coincidencias del escaneo revisadas a mano y aceptadas (fichero relativo a la raíz del árbol, texto exacto): valores
# inventados de las autopruebas de research/lobby (nunca se ejecutan en el paquete).
ESCANEO_OK = {
    ("server/yo2emu/research/lobby/proposal.py", "10.0.0.5"),
    ("server/yo2emu/research/lobby/proposal.py", "1.1.1.1"),
}
# Lo que se SUSTITUYE en la copia del paquete (texto de .py): empaquetar.local.json «sanear» ([[texto, sustituto], ...],
# en ese orden). Típico: el nombre del personaje del editor en comentarios y autopruebas, una IP suya de un comentario y
# la cuenta de desarrollo por defecto (en modo paquete OWNER_ACCT es None: yo2srv/cuentas/sesion.py). Nada de esto
# cambia el comportamiento del servidor en el paquete.
SANEAR = ()


def cargar_reglas(ruta=REGLAS_LOCAL):
    """Lee las reglas personales: «sanear» -> SANEAR y «escaneo» ([regex], sin distinguir mayúsculas) -> ESCANEO, que
    siempre lleva además el de IPs. Devuelve una línea para el resumen."""
    global SANEAR, ESCANEO
    if not os.path.isfile(ruta):
        SANEAR, ESCANEO = (), re.compile(ESCANEO_IP)
        return (f"AVISO: no hay {ruta}: solo el escaneo genérico de IPs, sin sustituciones "
                f"(formato: paquete/empaquetar.local.example.json)")
    with open(ruta, encoding="utf-8") as f:
        r = json.load(f)
    SANEAR = tuple((str(a), str(b)) for a, b in r.get("sanear", []))
    pats = [str(x) for x in r.get("escaneo", [])]
    ESCANEO = re.compile("|".join([*(f"(?:{x})" for x in pats), ESCANEO_IP]), re.I)
    return f"reglas personales: {ruta} ({len(SANEAR)} sustituciones, {len(pats)} patrones de escaneo)"


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _coincide(rel, pat):
    a, b = rel.split("/"), pat.split("/")
    return len(a) == len(b) and all(fnmatch.fnmatchcase(x, y) for x, y in zip(a, b))


def lista_servidor():
    """Ficheros del git (más los nuevos sin ignorar) de tools/yo2emu que entran por la lista blanca."""
    out = subprocess.run(["git", "-C", EMU, "ls-files", "-co", "--exclude-standard", "."], capture_output=True,
                         text=True, check=True, encoding="utf-8").stdout.splitlines()
    sel = sorted({r for r in out if any(_coincide(r, p) for p in SERVIDOR) and not any(_coincide(r, p) for p in FUERA)})
    faltan = [p for p in SERVIDOR if "*" not in p and p not in sel]
    if faltan:
        raise SystemExit(f"faltan en el git ficheros de la lista blanca: {faltan}")
    return sel


def _npc_ids(origen, destino):
    """npc_ids.json sin los diálogos de las NPC («lines», textos del juego) ni las notas («guess»): el servidor solo
    usa id, name, sex (yo2srv/mundo/npc.py, research/avatar)."""
    datos = json.load(open(origen, encoding="utf-8"))
    for d in datos:
        for k in ("lines", "lines_lang", "guess"):
            d.pop(k, None)
    with open(destino, "w", encoding="utf-8", newline="\n") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
        f.write("\n")


def _copiar(origen, destino):
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    shutil.copyfile(origen, destino)


def _descarga(nombre, sha):
    ruta = os.path.join(DESCARGAS, nombre)
    if not os.path.isfile(ruta):
        raise SystemExit(f"falta {ruta} (descarga aprobada: ver cabecera de este fichero)")
    if _sha(ruta) != sha:
        raise SystemExit(f"{ruta}: el sha256 no es el publicado ({sha})")
    return ruta


def montar_python():
    dst = os.path.join(ARBOL, "python")
    with zipfile.ZipFile(_descarga(PY_ZIP, PY_SHA)) as z:
        z.extractall(dst)
    # ._pth: sys.path EXACTO (el embeddable ignora PYTHONPATH/PYTHONHOME y no añade la carpeta del script)
    with open(os.path.join(dst, "python312._pth"), "w", encoding="ascii", newline="\r\n") as f:
        f.write("python312.zip\n.\n..\\server\\yo2emu\n..\\server\n")


def montar_node():
    dst = os.path.join(ARBOL, "node")
    os.makedirs(dst, exist_ok=True)
    with zipfile.ZipFile(_descarga(NODE_ZIP, NODE_SHA)) as z:
        base = NODE_ZIP[:-4] + "/"
        for n in ("node.exe", "LICENSE"):
            with z.open(base + n) as s, open(os.path.join(dst, n), "wb") as d:
                shutil.copyfileobj(s, d)


def montar_motor():
    sys.path.insert(0, AQUI)
    import bundle_duelcore
    r = bundle_duelcore.empaquetar(os.path.join(ARBOL, "engine", "duelcore.mjs"))
    # scripts de 2008 (duelcore/scripts_2008, sustituyen a los de CardScripts): junto al .mjs, que es donde duelcore.mts los
    # busca (o en YO2_SCRIPTS_2008_DIR). Solo los cNNNNN.lua (el LEEME.md se queda en el repo).
    origen = os.path.join(EMU, "duelcore", "scripts_2008")
    for f in sorted(os.listdir(origen)):
        if re.fullmatch(r"c\d+\.lua", f):
            _copiar(os.path.join(origen, f), os.path.join(ARBOL, "engine", "scripts_2008", f))
    return r["sha256"]


def montar_vendor():
    origen = os.path.join(ROOT, "_vendor")
    dst = os.path.join(ARBOL, "vendor")
    for n in ("cards.cdb", "strings.conf"):
        _copiar(os.path.join(origen, n), os.path.join(dst, n))
    cs = os.path.join(origen, "CardScripts")
    n = 0
    for d, dirs, files in os.walk(cs):
        dirs[:] = sorted(x for x in dirs if not x.startswith((".git", ".github")))
        for f in files:
            if f.startswith(".git"):
                continue
            src = os.path.join(d, f)
            _copiar(src, os.path.join(dst, "CardScripts", os.path.relpath(src, cs)))
            n += 1
    return n


def montar_servidor():
    rels = lista_servidor()
    dst = os.path.join(ARBOL, "server")
    for r in rels:
        destino = os.path.join(dst, "yo2emu", *r.split("/"))
        if r == "npc_ids.json":
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            _npc_ids(os.path.join(EMU, r), destino)
        else:
            _copiar(os.path.join(EMU, *r.split("/")), destino)
            if r.endswith(".py"):
                _sanear(destino)
    for n in DE_TOOLS:
        _copiar(os.path.join(TOOLS, n), os.path.join(dst, n))
        _sanear(os.path.join(dst, n))
    return rels


def _sanear(ruta):
    """SANEAR sobre la copia (binario: respeta CRLF/LF y la codificación)."""
    datos = open(ruta, "rb").read()
    nuevo = datos
    for a, b in SANEAR:
        nuevo = nuevo.replace(a.encode(), b.encode())
    if nuevo != datos:
        with open(ruta, "wb") as f:
            f.write(nuevo)


def comprobar_arbol():
    """NUNCA sobre todas las rutas; ESCANEO sobre el texto de server/ y los .txt de la raíz. Devuelve los avisos."""
    malos, avisos = [], []
    for d, _, files in os.walk(ARBOL):
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), ARBOL).replace("\\", "/")
            sin_cs = rel if not rel.startswith("vendor/CardScripts/") else ""
            if sin_cs and NUNCA.search(rel):
                malos.append(rel)
            if rel.startswith("server/") or (rel.count("/") == 0 and rel.endswith(".txt")):
                try:
                    texto = open(os.path.join(d, f), encoding="utf-8").read()
                except (UnicodeDecodeError, OSError):
                    continue
                for m in ESCANEO.finditer(texto):
                    clave = (rel, m.group(0))
                    if clave not in ESCANEO_OK:
                        linea = texto.count("\n", 0, m.start()) + 1
                        avisos.append(f"{rel}:{linea}: {m.group(0)}")
    return malos, avisos


def _parte(rel):
    """La parte del paquete de una ruta (como sala.ParteDeRuta; firmar.exe vuelve a comprobarlo todo)."""
    segs = rel.split("/")
    if len(segs) == 1:
        if rel in (EXE_LANZADOR, EXE_LANZADOR + ".firma"):
            return "lanzador"
        if rel.lower().endswith((".txt", ".md")):
            return "raiz"
        raise SystemExit(f"PARADO: fichero no permitido en la raíz del paquete: {rel}")
    if segs[0] not in PARTES_DIR:
        raise SystemExit(f"PARADO: carpeta no permitida en el paquete: {rel}")
    return segs[0]


def firmar_red(firmar, nodos):
    """La lista de nodos de arranque/relays firmada (firmar.exe -red): va como «red» en el manifiesto (sala/red_nodos.go)."""
    os.makedirs(BUILD, exist_ok=True)
    salida = os.path.join(BUILD, "red.firmada.json")
    if os.path.exists(salida):
        os.remove(salida)
    _firmar_exe(firmar, "-red", "-salida", salida, nodos)
    return json.load(open(salida, encoding="utf-8"))


def escribir_manifiesto(version, red=None):
    """paquete.manifiesto.json: lo que firma firmar.exe -paquete (bytes exactos: JSON compacto, ASCII)."""
    firma = json.load(open(os.path.join(ARBOL, EXE_LANZADOR + ".firma"), encoding="utf-8"))
    fs = []
    for d, dirs, files in os.walk(ARBOL):
        dirs.sort()
        if "__pycache__" in dirs:
            raise SystemExit(f"PARADO: __pycache__ dentro del árbol ({d})")
        for f in sorted(files):
            p = os.path.join(d, f)
            rel = os.path.relpath(p, ARBOL).replace("\\", "/")
            if rel in (MANIFIESTO, FIRMADO):
                continue
            fs.append({"ruta": rel, "parte": _parte(rel), "sha256": _sha(p), "tam": os.path.getsize(p)})
    fs.sort(key=lambda x: x["ruta"])
    m = {"v": 1, "producto": "yo2online-paquete", "version": int(version), "version_lanzador": int(firma["version"]),
         "creado": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ficheros": fs}
    if red:
        m["red"] = red
    with open(os.path.join(ARBOL, MANIFIESTO), "wb") as f:
        f.write(json.dumps(m, ensure_ascii=True, separators=(",", ":")).encode("ascii"))
    partes = {}
    for x in fs:
        n, b = partes.get(x["parte"], (0, 0))
        partes[x["parte"]] = (n + 1, b + x["tam"])
    return partes


def _firmar_exe(firmar, *args):
    r = subprocess.run([firmar, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("  " + (r.stdout + r.stderr).strip().replace("\n", "\n  "))
    if r.returncode != 0:
        raise SystemExit(f"PARADO: {os.path.basename(firmar)} {' '.join(args)} falló")


def instalar_semilla(destino):
    """Copia el árbol firmado a destino (enlaces duros si se puede): de ahí lo reparte el lanzador del editor."""
    nuevo, viejo = destino + ".nueva", destino + ".vieja"
    for x in (nuevo, viejo):
        if os.path.isdir(x):
            shutil.rmtree(x)

    def enlazar(src, dst):
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)
    shutil.copytree(ARBOL, nuevo, copy_function=enlazar)
    if os.path.isdir(destino):
        os.rename(destino, viejo)
    os.rename(nuevo, destino)
    shutil.rmtree(viejo, ignore_errors=True)


def comprimir(version):
    zpath = os.path.join(BUILD, f"{RAIZ_ZIP}-{version}.zip")
    if os.path.exists(zpath):
        os.remove(zpath)
    n = 0
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d, dirs, files in os.walk(ARBOL):
            dirs.sort()
            if "__pycache__" in dirs:
                dirs.remove("__pycache__")
            for f in sorted(files):
                src = os.path.join(d, f)
                z.write(src, RAIZ_ZIP + "/" + os.path.relpath(src, ARBOL).replace("\\", "/"))
                n += 1
    return zpath, n


def main():
    ap = argparse.ArgumentParser(description="Monta paquete/_build/YO2Server y el zip para el Discord")
    ap.add_argument("--lanzador", required=True, help="YO2OnlineLauncher.exe compilado con app\\build.ps1 -Salida (sin firmar)")
    ap.add_argument("--version", default=time.strftime("%Y%m%d%H%M%S", time.gmtime()),
                    help="versión del paquete (número creciente; por defecto la fecha UTC AAAAMMDDhhmmss)")
    ap.add_argument("--sin-zip", action="store_true")
    ap.add_argument("--firmar", nargs="?", const=FIRMAR_EXE, default=None, metavar="FIRMAR_EXE",
                    help="firmar la versión con firmar.exe (por defecto launcher\\firmar.exe, de build.ps1 -Firmar)")
    ap.add_argument("--semilla", default=os.path.join(os.environ.get("LOCALAPPDATA", ""), "YO2Online", "semilla"),
                    help="con --firmar: dónde se copia el árbol firmado para repartirlo (lanzador del editor)")
    ap.add_argument("--sin-semilla", action="store_true", help="con --firmar: no copiar el árbol a --semilla")
    ap.add_argument("--red", default=NODOS_RED,
                    help="con --firmar: lista de nodos de arranque/relays que se firma y viaja en el manifiesto")
    ap.add_argument("--sin-red", action="store_true", help="con --firmar: el manifiesto sin lista de nodos")
    ap.add_argument("--reglas", default=REGLAS_LOCAL,
                    help="reglas personales de sustitución y escaneo (por defecto paquete/empaquetar.local.json)")
    a = ap.parse_args()
    if not a.version.isdigit() or int(a.version) <= 0:
        raise SystemExit("--version tiene que ser un número (AAAAMMDDhhmmss)")
    if a.firmar and not os.path.isfile(a.firmar):
        raise SystemExit(f"falta {a.firmar}: compílalo con launcher\\app\\build.ps1 -Firmar")
    t0 = time.time()
    print(cargar_reglas(a.reglas))
    if os.path.isdir(ARBOL):
        shutil.rmtree(ARBOL)
    os.makedirs(ARBOL)
    montar_python()
    montar_node()
    mjs = montar_motor()
    ncs = montar_vendor()
    rels = montar_servidor()
    if EXE_LANZADOR.lower() == EXE_KONAMI or RAIZ_ZIP.lower() + ".exe" == EXE_KONAMI:
        raise SystemExit(f"PARADO: el lanzador no puede llamarse como el de Konami ({EXE_KONAMI})")
    _copiar(a.lanzador, os.path.join(ARBOL, EXE_LANZADOR))
    if a.firmar:
        if os.path.isfile(a.lanzador + ".firma"):
            _copiar(a.lanzador + ".firma", os.path.join(ARBOL, EXE_LANZADOR + ".firma"))
        else:
            _firmar_exe(a.firmar, os.path.join(ARBOL, EXE_LANZADOR))   # le pregunta la versión al exe
    for n in ("README.txt", "THIRD-PARTY.txt"):
        _copiar(os.path.join(AQUI, n), os.path.join(ARBOL, n))
    malos, avisos = comprobar_arbol()
    print(f"árbol: {ARBOL}\n  servidor: {len(rels)} ficheros de tools/yo2emu + {len(DE_TOOLS)} de tools/\n"
          f"  vendor: CardScripts {ncs} ficheros\n  motor: duelcore.mjs sha256 {mjs}")
    if malos:
        raise SystemExit("PARADO: ficheros prohibidos en el paquete:\n  " + "\n  ".join(malos))
    if avisos:
        raise SystemExit("PARADO: el escaneo encontró rutas/IPs/cuentas (revisar y quitar, o aceptar en ESCANEO_OK):\n  "
                         + "\n  ".join(avisos))
    total = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(ARBOL) for f in fs)
    print(f"  total sin comprimir: {total / 1e6:.1f} MB")
    if a.firmar:
        red = None
        if not a.sin_red and os.path.isfile(a.red):
            red = firmar_red(a.firmar, a.red)
            print(f"  nodos de red: versión {red['version']}, {len(red['arranque'])} de arranque, {len(red.get('relays') or [])} relays")
        partes = escribir_manifiesto(a.version, red)
        print(f"  versión {a.version}: " + ", ".join(f"{k} {n} ({b / 1e6:.1f} MB)" for k, (n, b) in sorted(partes.items())))
        _firmar_exe(a.firmar, "-paquete", ARBOL)
        if not os.path.isfile(os.path.join(ARBOL, FIRMADO)) or os.path.exists(os.path.join(ARBOL, MANIFIESTO)):
            raise SystemExit("PARADO: firmar.exe no dejó paquete.json")
        if not a.sin_semilla and a.semilla:
            instalar_semilla(a.semilla)
            print(f"  semilla: {a.semilla} (el lanzador del editor la reparte al hospedar; no hace falta reiniciarlo)")
    else:
        print("  AVISO: SIN versión firmada (--firmar): este zip no se actualiza solo ni actualiza a nadie")
    if not a.sin_zip:
        z, n = comprimir(a.version)
        print(f"zip: {z} ({n} ficheros, {os.path.getsize(z) / 1e6:.1f} MB, sha256 {_sha(z)})")
    print(f"listo en {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
