"""Mundo de 2008 (paso E5 de la reestructura, REESTRUCTURA.md §3.4): `MUNDO_2008` (las seis banderas de research/mundo,
con `YO2_MUNDO`) y `mundo(k)`.

Salido de run.py SIN cambiar la lógica. `MUNDO_2008` es un dict: el MISMO objeto aquí, en el namespace y en
research/mundo (`MW.install(globals())` lo lee de `ns["MUNDO_2008"]`); el panel cambia sus claves por dentro. `MW` (el
módulo research/mundo) lo carga run.py (paso E8 lo moverá) y aquí se lee como `srv.MW`. El GM (`GM_ACCOUNTS`) sigue en
research/mundo hasta la fase 3.
"""
import os

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama justo antes de importar los nombres de aquí."""
    global srv
    srv = servidor
    return servidor


# MUNDO_2008 (research/mundo/proposal.py; notas: research/npcs_web/NOTES.md §12 y research/lobby/NOTES.md «Mundo
# 2008»). Todo False = como antes (YO2_MUNDO=0). spots: las 24 originales de la captura DuelCity del 1-2-2008 con su nombre real y
# su zona (cruce id <-> NPC por la receta 100NN del cliente: C; zona de febrero: P) en vez de los ids 1-32 de relleno ·
# prizes: carta de 5 victorias de cada original, dancer/raMon cuentan duelos, Miss Scout 8 (DC: C) · challenge:
# 挑戦権 de Kaiba/Alexis al ganar 5 veces a gouk/KC Dragon/Miss Scout o AoJ/Kyuros/lalala (C; Marik como antes) ·
# pvp_titles: títulos por logros también tras un PvP (umbrales D) · hair_titles: Kaiser Afro / Rey del Mechón (P) ·
# title_owned: 0x4174 rechaza un título que no tiene (P). Activar: poner True y reiniciar; en caliente,
# MUNDO_2008["x"] = True; MW.install(globals()); NPCS = load_npcs()  (lo último solo para spots).
# 07-10: activado por defecto con el OK del owner (las seis). YO2_MUNDO=0 las apaga todas (el «como antes» de las
# pruebas viejas); YO2_MUNDO=-spots,-prizes apaga solo esas; YO2_MUNDO=spots,all enciende como siempre.
MUNDO_2008 = {"spots": True, "prizes": True, "challenge": True, "pvp_titles": True, "hair_titles": True,
              "title_owned": True}
for _k in [k.strip() for k in os.environ.get("YO2_MUNDO", "").split(",") if k.strip()]:   # p. ej. YO2_MUNDO=spots,prizes
    if _k in ("0", "none"):
        MUNDO_2008.update({k: False for k in MUNDO_2008})
    elif _k.startswith("-") and _k[1:] in MUNDO_2008:
        MUNDO_2008[_k[1:]] = False
    elif _k in MUNDO_2008 or _k == "all":
        MUNDO_2008.update({k: True for k in MUNDO_2008} if _k == "all" else {_k: True})

def mundo(k):
    return bool(srv.MW is not None and srv.MUNDO_2008.get(k))
