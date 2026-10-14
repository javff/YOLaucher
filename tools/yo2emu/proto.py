"""Alias de yo2srv/red/proto.py (REESTRUCTURA.md paso E1a): `import proto` devuelve EL MISMO objeto de modulo que
`import yo2srv.red.proto` (misma identidad: `proto.W is yo2srv.red.proto.W`). El codigo vive en el paquete.

Vale para `import proto` / `from proto import W`. NO vale para cargar este fichero por ruta con
spec_from_file_location(...).exec_module(...): eso ejecuta ESTE texto y deja sys.modules[__name__] apuntando al modulo
del paquete, pero el objeto que se devuelve al llamador es el modulo vacio; cargarlo siempre con import.
"""
import importlib
import sys

sys.modules[__name__] = importlib.import_module("yo2srv.red.proto")
