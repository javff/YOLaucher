"""Avatar propio y ajeno en el mapa (paso E5 de la reestructura, REESTRUCTURA.md §3.4): `avatar_info` (0x5340),
`AV_STATE` (mundo del último 0x4170) y `myroom_self_avatar` (0x4181 al propio cliente en My Room).

Salido de run.py SIN cambiar la lógica (research/avatar sigue siendo `AV`, cargado por run.py). `MYROOM_SELF_AVATAR` es una
bandera del panel (la escribe como `NS["MYROOM_SELF_AVATAR"]`): se lee como `srv.MYROOM_SELF_AVATAR`, igual que `HUB`,
`my_pid`, `my_name` y `LOBBY_PORT`. `AV_STATE` es el MISMO dict en el módulo y en el namespace, y sobrevive a las
recargas: `setup()` adopta el del namespace si ya había uno (antes: `globals().get("AV_STATE") or {"world": 1}`).
"""
import os
import struct

from yo2srv.red import proto

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama justo antes de importar los nombres de aquí."""
    global srv, AV_STATE
    srv = servidor
    AV_STATE = servidor.ns.get("AV_STATE") or AV_STATE      # sobrevive a una recarga de run.py en el mismo namespace
    return servidor


AV_STATE = {"world": 1}   # mundo del último 0x4170: 0 Domino, 1 Academia

def avatar_info(pid):
    """0x5340 (FUN_006b9a10): u32 0, u32 id, nombre16, u16 x, u16 y, u8 dirección 0-7. P0-4: si el pid es de OTRO
    jugador conectado, su nombre y su última posición (antes, siempre el propio)."""
    other = srv.HUB.avatar_of(pid) if pid != srv.my_pid() else None
    if other is not None:
        name, x, y, d = other
        return proto.W().u32(0).u32(pid).s(name, 16).u16(x).u16(y).u8(d).bytes()
    name = srv.my_name()
    return proto.W().u32(0).u32(pid).s(name, 16).u16(400).u16(300).u8(0).bytes()

# My Room y el aspecto propio (research/avatar/NOTES.md, «My Room y un aspecto por mundo»). C: el muñeco propio de
# My Room lo crea FUN_009be6b0 al llegar 0x5340: busca el pid en la lista del bloque (+0x9a4) y FUN_009d2930 ->
# FUN_009de490 usa las piezas de ESE registro, reg+0x134 + mundo·0x14 (mundo = DAT_012bb6b0+0x448, que My Room no
# cambia: FUN_00a587a0 caso 0xc). LIST1 (0x4212) crea el registro con el avatar por defecto (FUN_006c33d0 ->
# FUN_0063c250 -> FUN_009bced0(0)) y solo LIST2, 0x3112 op 1 o 0x4181 le ponen piezas; en My Room el cliente no pide
# LIST2, así que cada uno se veía con el chico por defecto (pelo naranja; uniforme azul de Domino o, validado en
# Academia, la chaqueta roja). El cliente registra en My Room los DOS mundos (0x4170 del otro y luego del actual,
# FUN_006d1840 estados 0xd/0xf) después de LIST1 y antes de 0x5320: se le devuelve cada uno como 0x4181
# UPDATE_PLAYERAVATARINFO (FUN_006b9930 escribe reg+0x134 + mundo·0x14) con su pid. Que el oficial lo hiciera con
# 0x4181 y no con otro mensaje es P; las piezas son las que el propio cliente registra. YO2_MYROOM_AVATAR=0 lo apaga.
MYROOM_SELF_AVATAR = os.environ.get("YO2_MYROOM_AVATAR", "1") not in ("", "0")

def myroom_self_avatar(c, data):
    """Tras contestar 0x4170 en My Room: 0x4181 (u32 pid, u32 mundo, 16 B) al propio cliente (ver arriba)."""
    if not srv.MYROOM_SELF_AVATAR or c.port != srv.LOBBY_PORT or len(data) < 20:
        return
    world = struct.unpack(">I", data[:4])[0]
    if world in (0, 1):                     # FUN_006b9930 no comprueba el mundo: solo los dos huecos que existen
        c.send(0x4181, struct.pack(">II", srv.my_pid(), world) + bytes(data[4:20]))
