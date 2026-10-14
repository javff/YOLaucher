"""Elección de la carta del sobre tras un duelo (paso E8 de la reestructura): el 0x4802 (SELECTPACK) del lobby con dos
envoltorios delante, los dos salidos de run.py SIN cambiar la lógica.

  - `instalar_npc()`: NPC_RULES_2008 «card» (research/npcreglas): el 0x4802 que abre el sobre de un duelo de NPC da UNA carta
    (si no, el manejador de siempre). Crea `_h_selectpack_npc` en el namespace y lo pone en `LP.HANDLERS[0x4802]`.
  - `_h_selectpack_pvp` / `pvp_card_install()` (07-10, research/persistencia/NOTES.md §14): PvpMatch._finish deja la carta
    pendiente en el perfil y manda sus sobres en 0x4800; el cliente abre él solo el elector (CWindowPackSelect) y manda
    0x4802 con el elegido. Aquí: si el perfil tiene una carta de PvP pendiente con ese sobre, 0x4803 con
    PP.pvp_card_take() cartas (1 por duelo) de ese sobre, guardadas en el baúl (LP._cards_result, B13). Si no, lo de
    siempre (NPC o h_selectpack). Las pendientes se entregan aunque luego se ponga YO2_PVP_CARTA=0 (eso solo deja de
    ofrecerlas).

Reglas (REESTRUCTURA.md §2.3): todo lo reasignable o parcheable en caliente (`LP`, `PP`, `NR`, `DFM`, `load_state`,
`save_state`, `npc_rule`, `_h_selectpack_pvp`, `log`…) se lee como `srv.X` en el momento de usarlo. `_h_selectpack_pvp` y
`pvp_card_install` están en el namespace de run.py (hot_pvp_carta.py y test_hot_pvp_carta.py los buscan ahí).
"""
import struct

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). arranque.py lo llama antes de usarlo."""
    global srv
    srv = servidor
    return servidor


def instalar_npc(LP=None):
    """NPC_RULES_2008 card: el 0x4802 que abre el sobre de un duelo de NPC da UNA carta (si no, el manejador de siempre).
    Idempotente: no envuelve dos veces (marca `_npcreglas`). LP: solo la recarga en caliente de LP (módulo nuevo, aún
    no visible en el namespace)."""
    LP, NR = LP if LP is not None else getattr(srv, "LP", None), getattr(srv, "NR", None)
    if LP is not None and NR is not None and 0x4802 in LP.HANDLERS and not getattr(LP.HANDLERS[0x4802], "_npcreglas", 0):
        def _h_selectpack_npc(c, msg_id, param, data, _orig=LP.HANDLERS[0x4802]):
            if not srv.npc_rule("card"):
                return _orig(c, msg_id, param, data)
            def take_due(pack):
                with srv.LP._state_lock():
                    st = srv.load_state(); p = srv.DFM.PS.profile(st, srv.DFM.PS.active_slot(st), create=False)
                    if not p or not srv.NR.take_due_in(p, pack):
                        return False
                    srv.save_state(st); return True
            return srv.NR.h_selectpack(c, data, lambda: _orig(c, msg_id, param, data), take_due,
                                       lambda pk: srv.LP._cards(pk, 1), srv.LP._cards_result)
        _h_selectpack_npc._npcreglas = 1
        srv._h_selectpack_npc = _h_selectpack_npc
        LP.HANDLERS[0x4802] = _h_selectpack_npc


def _h_selectpack_pvp(c, msg_id, param, data, _orig=None):
    pack = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else 0
    n = 0
    with srv.LP._state_lock():
        st = srv.load_state(); p = srv.PP.profile(st, srv.PP.active_slot(st), create=False)
        if p and pack:
            n = srv.PP.pvp_card_take(p, pack)
            if n:
                srv.save_state(st)
    if not n:
        return _orig(c, msg_id, param, data) if _orig is not None else None
    cards = srv.LP._cards(pack, n)
    srv.log(f"[pvp] carta tras el PvP: sobre {pack} -> {cards}")
    srv.LP._cards_result(c, cards)
    return True

def pvp_card_install(LP=None):
    """Pone _h_selectpack_pvp delante del 0x4802 del lobby (idempotente: si ya estaba, lo cambia por el nuevo). LP: solo
    la recarga en caliente de LP (módulo nuevo, aún no visible en el namespace)."""
    LP, PP = LP if LP is not None else getattr(srv, "LP", None), getattr(srv, "PP", None)
    if LP is None or PP is None or not hasattr(PP, "pvp_card_take") or 0x4802 not in LP.HANDLERS:
        return False
    cur = LP.HANDLERS[0x4802]
    base = getattr(cur, "_pvp_orig", cur) if getattr(cur, "_pvpcarta", 0) else cur
    def h(c, msg_id, param, data, _orig=base):
        return srv._h_selectpack_pvp(c, msg_id, param, data, _orig)
    h._pvpcarta, h._pvp_orig = 1, base
    LP.HANDLERS[0x4802] = h
    return True
