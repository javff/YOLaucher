"""Tienda (paso E4 de la reestructura, REESTRUCTURA.md §3.4): catálogo, precios, compra de cartas/sobres/mazos/ropa.

Salido de run.py SIN cambiar la lógica: solo cambia de dónde sale cada nombre. Todo lo que alguien puede reasignar o
parchear en caliente (las banderas SHOP_*, `PP`, `AV`, `wallet`, `set_wallet`, `player_data_update`, las funciones de
este mismo módulo que un hot_*.py sustituya en el namespace…) se lee SIEMPRE como `srv.X` en el momento de usarlo
(§2.3), es decir, de `srv.ns` = `globals()` de run.py. run.py importa de aquí los MISMOS nombres, así que la consola
5799, el panel (sec_economia) y los hot_*.py los siguen encontrando donde siempre.
"""
import json
import os
import struct

import proto
from yo2srv import rutas

srv = None      # el Server; lo fija setup(). Hasta entonces nada de este módulo se puede llamar.


def setup(servidor):
    """Enlaza el módulo con el Server (idempotente). run.py lo llama justo después de importarlo."""
    global srv
    srv = servidor
    return servidor


PACKINFO = rutas.packinfo()           # la misma ruta que tenía run.py (HERE/../../_original/extracted/merge2008/data/cardpack/packinfo_s.txt), o bajo YO2_JUEGO_DATOS
SHOP_CARDS = range(1, 41)   # cartas sueltas de muestra (id 1 = Espíritu Atado a la Tierra)

def shop_prices():
    """Precios de la tienda que pone el panel (Economía › Tienda y sobres) en admin_tienda.json junto a state.json:
    {"sobre"|"principiante"|"estructura"|"carta": MP, "<id de pack>": MP} (solo enteros 1..100000). Sin el fichero, o
    si no se lee, {} -> shop_catalog cobra lo de siempre."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(srv.STATE_PATH)), "admin_tienda.json"), encoding="utf-8") as f:
            d = json.load(f)
        out = dict(d.get("precios") or {}, **(d.get("sobres") or {}))
        return {str(k): v for k, v in out.items() if type(v) is int and 1 <= v <= 100000}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}

# B71/B68 (07-10, tienda2). packinfo_s.txt, un bloque por pack: id, tipo, portada, nombre, descripción y, en los
# mazos, <id>.ydc y el bit de PBIN del derecho («GET権利»; +1 = «GET済み»). El cliente copia el tipo a +4 y el bit a
# +0x68 de su ficha (FUN_0084d8a0); el editor de mazos solo lista los de tipo 1/4 con PBIN[bit + 1] puesto
# (FUN_00983c80) y, al recibir un mazo por DPAUTH (0x8102) o SD_GET, el propio cliente pone ese bit + 1.  C
# Tipo 2 = 9001 «TP3» y 9002 «Startup Present» (+ yo2pbinmap «Startup Present 10/30…»): premios, no sobres. C (datos)
SHOP_SELL_PRIZE_PACKS = False   # B71: True = volver a vender también los packs de tipo 2 (como antes del 07-10)
SHOP_DECK_RECIPE = True         # B68: comprar un mazo de tipo 1 pone PBIN[bit + 1] en el perfil (receta en el editor)
SHOP_DECK_ITEMGET = False       # B68: True = mandar además 0x5205 con ese bit (el cliente lo ve sin volver a entrar,
                                # pero sale «¡Has ganado un nuevo objeto!» con algo que el oficial no se sabe si enseñaba: D)

def packinfo_packs():
    """[(id, tipo, bit de PBIN o None)] de packinfo_s.txt en su orden; sin el fichero, 1-45 de tipo 0."""
    try:
        t = open(srv.PACKINFO, "rb").read().decode("cp1252").replace(chr(13), "")
    except OSError:
        return [(pid, 0, None) for pid in range(1, 46)]
    out = []
    for b in t.strip().split(chr(10) * 2):
        ln = [x.strip() for x in b.split(chr(10))]
        if not ln[0].isdigit():
            continue
        kind = int(ln[1]) if len(ln) > 1 and ln[1].isdigit() else 0
        out.append((int(ln[0]), kind, int(ln[6]) if len(ln) >= 7 and ln[6].isdigit() else None))
    return out

def shop_catalog():
    """Artículos de la tienda, 17 B cada uno (FUN_006c1c70): u32 id artículo, u8 tipo, u32 param, u32 precio MP, 4×u8.
    Tipo 0 = carta suelta (param = id de carta), tipo 1 = booster pack (param = id de pack de packinfo_*.txt:
    1-45 normales, 1001-1003 principiante, 7001-8006 decks de estructura; 9001-9002 = premios, no se venden, B71)."""
    items, iid, pr = [], 1, srv.shop_prices()
    packs = [pid for pid, kind, _ in srv.packinfo_packs() if kind != 2 or srv.SHOP_SELL_PRIZE_PACKS]
    for pid in packs:
        cost = 100 if 1001 <= pid <= 1003 else 1000 if pid >= 7000 else 200
        cost = pr.get(str(pid), pr.get("principiante" if 1001 <= pid <= 1003 else "estructura" if pid >= 7000 else "sobre", cost))
        items.append(proto.W().u32(iid).u8(1).u32(pid).u32(cost).u8(0).u8(0).u8(0).u8(0).bytes()); iid += 1
    for cid in srv.SHOP_CARDS:
        items.append(proto.W().u32(iid).u8(0).u32(cid).u32(pr.get("carta", 100)).u8(0).u8(0).u8(0).u8(0).bytes()); iid += 1
    if srv.ns.get("AV") is not None:          # ropa y piezas de avatar (la ventana filtra por sexo y mundo)
        items += srv.AV.avatar_shop_items(iid)
    return items

PACK_SIZE = 5   # cartas por sobre (supuesto; no sale del código)
MAX_CARD = 0xcb8   # nº de cartas del cliente (FUN_006c1f30 solo cuenta ids < 0xcb9)

def shop_purchase(kind, prm, num):
    """Cartas que da una compra: [(tipo, id carta)]. Carta suelta = ella misma; pack = PACK_SIZE cartas al azar
    de <id>.iid; deck de estructura = todas las cartas de <id>.ydc. Ambos ficheros son listas de u16 LE (los
    .ydc rellenan con ceros). Nunca se manda un id fuera de 1..MAX_CARD: el juego se cierra al pintarlo."""
    import random
    if kind == 0:
        cards = [prm] * num
    else:
        base = os.path.join(os.path.dirname(srv.PACKINFO), str(prm))
        cards = []
        for ext in (".iid", ".ydc"):
            try:
                raw = open(base + ext, "rb").read()
            except OSError:
                continue
            ids = [x for x in struct.unpack(f"<{len(raw) // 2}H", raw[:len(raw) // 2 * 2]) if x]
            cards = ([random.choice(ids) for _ in range(srv.PACK_SIZE * num)] if ext == ".iid" else ids * num)
            break
    return [(kind, x) for x in cards if 0 < x <= srv.MAX_CARD]

SHOP_ENTRIES_PER_MSG = 78   # B04: 0x5212 = s32 + 13 B por carta; 4 + 13·78 = 1018 B <= 1024 (FUN_00689ea0)
SHOP_ANIM_MAX = 80          # la animación de cartas obtenidas solo enseña 80 (FUN_006aefc0 ignora el índice >= 0x50)
SHOP_AVATAR_ITEMGET = False # B69: el cliente ya pone el bit de la prenda (0x856369); True = mandar además 0x5205

def shop_buy(c, data):
    """0x5210 REQSHOPRESULT. B33: es un CARRITO (FUN_006ce830 escribe una pareja u32 artículo + u8 cantidad por fila
    con cantidad > 0, y FUN_00b01410 suma precio × cantidad de todas); antes solo se leía la primera. Se compra todo
    o nada: si el saldo no llega para el total, resultado 1 (el cliente ya lo comprueba en local, FUN_00afd0d0 caso 5).
    Prendas (tipo 2, param = bit PBIN): 1 unidad y nunca una que ya se tenga. Cartas y sobres: una entrada de 0x5212
    por carta (el cliente suma 1 copia por entrada, FUN_006c1f30). B04: 0x5212 en trozos de <= 78 entradas (cada uno
    con su s32 0; el cliente los acumula) y un único 0x5211 al final. Sustituye a AV.purchase_avatar y
    PH["h_shop_bought"] (la regla del baúl sigue siendo PP.on_shop_purchase)."""
    def fail(why):
        srv.log(f"[tienda] compra rechazada: {why}")
        c.send(0x5212, proto.W().u32(1).bytes()); c.send(0x5211, proto.W().u32(1).bytes())
    if len(data) % 5:
        srv.log(f"[tienda] 0x5210 de {len(data)} B (no es múltiplo de 5): se ignoran los {len(data) % 5} últimos")
    cart = [struct.unpack(">IB", data[i:i + 5]) for i in range(0, len(data) - len(data) % 5, 5)]
    cat = srv.shop_catalog()
    AV, PP = srv.AV, srv.PP
    av_ok = AV is not None and PP is not None
    av_valid = getattr(AV, "PBIN", None) if AV is not None else None
    err = None
    with srv.STATE_LOCK:                           # leer-modificar-guardar sin pisar a otro hilo; nada se manda aquí dentro
        st = srv.load_state() if PP is not None else None
        p = PP.profile(st, PP.active_slot(st)) if PP is not None else None
        owned = set(p.get("pbin", [])) if p is not None else set()
        plan, total = [], 0                    # [(id artículo, tipo, param, [(tipo, carta)] | None)]
        for iid, num in cart:
            if not 0 < iid <= len(cat) or num == 0:
                srv.log(f"[tienda] artículo {iid} x{num} no válido: se salta")
                continue
            it = cat[iid - 1]; kind = it[4]
            prm, cost = struct.unpack(">I", it[5:9])[0], struct.unpack(">I", it[9:13])[0]
            if kind == 2:                      # pieza de avatar = un bit PBIN (agente 4)
                if not av_ok or (av_valid is not None and prm not in av_valid) or prm in owned:
                    srv.log(f"[tienda] prenda {iid} (bit {prm}) no disponible o ya comprada: se salta")
                    continue
                owned.add(prm); plan.append((iid, 2, prm, None)); total += cost
                continue
            cards = srv.shop_purchase(kind, prm, num)   # al azar: se calcula una vez y esa misma lista va al baúl
            if not cards:
                srv.log(f"[tienda] artículo {iid} (tipo {kind}, {prm}) sin cartas: no se cobra")
                continue
            plan.append((iid, kind, prm, cards)); total += cost * num
        mp = (p["rec"]["mp"] if p is not None else srv.wallet()["mp"]) if plan else 0
        bought = [x for _, kind, _, cards in plan if kind != 2 for x in cards]
        # B40: u8 «nueva» de cada carta = 1 solo si el baúl no la tenía antes de esta compra (la 2.ª copia de una carta
        # nueva en el mismo carrito ya no lo es), como 0x4803 (lobby) y 0x4903 (persistencia) y como lo calcula el
        # propio cliente en 0x8102 (FUN_006c01b0: kaban[carta] == 0). El u8 va a lobby+0x1350+i (FUN_006aefc0) y
        # CCardGetResultScene pinta «Nuevo» (texto 5310). Sin persistencia no hay baúl en el servidor: todas 1.
        have = (p.get("trunk") or {}) if p is not None else None
        seen, new_flags = set(), []
        for _, cid in bought:
            new_flags.append(1 if have is None or (int(have.get(str(cid), 0) or 0) == 0 and cid not in seen) else 0)
            seen.add(cid)
        recipes = []                           # B68: bits «GET済み» de los mazos comprados
        if srv.SHOP_DECK_RECIPE and p is not None:
            deck_bits = {pid: bit for pid, k, bit in srv.packinfo_packs() if k == 1 and bit is not None}
            recipes = sorted({deck_bits[prm] + 1 for _, kind, prm, _ in plan if kind == 1 and prm in deck_bits})
        if not plan:
            err = "carrito vacío o sin artículos válidos"
        elif mp < total:
            err = f"saldo {mp} < total {total}"
        elif p is not None:
            p["rec"]["mp"] = mp - total
            PP.on_shop_purchase(p, bought)
            for _, kind, prm, _ in plan:
                if kind == 2:
                    PP.pbin_set(p, prm)
            for bit in recipes:
                PP.pbin_set(p, bit)
            srv.save_state(st)
        else:
            srv.set_wallet(mp=mp - total)
    if err:
        return fail(err)
    entries, nf = [], iter(new_flags)          # (id artículo, tipo, carta | bit PBIN, u8 «nueva»)
    for iid, kind, prm, cards in plan:
        entries += [(iid, 2, prm, 1)] if kind == 2 else [(iid, k, card, next(nf, 1)) for k, card in cards]
    srv.log(f"[tienda] carrito {cart}: {len(plan)} artículos, {len(entries)} entradas ({sum(new_flags)} nuevas), "
        f"total {total} MP, saldo {mp - total}" + (f", recetas PBIN {recipes}" if recipes else ""))
    srv._admin_ev("compra", getattr(c, "sess", None), sobres=sum(1 for x in plan if x[1] == 1),
              cartas_sueltas=sum(1 for x in plan if x[1] == 0), prendas=sum(1 for x in plan if x[1] == 2),
              cartas=len(bought), mp=total, saldo=mp - total)   # panel: Actividad
    if len(bought) > srv.SHOP_ANIM_MAX:
        srv.log(f"[tienda] {len(bought)} cartas: la animación solo enseña {srv.SHOP_ANIM_MAX}; las demás entran en el baúl igual")
    for i in range(0, len(entries), srv.SHOP_ENTRIES_PER_MSG):
        w = proto.W().u32(0)
        for iid, k, card, new in entries[i:i + srv.SHOP_ENTRIES_PER_MSG]:
            w.u32(iid).u8(k).u32(card).u8(new).u8(0).u8(0).u8(0)
        c.send(0x5212, w.bytes())
    c.send(0x5211, proto.W().u32(0).bytes())
    c.send(0x4140, srv.player_data_update())
    bits = [prm for _, kind, prm, _ in plan if kind == 2]
    if bits and srv.SHOP_AVATAR_ITEMGET and hasattr(AV, "itemget_result"):
        c.send(0x5205, AV.itemget_result(bits))
    if recipes and srv.SHOP_DECK_ITEMGET:          # 0x5205 (FUN_006c2b30): u16 n + n×u16 bits, u16 0 cartas (nunca cartas)
        w = proto.W().u16(len(recipes))
        for bit in recipes:
            w.u16(bit)
        c.send(0x5205, w.u16(0).bytes())
