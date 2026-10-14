"""Cartera (paso E4 de la reestructura, REESTRUCTURA.md §3.4): saldo del jugador y los dos mensajes que lo llevan al
cliente (PLAYERDATAUPDATE 0x4140 y PSELECTRESULT 0x4101).

Son los de run.py de siempre: cuando la persistencia carga (research/persistencia, `PH`), run.py REASIGNA `wallet`,
`set_wallet` y `player_data_update` con los de PH; por eso aquí dentro se llaman siempre como `srv.wallet(...)` etc.
(§2.3): lo que hay en el namespace en ese momento, no la copia de este módulo.

E8: también viven aquí `DEFAULT_COUNTRY` (país de los personajes nuevos), `dp_balance` (DP guardados del personaje) y
`dp_npc_gate` (0x4288 contra una NPC sin DP -> 0x428a −612), que se habían quedado en run.py.
"""
import os
import struct

import proto

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama justo después de importarlo."""
    global srv
    srv = servidor
    return servidor


START_MP = 10000
START_DPP = int(os.environ.get("YO2_DP_INICIO") or 10)   # puntos DUELPASS (+0x1248) de un personaje NUEVO: 10 gratis
# como el oficial; 1 DP por duelo (research/persistencia/NOTES.md §13). Sin ellos no deja retar a duelo («No tienes
# suficientes DUELPASS points»). Con persistencia manda PP.START_DPP (el mismo YO2_DP_INICIO); los personajes que ya
# existen guardan su saldo (PP.LEGACY_DPP = 1000 si un perfil viejo no lo tiene).

def wallet():
    """Saldo del jugador guardado en state.json: mp (tienda) y dpp (puntos DUELPASS, +0x1248)."""
    w = srv.load_state().get("wallet") or {"mp": srv.START_MP, "dpp": srv.START_DPP}
    return w

def set_wallet(**kv):
    st = srv.load_state(); w = st.get("wallet") or {"mp": srv.START_MP, "dpp": srv.START_DPP}; w.update(kv); st["wallet"] = w; srv.save_state(st)
    return w

def player_data_update():
    """PLAYERDATAUPDATE 0x4140 (FUN_006bc270): reescribe los datos del jugador. u8, u32, u32 MP (+0xac), 5×u16,
    u32 puntos DUELPASS (+0x1248), 10×u16, u32, 2×u16, 2×u32, 6×u16, 6×u32, 3×u16, u32, 3×u16, u32, u32 + 4×u8, u16.
    Sin él la tienda no descuenta los MP."""
    w = srv.wallet()
    b = proto.W().u8(0).u32(0).u32(w["mp"])
    for _ in range(5): b.u16(0)
    b.u32(w["dpp"])
    for _ in range(10): b.u16(0)
    b.u32(0).u16(0).u16(0).u32(0).u32(0)
    for _ in range(6): b.u16(0)
    for _ in range(6): b.u32(0)
    b.u16(0).u16(0).u16(0).u32(0).u16(0).u16(0).u16(0).u32(0).u32(0).u8(0).u8(0).u8(0).u8(0).u16(0)
    return b.bytes()

def pselect_result():
    """PSELECTRESULT 0x4101 (FUN_006b7ef0), 133 B: u32 0, u32 id jugador, u32 (+0x1248), u16, u32, u32, u32, u8,
    u32, u32 MP (saldo de la tienda, +0xac), 15×u16, u32, 2×u16, 2×u32, 6×u16, 6×u32, 3×u16, u32, 3×u16.
    El resto va a 0: con valores al azar salió «Cartas Premio del CHAMPIONSHIP» (pide 0x4915 y espera 0x4803)."""
    head = proto.W().u32(0).u32(srv.my_pid()).u32(srv.wallet()["dpp"]).u16(0).u32(0).u32(0).u32(0).u8(0).u32(0).u32(srv.wallet()["mp"]).bytes()
    return head + bytes(133 - len(head))


# Social: la BANDERA sobre el nombre (CNationFlag) sale de la cadena de 4 B de LIST1 (FUN_006b8c30: struct+0x61 ->
# reg+0x12c), comparada con la tabla del exe (0xd0b78c: 0x30 B por país, código ISO de 2 letras «AL», «AD», «AT»…
# «ES»… y su recorte de flag_2215_01.png / flag_4228_01.png; FUN_009d5e80). Por defecto el muñeco usa «AL»
# (DAT_00d0db68, FUN_009ddd60): la bandera roja que llevaban todos con la cadena vacía.  C
# El país es de la CUENTA en el oficial (no se elige en el cliente; P): aquí profiles[<hueco>]["country"] del perfil,
# o YO2_COUNTRY (por defecto «ES»). Solo códigos de la tabla del exe (0xd0b788: 146 países de todo el mundo, no solo Europa).
# 12-10: los personajes NUEVOS sacan el país del idioma del cliente (PP.COUNTRY_BY_LANG, research/persistencia); este valor
# queda de respaldo (idioma desconocido o bandera apagada) y para los personajes de antes.
DEFAULT_COUNTRY = (os.environ.get("YO2_COUNTRY") or "ES").upper()[:2]


def dp_balance(sess=None):
    """Coste del duelo (research/persistencia/NOTES.md §13): DP guardados del personaje de la sesión sess (None = la
    del hilo), como block_player. None si no se puede saber (sin persistencia, sin sesión…): quien pregunta no bloquea."""
    if getattr(srv, "PP", None) is None or not hasattr(srv.PP, "dp_of"):
        return None
    old = getattr(srv.CTX, "sess", None)
    try:
        if sess is not None:
            srv.CTX.sess = sess
        st = srv.load_state()
        slot = sess.slot if sess is not None and sess.slot is not None else srv.my_slot(st)
        p = srv.PP.profile(st, slot, create=False)
        return srv.PP.dp_of(p) if p else None
    except Exception as e:
        srv.log(f"[dp] saldo de {sess!r}: {e!r}")
        return None
    finally:
        srv.CTX.sess = old

def dp_npc_gate(c, data):
    """0x4288 con rival NPC y sin DP para un duelo -> 0x428a s32 −612 (4556 «No tienes suficientes DUELPASS points.»,
    FUN_00884b70; el cliente pasa al estado 0x4a con ese código como en sus propias comprobaciones, C) y True. El cliente
    NO mira los DP en el camino de la NPC (los 4 lectores de +0x1248 de FUN_00863750/FUN_00863ec0 son de salas de
    jugador: 0x869410 mira el dstyle de la sala y con 0, que es la NPC, sale antes), así que solo lo puede parar el
    servidor (P: que Konami lo hiciera así). Va ANTES de pvp_hub (si no, los demás le verían «en duelo»).
    Apagar: YO2_DP_NPC_BLOQUEO=0 / PP.DP_NPC_BLOCK = False, o NPC_RULES_2008["dp"] = False (la NPC ya no cobra)."""
    if getattr(srv, "PP", None) is None or not getattr(srv.PP, "DP_NPC_BLOCK", False) or not srv.npc_rule("dp"):
        return False
    rival = struct.unpack(">I", data[24:28])[0] if len(data) >= 28 else 0
    if not 1 <= rival <= 98:                     # 0xffffffff = sala propia del menú (research/lobby is_npc_rival)
        return False
    need = srv.PP.dp_need(srv.PP.DP_SINGLE_STYLE)
    have = srv.dp_balance(getattr(c, "sess", None))
    if not need or have is None or have >= need:
        return False
    srv.log(f"[dp] reto a la NPC {rival} rechazado: {have} DP (hace falta {need}) -> 0x428a {srv.PP.R_NO_DP_ME}")
    c.send(0x428a, proto.W().u32(srv.PP.R_NO_DP_ME & 0xffffffff).bytes())
    return True
