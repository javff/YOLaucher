"""Contrato del namespace de run.py (REESTRUCTURA.md §3.4, paso P0c; T10 de la batería).

    python paridad/test_contrato.py [--banda 1900 | --offset N]     # comprueba contrato_ns.txt (sale 1 si falta algo)
    python paridad/test_contrato.py --extraer [--banda 1900]         # vuelve a sacar los nombres y reescribe el fichero

Los globals() de run.py son el «espacio compartido» de todo el sistema: la consola 5799 (admin.py) ejecuta ahí cada
línea, los hot_*/reload_*/*_live.py se cargan con exec(..., globals()), el panel y research/* reciben el MISMO dict
(NS, ns, self.env) y ocg_test/pvp_test/barrido ejecutan el texto de run.py en un dict y leen de él. Los nombres que
usan todos ellos son un CONTRATO: la reestructura puede mover el código, pero cada nombre tiene que seguir existiendo
en el namespace y con el mismo tipo.

--extraer saca los candidatos del código y la documentación (sin ejecutar nada de lo analizado):
  A. `NS[...]`, `NS.get(...)`, `ns[...]`, `self.env[...]`, `env[...]`, `g[...]`, `G[...]`, `globals()[...]` /
     `.get/.setdefault/.pop`, `"X" in NS` en el código que no es prueba (panel, research/*, pvp_*, run.py, recargas,
     ocg_test/pvp_test, barrido);
  B. nombres globales LIBRES (symtable) de los scripts que se ejecutan con exec(..., globals()): hot_*.py, reload_*.py,
     *_live.py y los del scratchpad citados en HANDOFF/NOTES (chk_nombres.py, set_lv*.py);
  C. líneas de consola: `python admin.py "..."` en HANDOFF/NOTES/*.md y en los comentarios, y las cadenas que las pruebas
     mandan con console(...)/cmd(...) a sus instancias;
  D. banderas del panel (`("ns", X)`, `("nsdict", X, k)`, `("mod", M, A)`): además del nombre, la ruta `M.A` / `X[k]`;
  E. la tabla «Globals compartidos clave» del plan (REESTRUCTURA.md §1.4).
`env`, `g`, `G`... solo cuentan en los ficheros donde son el namespace (salen de globals()/self.env/ns, o son el dict de
un exec(texto_de_run_py, g)); en el resto suelen ser os.environ o un dict local.
Luego arranca una instancia de PRUEBA propia (estado congelado de _fixtures, almacén sqlite sin cifrar con la clave en
su carpeta, panel encendido, sin cliente; igual que el vivo salvo eso) y se queda con los candidatos que EXISTEN, con su
tipo. Los que no existen (opcionales con .get, nombres que crea la propia recarga, documentación vieja) van al final del
fichero comentados, para revisarlos a mano: no se comprueban.

La comprobación arranca otra instancia de prueba igual y, por la consola de ESA instancia (token en
%LOCALAPPDATA%\\YO2Online\\consola_<puerto>.txt, solo contesta «ok»/«error»), ejecuta un volcado a fichero de
{nombre: tipo} de todo el namespace y de las rutas de las banderas; compara con el contrato. Nunca el vivo: offset 0,
3000 y los que pisan 5730-5799/8730/8790 prohibidos (comun.offset_valido) y puertos comprobados libres antes.
"""
import argparse
import ast
import builtins
import glob
import json
import keyword
import os
import re
import socket
import symtable
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import comun  # noqa: E402

CONTRATO = os.path.join(comun.AQUI, "contrato_ns.txt")
ESTADO = os.path.join(comun.FIX, "state-paridad.json")
NO_MIRAR = {"_out", "logs", "node_modules", "_vendor", "__pycache__", "_original", "out", "brain_out", ".git"}
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__builtins__", "__doc__", "__spec__", "__loader__",
                                 "__package__", "__path__", "__cached__", "__annotations__", "__dict__", "__class__"}

# A: variables que en algún fichero son el namespace de run.py (o el dict donde ocg_test/pvp_test/barrido ejecutan su
# texto). `env`/`g`/`G` también son a veces os.environ u otro dict: el filtro de existencia de la instancia los quita.
HOLDER = r"(?<![\w.])(NS|ns|_NS|P\.NS|panel\.NS|ADMIN_WEB\.NS|self\.env|self\.ns|env|_env|G|g|_g|globals\(\))"
RE_A = re.compile(HOLDER + r"\s*(?:\[|\.get\(|\.setdefault\(|\.pop\()\s*[\"']([A-Za-z_]\w*)[\"']")
RE_A_IN = re.compile(r"[\"']([A-Za-z_]\w*)[\"']\s+(?:not\s+)?in\s+" + HOLDER + r"(?![\w.\[(])")
AMBIGUOS = {"env", "_env", "G", "g", "_g"}          # a veces son os.environ o un dict local cualquiera


def _es_namespace(h, src):
    """¿La variable ambigua h es, en este fichero, el namespace de run.py (o el dict donde se ejecuta su texto)?"""
    h = re.escape(h)
    return bool(re.search(rf"\b{h}\s*=\s*(?:globals\(\)|self\.env\b|self\.ns\b|NS\b|ns\b)", src)
                or re.search(rf"exec\([^\n]*,\s*{h}\s*[,)]", src)
                or re.search(rf"(?:Hub|Matches|NpcWatch)\(\s*{h}\b|def \w+\(self,\s*{h}\b|def install\w*\(\s*{h}\b", src)
                # hot_*: «def _hot_x(G): ...» + «_hot_x(globals())»
                or (re.search(rf"def \w+\(\s*{h}\s*[,)]", src) and re.search(r"\w\(\s*globals\(\)\s*[,)]", src)))


def _nombres_a(src):
    """Nombres leídos del namespace con NS[...]/ns.get(...)/«"X" in g»... (las variables ambiguas, solo si lo son)."""
    vale = {}
    for rx, ih, i_n in ((RE_A, 1, 2), (RE_A_IN, 2, 1)):
        for m in rx.finditer(src):
            h, n = m.group(ih), m.group(i_n)
            if h in AMBIGUOS:
                if h not in vale:
                    vale[h] = _es_namespace(h, src)
                if not vale[h]:
                    continue
            yield n


# C: líneas de consola documentadas
RE_ADMIN = [re.compile(r'admin\.py\s+"((?:[^"\\\n]|\\.)*)"'), re.compile(r"admin\.py\s+'((?:[^'\\\n]|\\.)*)'")]
FUNCS_CONSOLA = {"console", "console_cmd", "cmd", "_cmd", "run_cmd", "consola", "_consola", "admin_line"}
# D: banderas del panel
RE_FLAG = re.compile(r"\(\s*\"(ns|nsdict|mod)\"\s*,\s*\"(\w+)\"(?:\s*,\s*\"(\w+)\")?\s*\)")


# ------------------------------------------------------------------------------------------------ extracción
def _ficheros(ext):
    for raiz, dirs, fichs in os.walk(comun.EMU):
        dirs[:] = [d for d in dirs if d not in NO_MIRAR and not d.startswith(".")]
        if os.path.abspath(raiz).startswith(comun.AQUI):
            continue                                        # paridad/ no es consumidor del contrato
        for f in fichs:
            if f.endswith(ext) and not f.startswith("_run_"):
                yield os.path.join(raiz, f)


def _rel(p):
    try:
        r = os.path.relpath(p, comun.EMU)
    except ValueError:
        r = p
    if r.startswith(".."):
        r = "scratchpad/" + os.path.basename(p)
    return r.replace("\\", "/")


def _leer(p):
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def _globales_libres(code):
    """Nombres globales que el código LEE sin definirlos él (lo que tiene que darle el namespace donde se ejecuta).
    None si no compila."""
    try:
        top = symtable.symtable(code, "<contrato>", "exec")
    except (SyntaxError, ValueError):
        return None
    propios = {s.get_name() for s in top.get_symbols()
               if s.is_assigned() or s.is_imported() or s.is_namespace()}
    libres = set()

    def recorrer(t, es_top):
        for s in t.get_symbols():
            n = s.get_name()
            if n in propios or n in BUILTINS or not s.is_referenced():
                continue
            if es_top or s.is_global() or s.is_declared_global():
                libres.add(n)
        for h in t.get_children():
            recorrer(h, False)
    recorrer(top, True)
    return libres


def _nombres_sueltos(code):
    """Plan B para líneas documentadas que no compilan (placeholders <id>, «…»): identificadores que no van tras un punto,
    sin el texto entre comillas ni palabras clave."""
    code = re.sub(r"([\"']).*?\1", "''", code)
    out = set()
    for m in re.finditer(r"(?<![\w.])([A-Za-z_]\w*)", code):
        n = m.group(1)
        if not keyword.iskeyword(n) and n not in BUILTINS:
            out.add(n)
    return out


def _texto_de(nodo):
    """Texto aproximado de una cadena construida en el código (f-string, +, %, .format): los huecos valen «0»."""
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return nodo.value
    if isinstance(nodo, ast.JoinedStr):
        partes = []
        for v in nodo.values:
            partes.append(v.value if isinstance(v, ast.Constant) else "0")
        return "".join(partes)
    if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add):
        a, b = _texto_de(nodo.left), _texto_de(nodo.right)
        return a + b if a is not None and b is not None else (a or b)
    if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Mod):
        a = _texto_de(nodo.left)
        return re.sub(r"%[-#0 +]*\d*(?:\.\d+)?[sdrfxi]", "0", a) if a is not None else None
    if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) and nodo.func.attr == "format":
        a = _texto_de(nodo.func.value)
        return re.sub(r"\{[^{}]*\}", "0", a) if a is not None else None
    return None


def _lineas_consola(src):
    """Cadenas de código que una prueba manda a la consola de su instancia: console(off, "...") y parecidos."""
    try:
        arbol = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(arbol):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        nombre = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
        if nombre not in FUNCS_CONSOLA:
            continue
        for arg in n.args:
            t = _texto_de(arg)
            if t and t.strip():
                out.append(t)
    return out


def _scratchpad_citados():
    """Scripts del scratchpad citados en HANDOFF/NOTES (líneas que dicen «scratchpad» y nombran un .py): se buscan en los
    scratchpads de las sesiones del proyecto (YO2_SCRATCH\\*\\*\\scratchpad)."""
    nombres = set()
    for md in _ficheros(".md"):
        for linea in _leer(md).splitlines():
            if "scratchpad" in linea.lower():
                nombres |= set(re.findall(r"([\w-]+\.py)\b", linea))
    raiz = os.environ.get("YO2_SCRATCH") or os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp", "yo2")   # no TEMP: la batería lo cambia
    hallados = {}
    for n in sorted(nombres):
        if glob.glob(os.path.join(comun.EMU, "**", n), recursive=True):
            continue                                        # ya está en el árbol (p. ej. ocg_test.py): no es del scratchpad
        for p in sorted(glob.glob(os.path.join(raiz, "*", "*", "scratchpad", n))):
            hallados.setdefault(n, p)
    return hallados


def extraer_candidatos():
    """{nombre o ruta: {fuentes}} sin filtrar."""
    cand = {}

    def add(n, fuente):
        if n and n not in BUILTINS and not keyword.iskeyword(n):
            cand.setdefault(n, set()).add(fuente)

    exec_scripts = []
    for p in _ficheros(".py"):
        rel = _rel(p)
        src = _leer(p)
        base = os.path.basename(p)
        es_prueba = base.startswith("test_")
        # B: scripts que se ejecutan dentro del namespace (lo dice su cabecera: exec(open(...).read(), globals()))
        if (base.startswith(("hot_", "reload_")) or base.endswith("_live.py")) and "globals())" in "\n".join(
                src.splitlines()[:30]):
            exec_scripts.append(p)
        # A
        if not es_prueba:
            for n in _nombres_a(src):
                add(n, f"A:{rel}")
        # C (pruebas y herramientas): cadenas mandadas a la consola de su instancia
        for linea in _lineas_consola(src):
            libres = _globales_libres(linea)
            for n in (libres if libres is not None else set()):
                add(n, f"C:{rel}")
        # C (comentarios de cabecera de los .py): python admin.py "..."
        for rx in RE_ADMIN:
            for m in rx.finditer(src):
                linea = m.group(1).replace('\\"', '"').replace("\\'", "'")
                libres = _globales_libres(linea)
                for n in (libres if libres is not None else _nombres_sueltos(linea)):
                    add(n, f"C:{rel}")
        # D
        if rel == "research/admin/panel.py":
            for m in RE_FLAG.finditer(src):
                kind, a, b = m.groups()
                add(a, f"D:{rel}")
                if kind == "mod" and b:
                    add(f"{a}.{b}", f"D:{rel}")
                elif kind == "nsdict" and b:
                    add(f"{a}[{b}]", f"D:{rel}")
    # C: documentación
    for md in _ficheros(".md"):
        rel = _rel(md)
        src = _leer(md)
        for rx in RE_ADMIN:
            for m in rx.finditer(src):
                linea = m.group(1).replace('\\"', '"').replace("\\'", "'")
                libres = _globales_libres(linea)
                for n in (libres if libres is not None else _nombres_sueltos(linea)):
                    add(n, f"C:{rel}")
    # E: «Globals compartidos clave» del plan (REESTRUCTURA.md §1.4; p. ej. DEADLINE, que la consola sube a mano)
    plan = _leer(os.path.join(comun.EMU, "REESTRUCTURA.md"))
    i, j = plan.find("### 1.4"), plan.find("### 1.5")
    if 0 <= i < j:
        for linea in plan[i:j].splitlines():
            if linea.startswith("| `"):
                for n in re.findall(r"`([A-Za-z_]\w*)`", linea.split("|")[1]):
                    add(n, "E:REESTRUCTURA.md §1.4")
    # B: scratchpad citado en la documentación
    for n, p in _scratchpad_citados().items():
        exec_scripts.append(p)
    for p in exec_scripts:
        rel = _rel(p)
        src = _leer(p)
        libres = _globales_libres(src) or set()
        for n in libres:
            add(n, f"B:{rel}")
        for n in _nombres_a(src):
            add(n, f"B:{rel}")
    return cand


# ------------------------------------------------------------------------------------------------ instancia
VOLCADO = r'''
import json
def _tipo(v):
    return type(v).__name__
res = {"ns": {k: _tipo(v) for k, v in list(NS.items())}, "rutas": {}}
for r in RUTAS:
    try:
        if r.endswith("]"):
            n, k = r[:-1].split("[", 1)
            v = NS[n][k]
        else:
            n, a = r.split(".", 1)
            v = NS[n]
            for p in a.split("."):
                v = getattr(v, p)
        res["rutas"][r] = _tipo(v)
    except Exception:
        res["rutas"][r] = None
with open(SALIDA, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=0)
'''


def _consola(off, linea):
    port = 5799 + off
    tok = open(os.path.join(os.environ.get("LOCALAPPDATA", ""), "YO2Online", f"consola_{port}.txt"),
               encoding="ascii").read().strip()
    s = socket.create_connection(("127.0.0.1", port), timeout=30)
    try:
        s.sendall(("AUTH " + tok + "\n" + linea + "\n").encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        return s.recv(65536).decode("utf-8", "replace").strip()
    finally:
        s.close()


def elegir_offset(banda):
    for k in range(20):
        off = banda + 10 * k
        if not comun.offset_valido(off) and not comun.ocupados(off):
            return off
    sys.exit(f"no queda ningún offset libre en la banda {banda}")


def volcar(off, rutas):
    """Arranca una instancia de prueba en off y devuelve {"ns": {nombre: tipo}, "rutas": {ruta: tipo|None}}."""
    if not os.path.exists(ESTADO):
        sys.exit(f"falta {ESTADO} (python paridad/grabar.py --preparar)")
    carpeta = os.path.join(comun.OUT, "contrato", f"inst-{off}")
    if os.path.isdir(carpeta):
        import shutil
        shutil.rmtree(carpeta, ignore_errors=True)
    os.makedirs(carpeta, exist_ok=True)
    st = os.path.join(carpeta, "state-prueba.json")
    entorno = {
        # como el vivo (almacén sqlite, panel encendido), pero sin cifrar y con la clave y el token en su carpeta
        "YO2_STORAGE": "sqlite", "YO2_DB_CRYPT": "off", "YO2_DB": st + ".sqlite",
        "YO2_DB_KEYFILE": os.path.join(carpeta, "db.key"), "YO2_ADMIN_PANEL": "1",
        "YO2_ADMIN_TOKEN_FILE": os.path.join(carpeta, "admin_token.txt"),
    }
    script = os.path.join(carpeta, "volcado.py")
    salida = os.path.join(carpeta, "volcado.json")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(VOLCADO)
    linea = (f"exec(compile(open({script!r}, encoding='utf-8').read(), 'contrato', 'exec'), "
             f"{{'NS': globals(), 'SALIDA': {salida!r}, 'RUTAS': {sorted(rutas)!r}}})")
    with comun.Instancia(off, carpeta, ESTADO, entorno, espera_duelcore=4.0):
        r = _consola(off, linea)
        if r != "ok":
            sys.exit(f"consola de la instancia de prueba ({5799 + off}): {r}")
        for _ in range(50):
            if os.path.exists(salida):
                break
            time.sleep(0.1)
    return comun.leer_json(salida)


# ------------------------------------------------------------------------------------------------ fichero
def escribir_contrato(cand, vol, off):
    presentes, ausentes = [], []
    for n in sorted(cand, key=lambda x: (x.lower(), x)):
        fuentes = sorted(cand[n])
        t = vol["rutas"].get(n) if ("." in n or n.endswith("]")) else vol["ns"].get(n)
        (presentes if t else ausentes).append((n, t, fuentes))

    def fuentes_txt(fs):
        cortas = sorted({f.split(":", 1)[0] + ":" + f.split(":", 1)[1] for f in fs})
        return ", ".join(cortas[:4]) + (f" (+{len(cortas) - 4})" if len(cortas) > 4 else "")

    cab = [
        "# Contrato del namespace de run.py (REESTRUCTURA.md §3.4 P0c). Lo comprueba paridad/test_contrato.py (T10).",
        "# Cada línea: NOMBRE<TAB>TIPO<TAB>quién lo usa. NOMBRE = global de run.py; «M.A» = atributo de un módulo del",
        "# namespace y «X[k]» = clave de un dict del namespace (banderas del panel). TIPO = type(valor).__name__ en una",
        "# instancia de prueba recién arrancada (sqlite, panel encendido, sin cliente).",
        "# Fuentes: A = NS[...]/ns[...]/self.env[...]/g[...] en el código; B = nombre libre de un script que se ejecuta con",
        "# exec(..., globals()) (hot_*, reload_*, *_live, scratchpad); C = línea de consola (admin.py en la documentación o",
        "# console(...) de una prueba); D = bandera del panel; E = tabla §1.4 del plan.",
        f"# Generado con «python paridad/test_contrato.py --extraer» el {time.strftime('%Y-%m-%d %H:%M')} "
        f"(offset {off}). {len(presentes)} nombres comprobados; {len(ausentes)} candidatos ausentes en la base (al final,",
        "# comentados: no se comprueban). Si un paso QUITA o cambia de tipo un nombre a propósito, se edita esta línea a",
        "# mano con el motivo en un comentario; si aparece un consumidor nuevo, se vuelve a extraer.",
        "",
    ]
    cuerpo = [f"{n}\t{t}\t{fuentes_txt(fs)}" for n, t, fs in presentes]
    pie = ["", "# ---- Candidatos AUSENTES en la base (no se comprueban): opcionales leídos con .get/try, nombres que crea la",
           "# ---- propia recarga o la prueba, variables de entorno o de otros dicts que el patrón confunde, documentación vieja.",
           ] + [f"# {n}\t-\t{fuentes_txt(fs)}" for n, t, fs in ausentes]
    with open(CONTRATO, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(cab + cuerpo + pie) + "\n")
    return presentes, ausentes


def leer_contrato():
    out = {}
    for linea in open(CONTRATO, encoding="utf-8"):
        linea = linea.rstrip("\n")
        if not linea.strip() or linea.startswith("#"):
            continue
        partes = linea.split("\t")
        out[partes[0]] = partes[1] if len(partes) > 1 else ""
    return out


# ------------------------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extraer", action="store_true", help="reescribe contrato_ns.txt")
    ap.add_argument("--banda", type=int, default=1900, help="primer offset libre de banda + 10·k (k < 20)")
    ap.add_argument("--offset", type=int, default=None)
    ap.add_argument("--candidatos", action="store_true", help="solo imprime los candidatos (sin instancia)")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.candidatos:
        cand = extraer_candidatos()
        for n in sorted(cand):
            print(f"{n}\t{', '.join(sorted(cand[n])[:3])}")
        print(f"{len(cand)} candidatos")
        return 0
    if a.offset is not None:
        motivo = comun.offset_valido(a.offset)
        if motivo:
            sys.exit(motivo)
        off = a.offset
    else:
        off = elegir_offset(a.banda)
    if a.extraer:
        cand = extraer_candidatos()
        rutas = [n for n in cand if "." in n or n.endswith("]")]
        vol = volcar(off, rutas)
        pres, aus = escribir_contrato(cand, vol, off)
        print(f"== contrato: {len(cand)} candidatos -> {len(pres)} nombres en {CONTRATO}; {len(aus)} ausentes (comentados)")
        return 0
    if not os.path.exists(CONTRATO):
        sys.exit(f"falta {CONTRATO} (python paridad/test_contrato.py --extraer)")
    contrato = leer_contrato()
    rutas = [n for n in contrato if "." in n or n.endswith("]")]
    print(f"== contrato del namespace: {len(contrato)} nombres, instancia de prueba en el offset {off}")
    vol = volcar(off, rutas)
    mal = []
    for n, tipo in sorted(contrato.items()):
        ahora = vol["rutas"].get(n) if n in rutas else vol["ns"].get(n)
        if ahora is None:
            mal.append(n)
            print(f"  MAL {n}: falta en el namespace (contrato: {tipo})")
        elif ahora != tipo:
            mal.append(n)
            print(f"  MAL {n}: tipo {ahora} (contrato: {tipo})")
    bien = len(contrato) - len(mal)
    print(f"  {'OK ' if not mal else 'MAL'} contrato: {bien}/{len(contrato)} nombres presentes y del mismo tipo")
    print(f"== RESULTADO contrato: {bien}/{len(contrato)} bien" + (f"; mal: {mal}" if mal else ""))
    return 0 if not mal else 1


if __name__ == "__main__":
    sys.exit(main())
