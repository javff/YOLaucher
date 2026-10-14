"""El cliente original con Frida (paso E8 de la reestructura, REESTRUCTURA.md §3.4): `launch_client`, `respawn_client`,
`on_msg` (lo que net_hook.js manda al servidor, al log) y el arranque del cliente (`iniciar`), salidos de run.py SIN cambiar
la lógica.

  - El cliente (yo2.exe, 2008-11) se lanza con __COMPAT_LAYER=RunAsInvoker (su manifiesto pide administrador).
  - net_hook.js redirige sus conexiones a 127.0.0.1 y registra DNS/connect/send/recv.
  - B32: el cliente vive atado a ESTE proceso (net_hook.js parchea con Memory.patchCode saltos a NativeCallback que mueren
    con el script): reiniciar run.py probablemente tumba el cliente. Por eso: (a) se registra cuándo Frida suelta yo2.exe y
    por qué (`_on_detached`); (b) `respawn_client()` desde la consola (admin.py) relanza yo2.exe con Frida sin parar el
    servidor.
  - YO2_NO_CLIENT=1 (instancia de prueba o servidor puro): no se importa frida ni se lanza yo2.exe.

Reglas (REESTRUCTURA.md §2.3): `CLIENT` es el MISMO dict en el namespace de run.py y aquí; `sess`, `script` y `pid` (la sesión
de Frida, su script y el pid de yo2.exe) se dejan en el namespace como siempre (`global sess, script, pid`). Todo lo
reasignable (`log`, `on_msg`, `_on_detached`, `launch_client`) se lee como `srv.X` al usarlo. `frida` se importa al
lanzar: sin cliente no hace falta (en las pruebas, ocg_test/pvp_test lo sustituyen por un módulo vacío).
"""
import json
import os
import sys
import threading
import time

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.

CLIENT = {"pid": None, "detached": None}


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). CLIENT es el mismo dict que el del namespace si ya había uno."""
    global srv, CLIENT
    srv = servidor
    CLIENT = servidor.ns.get("CLIENT") or CLIENT
    return servidor


def on_msg(m, data):
    if m["type"] == "send":
        p = m["payload"]; t = p.pop("t")
        if t == "glog":
            # Formato en Shift-JIS (bytes) + texto ya sustituido (puede traer bytes japoneses rotos)
            fmt = bytes(p["sjis"]).decode("cp932", "replace")
            srv.log(f"[game:{p['fn']}] {fmt.strip()}  ||  {p['args'].strip()[:200]}")
            return
        extra = p.pop("data", None)
        srv.log(f"[cli] {t} {json.dumps(p, ensure_ascii=False)}")
        if extra: srv.log(extra)
    else:
        srv.log("[frida]", m)

def _on_detached(*a):
    reason = a[0] if a else "?"
    crash = a[1] if len(a) > 1 else None
    srv.CLIENT["detached"] = str(reason)
    srv.log(f"[run] Frida suelta yo2.exe (pid {srv.CLIENT['pid']}): {reason}" + (f" | {getattr(crash, 'summary', crash)}" if crash else ""))

def launch_client():
    """Lanza yo2.exe con Frida y net_hook.js (lo que hacía el arranque) y devuelve el pid."""
    import frida
    # net_hook.js lee YO2_SERVER / YO2_PORT_OFFSET del entorno del juego (V03): el cliente del owner va por loopback
    env = dict(os.environ, __COMPAT_LAYER="RunAsInvoker", YO2_SERVER="127.0.0.1", YO2_PORT_OFFSET=str(srv.PORT_OFFSET))
    pid = frida.spawn([os.path.join(srv.GAME, "yo2.exe")], cwd=srv.GAME, env=env)
    sess = frida.attach(pid)
    sess.on("detached", srv._on_detached)
    script = sess.create_script(open(os.path.join(srv.HERE, "net_hook.js"), encoding="utf-8").read())
    script.on("message", srv.on_msg)
    script.load()
    frida.resume(pid)
    srv.sess, srv.script, srv.pid = sess, script, pid        # (antes: global sess, script, pid)
    srv.CLIENT.update(pid=pid, detached=None)
    return pid

def respawn_client(force=False):
    """Consola: relanza yo2.exe si se cerró (el cliente nuevo empieza en #1 y saw_client reinicia las secuencias).
    Con el cliente aún enganchado no hace nada, salvo force=True (habría dos clientes contra el mismo servidor)."""
    if srv.CLIENT["detached"] is None and srv.CLIENT["pid"] is not None and not force:
        srv.log(f"[run] respawn_client: yo2.exe (pid {srv.CLIENT['pid']}) sigue enganchado; respawn_client(force=True) para forzar")
        return None
    p = srv.launch_client()
    srv.log(f"[run] yo2.exe relanzado, pid {p}")
    return p


def registrar():
    """Deja en el namespace de run.py los nombres de siempre: CLIENT, on_msg, _on_detached, launch_client, respawn_client."""
    srv.registrar_nombres(CLIENT=CLIENT, on_msg=on_msg, _on_detached=_on_detached, launch_client=launch_client,
                          respawn_client=respawn_client)


def iniciar(servidor=None):
    """Lanza yo2.exe (salvo YO2_NO_CLIENT). El --login lo arranca Server.run_forever (después de fijar DEADLINE, como antes)."""
    if servidor is not None:
        setup(servidor)
    registrar()
    if srv.NO_CLIENT:                                # instancia de prueba o servidor puro: sin yo2.exe
        srv.log(f"[run] YO2_NO_CLIENT: no se lanza yo2.exe; log en {srv.log_path}")
    else:
        srv.launch_client()
        srv.log(f"[run] yo2.exe pid {srv.pid}, log en {srv.log_path}")


def auto_login():
    """--login (con cliente): espera a que el log diga «merge.txt», espera 3 s y lanza login.py (hilo aparte)."""
    def _login():
        while "merge.txt" not in open(srv.log_path, encoding="utf-8").read():
            time.sleep(0.5)
        time.sleep(3)
        srv.log("[run] login automático")
        import subprocess
        subprocess.run([sys.executable, os.path.join(srv.HERE, "login.py")])
        srv.log("[run] login enviado")
    threading.Thread(target=_login, daemon=True).start()
