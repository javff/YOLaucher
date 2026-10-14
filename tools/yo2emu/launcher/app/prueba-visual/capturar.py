"""Capturas de la pestaña Manage (launcher/capturas/manage-*.png) con Edge sin ventana, contra servir.py + fakego.js.

    python servir.py 8919            (en otra consola)
    python capturar.py [puerto] [prefijo]   (por defecto 8919; con prefijo, solo esas, p. ej. «nivel»)

Cada captura: nombre, tamaño, tema y la URL de fakego.js (?ir=sección[:diálogo]). Edge usa un perfil temporal.
"""
import os
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.normpath(os.path.join(AQUI, "..", "..", "capturas"))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

CAPS = [
    # (nombre, ancho, alto, tema, consulta)
    ("servidor-pestanas", 1160, 680, "light", "ir=servidor"),
    ("jugadores", 1160, 680, "light", "ir=jugadores"),
    ("vetos", 1160, 680, "light", "ir=vetos"),
    ("cuentas", 1160, 680, "light", "ir=cuentas"),
    ("cuentas-registro-abierto", 1160, 680, "light", "ir=cuentas&abierto=1"),
    ("aviso", 1160, 680, "light", "ir=aviso"),
    ("copias", 1160, 680, "light", "ir=copias"),
    ("dialogo-vetar", 1160, 680, "light", "ir=jugadores:vetar"),
    ("dialogo-expulsar", 1160, 680, "light", "ir=jugadores:expulsar"),
    ("dialogo-clave", 1160, 680, "light", "ir=cuentas:clave"),
    ("jugadores", 1160, 680, "dark", "ir=jugadores"),
    ("cuentas", 1160, 680, "dark", "ir=cuentas"),
    ("dialogo-vetar", 1160, 680, "dark", "ir=jugadores:vetar"),
    ("vetos", 1160, 680, "dark", "ir=vetos"),
    ("jugadores", 900, 600, "light", "ir=jugadores"),
    ("cuentas", 900, 600, "light", "ir=cuentas"),
    ("aviso", 900, 600, "light", "ir=aviso"),
    ("dialogo-clave", 900, 600, "light", "ir=cuentas:clave"),
    ("jugadores", 900, 600, "dark", "ir=jugadores"),
    ("copias", 900, 600, "dark", "ir=copias"),
    ("jugadores-vacio", 900, 600, "light", "ir=jugadores&vacio=1"),
    # 12-10: nivel (botón Level en Players y Accounts, su diálogo) y «Starting level» en el formulario (servidor parado)
    ("nivel-jugadores", 1160, 680, "light", "ir=jugadores"),
    ("nivel-jugadores", 1160, 680, "dark", "ir=jugadores"),
    ("nivel-dialogo", 1160, 680, "light", "ir=jugadores:nivel"),
    ("nivel-dialogo", 1160, 680, "dark", "ir=jugadores:nivel"),
    ("nivel-dialogo-cuenta", 1160, 680, "light", "ir=cuentas:nivel"),
    ("nivel-cuentas", 1160, 680, "dark", "ir=cuentas"),
    ("nivel-formulario", 1160, 680, "light", "ir=servidor&srv=0&form=nuevo"),
    ("nivel-formulario", 1160, 680, "dark", "ir=servidor&srv=0&form=nuevo"),
    ("nivel-formulario", 900, 600, "light", "ir=servidor&srv=0&form=nuevo"),
    # 12-10: Duelpass (botón en Players y Accounts, su diálogo «Set to»/«Add»), opciones del anfitrión en el formulario y la
    # lista con filas «Offline · last seen» y «Private now»
    ("dp-jugadores", 1160, 680, "light", "ir=jugadores"),
    ("dp-dialogo", 1160, 680, "light", "ir=jugadores:dp"),
    ("dp-dialogo", 1160, 680, "dark", "ir=jugadores:dp"),
    ("dp-dialogo-suma", 1160, 680, "light", "ir=jugadores:dpsuma"),
    ("dp-dialogo-cuenta", 1160, 680, "light", "ir=cuentas:dp"),
    ("dp-cuentas", 1160, 680, "dark", "ir=cuentas"),
    ("dp-formulario", 1160, 680, "light", "ir=servidor&srv=0&form=nuevo&dp=500&bajar=1"),
    ("dp-formulario", 1160, 680, "dark", "ir=servidor&srv=0&form=nuevo&dp=500&gratis=1&bajar=1"),
    ("dp-formulario", 900, 600, "light", "ir=servidor&srv=0&form=nuevo&dp=500&gratis=1&bajar=1"),
    ("lista-estados", 1160, 680, "light", "lista=1"),
    ("lista-estados", 1160, 680, "dark", "lista=1"),
    ("lista-estados", 900, 600, "light", "lista=1"),
    # 12-10: modo «Password» (Public / Password / Hidden): formulario, fila con candado, detalle, diálogo y servidor en marcha
    ("clave-formulario", 1160, 680, "light", "ir=servidor&srv=0&form=nuevo&modo=clave&escribe=1"),
    ("clave-formulario", 1160, 680, "dark", "ir=servidor&srv=0&form=nuevo&modo=clave&escribe=1&ver=1"),
    ("clave-formulario-guardada", 1160, 680, "light", "ir=servidor&srv=0&form=editar&modo=clave&tiene=1"),
    ("clave-formulario", 900, 600, "light", "ir=servidor&srv=0&form=nuevo&modo=clave&escribe=1"),
    ("clave-formulario-oculto", 1160, 680, "light", "ir=servidor&srv=0&form=nuevo&modo=oculto"),
    ("clave-lista", 1160, 680, "light", "lista=1&sel=Clave001"),
    ("clave-lista", 1160, 680, "dark", "lista=1&sel=Clave002"),
    ("clave-lista", 900, 600, "light", "lista=1&sel=Clave002"),
    ("clave-dialogo", 1160, 680, "light", "lista=1&sel=Clave001&pw=1&escribe=1"),
    ("clave-dialogo", 1160, 680, "dark", "lista=1&sel=Clave001&pw=1&escribe=1&ver=1"),
    ("clave-dialogo-mala", 1160, 680, "light", "lista=1&sel=Clave001&pw=1&pwerr=1"),
    ("clave-dialogo-mala", 900, 600, "dark", "lista=1&sel=Clave001&pw=1&pwerr=1"),
    ("clave-servidor", 1160, 680, "light", "ir=servidor&srvclave=1&modo=clave&tiene=1"),
    ("clave-servidor", 1160, 680, "dark", "ir=servidor&srvclave=1&modo=clave&tiene=1"),
    # 12-10: «My servers» (varios servidores guardados, cada uno con su mundo) y sin idioma del servidor
    ("mis-lista", 1160, 680, "light", "ir=servidor"),
    ("mis-lista", 1160, 680, "dark", "ir=servidor"),
    ("mis-lista", 900, 600, "light", "ir=servidor"),
    ("mis-lista-parado", 1160, 680, "light", "ir=servidor&srv=0"),
    ("mis-lista-parado", 900, 600, "dark", "ir=servidor&srv=0"),
    ("mis-vacio", 1160, 680, "light", "ir=servidor&srv=0&mis=0"),
    ("mis-nuevo", 1160, 680, "light", "ir=servidor&form=nuevo"),
    ("mis-nuevo", 1160, 680, "dark", "ir=servidor&form=nuevo"),
    ("mis-nuevo", 900, 600, "light", "ir=servidor&form=nuevo"),
    ("mis-editar", 1160, 680, "light", "ir=servidor&form=editar"),
    ("mis-editar", 1160, 680, "dark", "ir=servidor&form=editar"),
    ("mis-editar", 900, 600, "light", "ir=servidor&form=editar"),
    ("mis-borrar", 1160, 680, "light", "ir=servidor&borrar=1&nombre=1"),
    ("mis-borrar", 1160, 680, "dark", "ir=servidor&borrar=2"),
    ("mis-borrar-antes", 1160, 680, "light", "ir=servidor&srv=0&borrar=0"),
    ("mis-cambiar", 1160, 680, "light", "ir=servidor&cambiar=1"),
    ("mis-cambiar", 900, 600, "dark", "ir=servidor&cambiar=1"),
    # 12-10 (NET): Settings → Network (puerto fijo, nodos de arranque), «Join by address» y «Your server» → Internet
    ("red-ajustes", 1160, 680, "light", "red=ajustes&fijo=1"),
    ("red-ajustes", 1160, 680, "dark", "red=ajustes&fijo=1&reiniciar=1&malas=1"),
    ("red-ajustes-apagado", 1160, 680, "light", "red=ajustes&srv=0"),
    ("red-ajustes", 900, 600, "light", "red=ajustes&fijo=1"),
    ("red-dialogo", 1160, 680, "light", "red=dir&escribe=1"),
    ("red-dialogo-clave", 1160, 680, "dark", "red=dir&dirmodo=clave&escribe=1"),
    ("red-dialogo-publico", 1160, 680, "light", "red=dir&dirmodo=publico&escribe=1"),
    ("red-dialogo-error", 1160, 680, "light", "red=dir&direrr=1"),
    ("red-dialogo", 900, 600, "dark", "red=dir&escribe=1"),
    ("red-servidor-publico", 1160, 680, "light", "ir=servidor&acceso=publico&fijo=1"),
    ("red-servidor-publico", 1160, 680, "dark", "ir=servidor&acceso=publico&fijo=1"),
    ("red-servidor-router", 1160, 680, "light", "ir=servidor&acceso=privado&fijo=1"),
    ("red-servidor-ocupado", 1160, 680, "dark", "ir=servidor&acceso=ocupado&fijo=1"),
    ("red-servidor-aleatorio", 1160, 680, "light", "ir=servidor&acceso=privado"),
    ("red-servidor-publico", 900, 600, "light", "ir=servidor&acceso=publico&fijo=1"),
    ("red-lista", 1160, 680, "light", "lista=1"),
    # merge SRV+NET (12-10): «Your server» compacto con el chip «Internet» y la fila de la dirección; sin scroll en los dos tamaños
    # y los dos temas, también con contraseña (fila del código + fila de la dirección a la vez)
    ("red-servidor-publico", 900, 600, "dark", "ir=servidor&acceso=publico&fijo=1"),
    ("red-servidor-router", 1160, 680, "dark", "ir=servidor&acceso=privado&fijo=1"),
    ("red-servidor-router", 900, 600, "light", "ir=servidor&acceso=privado&fijo=1"),
    ("red-servidor-router", 900, 600, "dark", "ir=servidor&acceso=privado&fijo=1"),
    ("red-servidor-publico-clave", 1160, 680, "light", "ir=servidor&acceso=publico&srvclave=1"),
    ("red-servidor-publico-clave", 900, 600, "dark", "ir=servidor&acceso=publico&srvclave=1"),
    ("red-servidor-ocupado", 900, 600, "light", "ir=servidor&acceso=ocupado&fijo=1&srvclave=1"),
]
SOLO = None   # p. ej. python capturar.py 8919 nivel  -> solo las que empiezan por «nivel»


def main():
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8919
    solo = sys.argv[2] if len(sys.argv) > 2 else SOLO
    os.makedirs(SALIDA, exist_ok=True)
    perfil = tempfile.mkdtemp(prefix="yo2-edge-")
    for nombre, w, h, tema, q in CAPS:
        if solo and not nombre.startswith(solo):
            continue
        fich = os.path.join(SALIDA, f"manage-{nombre}-{w}x{h}-{'oscuro' if tema == 'dark' else 'claro'}.png")
        url = f"http://127.0.0.1:{puerto}/?tema={tema}&{q}"
        r = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={perfil}",
                            f"--window-size={w},{h}", "--virtual-time-budget=9000", f"--screenshot={fich}", url],
                           capture_output=True, text=True, timeout=120)
        print(os.path.basename(fich), "ok" if os.path.exists(fich) else "FALLO " + r.stderr[-200:], flush=True)


if __name__ == "__main__":
    main()
