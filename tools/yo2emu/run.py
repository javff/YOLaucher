"""Arranca el cliente original (2008-11) con Frida y un servidor TCP local que registra lo que llega.

  python tools/yo2emu/run.py [segundos]        (0 = sin límite; sin argumento, 40 s)

- El cliente se lanza con __COMPAT_LAYER=RunAsInvoker (su manifiesto pide administrador).
- net_hook.js redirige sus conexiones a 127.0.0.1 y registra DNS/connect/send/recv.
- El servidor escucha en 5730 (la puerta de entrada de YO2) y en otros puertos que se pidan.
Todo va a tools/yo2emu/logs/<fecha>.log.

Paso E8 de la reestructura (REESTRUCTURA.md): este fichero solo prepara el entorno y el log, crea el Server y arranca; todo
lo demás vive en el paquete yo2srv/ (arranque.py = las cargas, red/ = puertos, admin/consola.py = la consola 5799,
cliente.py = Frida, Server.run_forever = el bucle). Los `globals()` de ESTE fichero siguen siendo el espacio compartido de
todo el sistema (`SRV.ns`): la consola 5799 ejecuta ahí cada línea, el panel, research/* y los hot_*.py reciben ese dict, y
cada nombre del contrato (paridad/contrato_ns.txt) sigue existiendo en él.
"""
import os, sys, time, json, socket, struct, threading, datetime, queue, shutil, traceback
# P1 (empaquetado, 11-10): sin `import frida` (solo lo importa yo2srv/cliente.py al lanzar yo2.exe). YO2_PAQUETE=1: ver
# yo2srv/paquete.py y paquete/RUTAS.md (sin cliente, estado y logs en %LOCALAPPDATA%, para si no hay reglas).
from yo2srv import paquete as _paquete, rutas as _rutas

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.normpath(os.path.join(HERE, "..", "..", "_original", "yo2_2008-11"))
# PvP fase 1 (V03, social_pvp.md P0-3) y modo de prueba. Variables de entorno:
#   YO2_BIND         IPs en las que escuchan los puertos de juego y el HTTP, separadas por comas
#                    (por defecto 127.0.0.1; p. ej. «127.0.0.1,192.0.2.10» = loopback + Tailscale). La consola
#                    de administración (5799) escucha SIEMPRE solo en 127.0.0.1: ejecuta Python.
#   YO2_PUBLIC_IP    IP que se anuncia en la lista de la puerta (0x2003) y en 0x4306; da igual para el cliente con
#                    net_hook.js (reescribe todo destino a su --server), sirve para clientes sin hook.
#   YO2_PORT_OFFSET  se suma a TODOS los puertos (5730.., 5760, 8730, 5799): segunda instancia de prueba que no choca
#                    con la viva.
#   YO2_STATE        otro state.json (prueba). YO2_NO_CLIENT=1: no lanza yo2.exe. YO2_LOGDIR: carpeta del log (con
#                    desplazamiento de puertos, por defecto logs/prueba, para no confundir el «log más reciente»).
#                    En modo paquete: sin cliente salvo YO2_NO_CLIENT=0, y LOGDIR/STATE en %LOCALAPPDATA% (yo2srv/rutas.py).
PORT_OFFSET = int(os.environ.get("YO2_PORT_OFFSET", "0") or 0)
BIND_IPS = [x.strip() for x in (os.environ.get("YO2_BIND") or "127.0.0.1").split(",") if x.strip()] or ["127.0.0.1"]
PUBLIC_IP = os.environ.get("YO2_PUBLIC_IP") or next((x for x in BIND_IPS if x != "0.0.0.0"), "127.0.0.1")
NO_CLIENT = os.environ.get("YO2_NO_CLIENT", "1" if _rutas.paquete() else "") not in ("", "0")
LOGDIR = _rutas.logdir()  # YO2_LOGDIR, o logs (logs/prueba con YO2_PORT_OFFSET); paquete: %LOCALAPPDATA%\YO2Online\servidor\<puerto>\logs
# Reestructura E1a (REESTRUCTURA.md, 10-10): el log (log, logf, log_path, _LOG_LOCK, _LOG_T), las máscaras de logseguro
# (_logseg, _LOGSEG, _log_mask, _rx_line), los excepthooks (_thread_excepthook) y _admin_ev viven en yo2srv/log.py; aquí
# quedan los MISMOS nombres. `log` y `_log_mask` se buscan en ESTE namespace en cada llamada, así que lo que la consola o
# hot_logseguro.py reasignen aquí sigue mandando. `iniciar` abre LOGDIR/AAAAMMDD-HHMMSS.log, pasa stdout a utf-8 (B26) e
# instala los excepthooks (B02). Ver yo2srv/log.py para el porqué de cada pieza.
# Un import por nombre, a propósito: hot_reserva.py (y otras cargas en caliente) completan lo que le falta al vivo
# ejecutando la SENTENCIA de run.py que liga ese nombre; si fuera una sola sentencia con todos, cargar `_rx_line` en un
# vivo viejo reenlazaría también `log` a un log sin fichero (T8.hot_reserva).
from yo2srv.log import iniciar as _iniciar_log
from yo2srv.log import log
from yo2srv.log import logseguro as _logseg
from yo2srv.log import _LOGSEG
from yo2srv.log import log_mask as _log_mask
from yo2srv.log import rx_line as _rx_line
from yo2srv.log import _thread_excepthook
from yo2srv.log import _admin_ev
logf, log_path, _LOG_LOCK, _LOG_T = _iniciar_log(globals(), LOGDIR)

# S1: el contenedor del proceso (srv.ns es globals()). `or` para sobrevivir a una re-ejecución.
from yo2srv.server import Server; SRV = globals().get("SRV") or Server(globals())

# E8: las cargas, en su orden de siempre (yo2srv/arranque.py), y de ahí en adelante escuchar, cliente y bucle.
from yo2srv import arranque, cliente
arranque.cargar(SRV)
# ---- FIN DE LA CARGA ---- (ocg_test.py y pvp_test.py ejecutan el texto de este fichero HASTA la línea anterior: sin puertos ni hilos)
_paquete.comprobar(SRV)    # borra el token de la consola al salir; modo paquete: para (en inglés) si faltan datos o reglas
arranque.escuchar(SRV)     # HANDLERS, CONNS, consola 5799, comprobación de puertos, panel y los hilos serve/serve_http
cliente.iniciar(SRV)       # yo2.exe con Frida (nada con YO2_NO_CLIENT=1); respawn_client() queda para la consola
SRV.run_forever()          # `python run.py 0` = sin límite; DEADLINE += 3600 desde la consola para alargar
