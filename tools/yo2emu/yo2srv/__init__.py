"""yo2srv: paquete destino de la reestructura de run.py (REESTRUCTURA.md).

Estado (paso S1, 07-10): el paquete existe y run.py lo arranca (`SRV = Server(globals())`), pero NINGUNA lógica de juego
vive aquí todavía. Lo que hay es el esqueleto con su API y sus pruebas (yo2srv/tests):

    server.py   Server: contenedor del proceso; srv.ns es el dict compartido (hoy: globals() de run.py)
    rutas.py    EMU, GAME, ORIGINAL, WWW, STATE_PATH, LOGDIR, carpetas de tokens (calculadas desde tools/yo2emu)
    config.py   registro de banderas (Anexo A), de solo lectura y ligado a los sitios de hoy
    bus.py      eventos: emit / ask / subscribe (aún sin suscriptores)
    router.py   manejadores por (tipo de puerto, id de mensaje) con dueño (aún sin usar)
    hot.py      recarga genérica de módulos (aún sin usar)
    compat.py   alias de nombres antiguos del namespace y de módulos
    log.py      log(), máscaras de logseguro, excepthooks y _admin_ev: lo usa run.py desde el paso E1a (iniciar())

Regla de este paquete mientras dure la migración: importarlo NO debe arrancar hilos, abrir ficheros ni tocar sys.modules
más allá de lo suyo (lo comprueba yo2srv/tests/test_importar.py).
"""

__version__ = "S1"
