"""Genera yo2srv/BANDERAS.md desde el registro de config.py (REESTRUCTURA.md §2.6, paso R2-corto).

    python -m yo2srv.gen_banderas            # reescribe yo2srv/BANDERAS.md
    python -m yo2srv.gen_banderas --check    # no escribe; sale 1 si el fichero no está al día (lo vigila test_config.py)

No se edita a mano: cualquier cambio de `vanilla`, `enhanced`, `origen` o `desc` en config.py se refleja aquí al regenerar.
Arriba van las «inventadas encendidas en vanilla» (origen "inventado" con un valor distinto de apagado/0/None): candidatas
a pasar a `enhanced`. Las ideas nuestras que no son una bandera del registro (porque no tienen clave en
DEFAULT_CFG ni constante propia) van en NO_REGISTRADAS, con su fuente.
"""
import os
import sys

if __package__ in (None, ""):                       # python yo2srv/gen_banderas.py
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from yo2srv import config as C
else:
    from . import config as C

SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "BANDERAS.md")

# Ideas «inventadas» que hoy NO son una bandera del registro: (qué, dónde vive, cómo se apaga / estado en vanilla)
NO_REGISTRADAS = (
    ("chain_event_p2", "research/traductor/translator.py `_chain_priority`: `cfg.get(\"chain_event_p2\", True)`; la clave NO está en "
     "DEFAULT_CFG (por eso no es una bandera del registro)",
     "ENCENDIDA en vanilla (P2 2 si alguna opción es un efecto con evento); `cfg[\"chain_event_p2\"] = False` deja la tabla pura"),
    ("Aviso «Mover» (texto 5092)", "lado del cliente (Frida): `launcher/aviso_sala_hook.js` y copia en `net_hook.js` "
     "(research/lobby/NOTES.md «Aviso al pulsar MOVE con sala abierta»)",
     "APAGADO en vanilla: solo con el fichero `yo2-aviso-sala.txt` junto a yo2.exe o `YO2_AVISO_SALA=1`"),
    ("Tamaño de ventana del cliente (Ajustes → Display → «Window size»)", "lado del cliente (DLL propia): `launcher/juego/dll/yo2net.c` (ventana ampliada, "
     "cuentas en `yo2win.h`), valor en `lanzador.json` («ventana») y `YO2ONLINE_WINDOW` (rama rx/PAN)",
     "APAGADO en vanilla: «Original (1024x768)»; 1.5x / 2x / «Fit screen» solo si el jugador lo elige en el lanzador"),
    ("«Mirar» un duelo contra una NPC", "`npc_watch.py` (research/lobby/NOTES.md «C2»): cuelga de `hub.NPC_DUEL_STATE` y "
     "`lobby.WATCH`, sin bandera propia", "ENCENDIDO en vanilla mientras esas dos lo estén"),
    ("Torpeza de la IA de las NPC", "`research/ia/brain.py` (`NpcProfile.torpeza`, por nivel de la NPC; research/ia/BRAIN.md): "
     "parte de `ocg.USE_BRAIN`, sin bandera propia", "ENCENDIDA en vanilla mientras `ocg.USE_BRAIN` lo esté"),
)


def _apagada(v):
    return v is None or v is False or v == 0 or v == "" or v is C.DERIVADA


def _v(v):
    if v is C.DERIVADA:
        return "DERIVADA"
    if v is C.SIN:
        return "= vanilla"
    return repr(v)


def _celda(txt):
    return str(txt).replace("|", "\\|").replace("\n", " ")


def generar():
    reg = C.REGISTRO
    por_origen = {o: [f for f in reg if f.origen == o] for o in C.ORIGENES}
    inv_on = [f for f in por_origen["inventado"] if not _apagada(f.vanilla)]
    inv_off = [f for f in por_origen["inventado"] if _apagada(f.vanilla)]
    L = []
    w = L.append
    w("# Banderas del servidor (generado)")
    w("")
    w("> Generado por `python -m yo2srv.gen_banderas` desde `yo2srv/config.py`. **No se edita a mano**: cambia el registro y regenera.")
    w("")
    w(f"{len(reg)} banderas. Perfil por defecto: `vanilla` (`YO2_PERFIL=vanilla|enhanced`). Hoy `enhanced` = `vanilla` en todas "
      "(`enhanced` aparece como «= vanilla»). Precedencia al arrancar: perfil → `admin_banderas.json` → variable de entorno `YO2_*` → "
      "cambio en caliente.")
    w("")
    w("| Origen | Banderas | Qué significa |")
    w("|---|---:|---|")
    sig = {"oficial": "como el original (C/P en las notas) o intento de serlo",
           "inventado": "nuestro, aprobado y detrás de una bandera (no es 1:1)",
           "tecnico": "sin efecto de juego: rutas, puertos, límites, logs, pruebas, seguridad",
           "?": "sin decidir (ver más abajo)"}
    for o in C.ORIGENES:
        w(f"| `{o}` | {len(por_origen[o])} | {sig[o]} |")
    w("")
    w("## Inventadas encendidas en vanilla (candidatas a pasar a enhanced)")
    w("")
    w("Banderas de origen `inventado` cuyo valor en vanilla NO es «apagado» (y por tanto el Discord de restauración las vería).")
    w("")
    w("| id | vanilla | desc | nota |")
    w("|---|---|---|---|")
    for f in inv_on:
        w(f"| `{f.id}` | `{_celda(_v(f.vanilla))}` | {_celda(f.desc)} | {_celda(f.nota)} |")
    w("")
    w("### Inventadas que no son una bandera del registro")
    w("")
    w("| qué | dónde vive | estado en vanilla |")
    w("|---|---|---|")
    for q, donde, estado in NO_REGISTRADAS:
        w(f"| {_celda(q)} | {_celda(donde)} | {_celda(estado)} |")
    w("")
    if inv_off:
        w("### Inventadas apagadas en vanilla (no molestan)")
        w("")
        w(", ".join(f"`{f.id}` ({_celda(_v(f.vanilla))})" for f in inv_off))
        w("")
    w("## Origen dudoso (`?`)")
    w("")
    w("Sin evidencia clara en las notas (`research/**/NOTES.md`, `HANDOFF.md`, `TIEMPO.md`); decisión pendiente.")
    w("")
    w("| id | vanilla | desc |")
    w("|---|---|---|")
    for f in por_origen["?"]:
        w(f"| `{f.id}` | `{_celda(_v(f.vanilla))}` | {_celda(f.desc)} |")
    w("")
    w("## Todas las banderas")
    w("")
    for g, titulo in C.GRUPOS:
        fs = [f for f in reg if f.grupo == g]
        if not fs:
            continue
        w(f"### {titulo} (`{g}`)")
        w("")
        w("| id | vanilla | enhanced | origen | desc |")
        w("|---|---|---|---|---|")
        for f in fs:
            w(f"| `{f.id}` | `{_celda(_v(f.vanilla))}` | {_celda(_v(f.enhanced))} | {f.origen} | {_celda(f.desc)} |")
        w("")
    return "\n".join(L).rstrip("\n") + "\n"


def main(argv):
    texto = generar()
    if "--check" in argv:
        try:
            with open(SALIDA, encoding="utf-8", newline="") as fh:
                actual = fh.read()
        except OSError:
            actual = None
        if actual != texto:
            print("BANDERAS.md no está al día: python -m yo2srv.gen_banderas")
            return 1
        print("BANDERAS.md al día")
        return 0
    with open(SALIDA, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(texto)
    print(f"escrito {SALIDA} ({len(C.REGISTRO)} banderas)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
