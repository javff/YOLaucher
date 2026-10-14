"""Prepara los graficos del juego (2008) que usa la ventana del lanzador.

Origen (solo lectura): _original/extracted/merge2008/data/ del repo principal (el dueño aprobó incrustarlos en el
lanzador). Salida: frontend/dist/img/game/. Cada PNG es un recorte del original, a veces reescalado UNA vez con
Lanczos para que la pagina lo pinte a 1:1 (sin escalar en el navegador => bordes nitidos) y, en los botones rojos y
las etiquetas de colores, con el tono girado (el juego solo trae azul y dorado).

Uso: python -I graficos.py [carpeta_del_juego]
"""
import os
import sys
import numpy as np
from PIL import Image, ImageOps

_RAIZ = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '..'))  # raíz del repo
ORIG = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_RAIZ, '_original', 'extracted', 'merge2008', 'data')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend', 'dist', 'img', 'game')
os.makedirs(OUT, exist_ok=True)
os.makedirs(os.path.join(OUT, 'osc'), exist_ok=True)
total = 0


def abre(rel):
    return Image.open(os.path.join(ORIG, rel.replace('/', os.sep))).convert('RGBA')


def escala(im, f):
    return im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.LANCZOS)


def tono(im, grados, sat=1.0, val=1.0):
    """Gira el tono (en grados) conservando el alfa."""
    r, g, b, a = im.split()
    hsv = Image.merge('RGB', (r, g, b)).convert('HSV')
    h, s, v = hsv.split()
    d = round(grados / 360 * 256)
    h = h.point(lambda x: (x + d) % 256)
    if sat != 1.0:
        s = s.point(lambda x: max(0, min(255, int(x * sat))))
    if val != 1.0:
        v = v.point(lambda x: max(0, min(255, int(x * val))))
    out = Image.merge('HSV', (h, s, v)).convert('RGB')
    out.putalpha(a)
    return out


def _hsv(im):
    """RGBA -> (h grados 0-360, s 0-1, v 0-1, alfa) como arrays float."""
    r, g, b, a = im.convert('RGBA').split()
    h, s, v = Image.merge('RGB', (r, g, b)).convert('HSV').split()
    return (np.asarray(h, dtype=float) * 360 / 255, np.asarray(s, dtype=float) / 255, np.asarray(v, dtype=float) / 255, a)


def _rgba(h, s, v, a):
    hsv = np.stack([np.clip(h % 360 / 360 * 255, 0, 255), np.clip(s, 0, 1) * 255, np.clip(v, 0, 1) * 255], axis=2).round().astype(np.uint8)
    out = Image.fromarray(hsv, 'HSV').convert('RGB')
    out.putalpha(a)
    return out


def _paso(x, a, b):
    return np.clip((x - a) / (b - a), 0, 1)


def azul_fuerte(im, destino=222.0, fuerza=0.75, sat=1.12, val=1.0):
    """El azul cian del juego (tono ~190) -> azul real «royal» (tono ~215-220), mas saturado y algo mas hondo. Solo toca los
    pixeles azules/cian con color (el dorado, el blanco y el gris quedan como estaban): conserva brillo, forma y reflejos."""
    h, s, v, a = _hsv(im)
    w = _paso(s, 0.05, 0.2) * (h > 165) * (h < 262)
    h2 = destino - (destino - h) * (1 - fuerza)
    s2 = np.minimum(1.0, s * sat + 0.06)
    v2 = v * (1 - (1 - val) * _paso(s, 0.3, 0.7))
    return _rgba(h + (h2 - h) * w, s + (s2 - s) * w, v + (v2 - v) * w, a)


def oscuro(im, relleno, linea, azul=0.82, cuerpo=None):
    """Version oscura de un grafico claro: lo neutro/claro (el cuerpo blanco y su filete oscuro) pasa a un degradado
    linea->relleno segun la luminosidad; lo que tiene color (cabeceras azules, dorado) solo se oscurece un poco."""
    r, g, b, a = im.convert('RGBA').split()
    rgb = np.asarray(Image.merge('RGB', (r, g, b)), dtype=float)
    lum = (0.3 * rgb[..., 0] + 0.59 * rgb[..., 1] + 0.11 * rgb[..., 2]) / 255
    _, s, _, _ = _hsv(im)
    t = _paso(lum, 0.42, 0.94)[..., None]
    base = np.array(linea, dtype=float) * (1 - t) + np.array(relleno, dtype=float) * t
    wn = (1 - _paso(s, 0.12, 0.3))[..., None]           # 1 = neutro
    out = base * wn + rgb * azul * (1 - wn)
    if cuerpo:                                           # el hueco de una ventana: relleno liso con un velo de luz arriba
        x0, y0, x1, y1 = cuerpo
        velo = np.array(relleno, dtype=float) * (1 + 0.38 * (1 - _paso(lum, 0.55, 0.97)))[..., None]
        out[y0:y1, x0:x1] = velo[y0:y1, x0:x1]
    res = Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8), 'RGB')
    res.putalpha(a)
    return res


def guarda(im, nombre, cuantiza=False):
    """Guarda el PNG mas pequeño entre RGBA y paleta (esta ultima solo si no pierde colores, salvo cuantiza=True)."""
    global total
    ruta = os.path.join(OUT, nombre)
    cand = []
    im.save(ruta, optimize=True)
    cand.append(os.path.getsize(ruta))
    cols = im.getcolors(maxcolors=300)
    if cuantiza or (cols is not None and len(cols) <= 256):
        pal = im.quantize(colors=256, method=Image.FASTOCTREE, dither=Image.NONE)
        pal.save(ruta + '.p', format='PNG', optimize=True)
        if cuantiza or os.path.getsize(ruta + '.p') < cand[0]:
            os.replace(ruta + '.p', ruta)
        else:
            os.remove(ruta + '.p')
    n = os.path.getsize(ruta)
    total += n
    print('%-18s %5d B  %dx%d' % (nombre, n, im.width, im.height))


def recorta(im):
    return im.crop(im.split()[3].getbbox())


# ---- capsulas de la tienda (shop_btn1: azul 01, dorada 04): a 42 px de alto = el boton normal del lanzador
az = recorta(abre('shop/shop_btn1_01_c01.png'))
oro = recorta(abre('shop/shop_btn1_04_c01.png'))
f = 42 / az.height
guarda(azul_fuerte(escala(az, f)), 'btn_azul.png')
guarda(escala(oro, f), 'btn_oro.png')
guarda(tono(escala(oro, f), -26, 1.15, 0.92), 'btn_rojo.png')
# ---- boton claro (shop_btn2_02, blanco con borde gris) a 42 px
cl = recorta(abre('shop/shop_btn2_02_c01.png'))
cl_img = escala(cl, 42 / cl.height)
guarda(cl_img, 'btn_claro.png')

# ---- pestañas/chips (option_btn_01: 3 estados de 158x24: azul vivo, azul palido, amarillo) a 40 px
op = abre('common/window/option_btn_01_c01.png')
w3 = op.width // 3
est = [recorta(op.crop((i * w3, 0, (i + 1) * w3, op.height))) for i in range(3)]
# chip normal = el azul vivo (ahora en azul real), chip activo = el amarillo
for nombre, im in (('chip_azul', azul_fuerte(est[0])), ('chip_oro', est[2])):
    guarda(escala(im, 40 / im.height), nombre + '.png')
# etiquetas pequeñas (22 px): oro y azul del juego; violeta, verde, rojo y gris por giro de tono del azul
azr = azul_fuerte(est[0])
for nombre, im in (('tag_oro', est[2]), ('tag_azul', azr),
                   ('tag_violeta', tono(azr, 45, 1.0, 0.95)), ('tag_verde', tono(azr, -78, 1.0, 0.88)),
                   ('tag_rojo', tono(est[2], -30, 1.2)), ('tag_gris', tono(est[0], 0, 0.12, 0.98))):
    guarda(escala(im, 22 / im.height), nombre + '.png')

# ---- marco de ventana (main_win_01: franja azul + cuerpo claro) a 1,5x; rojo para los avisos de error
mw = abre('common/window/main_win_01_c01.png')
mw15 = escala(mw, 1.5)
def win_azul(mw):
    """Ventana en azul real: cabecera y marco con azul_fuerte; el hueco del cuerpo conserva el degradado claro original
    (con menos color) para que las etiquetas y los campos se lean sobre fondo casi blanco."""
    out = azul_fuerte(mw)
    h, s, v, a = _hsv(mw)
    pal = _rgba(h, s * 0.5, v, a)
    caja = (10, 33, 99, 96)
    sub = pal.crop(caja)
    aclara = Image.blend(sub.convert('RGB'), Image.new('RGB', sub.size, (255, 255, 255)), 0.6)
    aclara.putalpha(sub.getchannel('A'))
    out.paste(aclara, caja[:2])
    return out


guarda(win_azul(mw15), 'win.png')
guarda(tono(mw15, 150, 1.0, 1.0), 'win_rojo.png')
# franja del titulo de la ventana: solo columnas centrales del encabezado (degradado vertical) a 2x = 44 px
franja = mw.crop((28, 0, 36, 22))
guarda(azul_fuerte(escala(franja, 2)), 'titulo.png')

# ---- placa de nombre (lobbyname_win_01: azul->cian con borde dorado) para las cintas de seccion
guarda(azul_fuerte(escala(abre('common/window/lobbyname_win_01_c01.png'), 0.8)), 'placa.png')

# ---- ventana blanca redondeada (fuki_win_01) a 0,5x: filas, barras y tarjetas
fk = escala(abre('common/window/fuki_win_01_c01.png'), 0.5)
guarda(fk, 'fila.png')
# la misma ventana teñida de crema dorado (servidor propio, opcion elegida)
r, g, b, a = fk.split()
dor = Image.merge('RGB', (r.point(lambda x: x), g.point(lambda x: int(x * 0.965)), b.point(lambda x: int(x * 0.80))))
dor.putalpha(a)
guarda(dor, 'fila_oro.png')


def contorno_dorado(im):
    """Pasa el filete oscuro de la ventana blanca a dorado (fila elegida): lo oscuro se funde hacia el oro."""
    px = im.load()
    out = im.copy()
    po = out.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            lum = 0.3 * r + 0.59 * g + 0.11 * b
            if lum < 170:
                t = min(1.0, (170 - lum) / 120.0)
                po[x, y] = (round(r + (245 - r) * t), round(g + (178 - g) * t), round(b + (0 - b) * t), a)
    return out


guarda(contorno_dorado(fk), 'fila_sel.png')
guarda(contorno_dorado(dor), 'fila_orosel.png')

# ---- botones de la ventana y de la barra lateral (iconbar_01: cubo azul brillante, 3 estados de 24x24; se quita la
# base clara de las 3 ultimas filas para que el boton termine en su linea azul oscura) a 1,5x
ib = abre('common/window/iconbar_01_c01.png')


def rojo(im):
    """Cubo rojo: colorea por luminancia (girar el tono deja los brillos cian en rosa)."""
    r, g, b, a = im.split()
    lum = Image.merge('RGB', (r, g, b)).convert('L')
    col = ImageOps.colorize(lum, black=(70, 0, 12), mid=(226, 44, 56), white=(255, 238, 238), midpoint=118)
    col.putalpha(a)
    return col


for i, n in enumerate(('n', 'h', 'p')):
    t = escala(ib.crop((i * 24, 0, (i + 1) * 24, 21)), 1.5)
    guarda(azul_fuerte(t), 'cubo_%s.png' % n)
    guarda(rojo(t), 'cubo_rojo_%s.png' % n)
# estado activo de la barra lateral: cuadrado dorado (main_btn_01, 3 estados de 24x24) a 1,5x
mb = abre('common/window/main_btn_01_c01.png')
guarda(escala(mb.crop((24, 0, 48, 24)), 1.5), 'cubo_oro.png')

# ---- fondo: emblema «YU-GI-OH! ONLINE SYSTEM» (anillo de texto + anillo interior) y la cuadricula azul
a2 = abre('menu/menu_mark_a01_2_j.png')
a1 = abre('menu/menu_mark_a01_1_j.png')
lienzo = Image.new('RGBA', a2.size, (0, 0, 0, 0))
lienzo.alpha_composite(a2)
lienzo.alpha_composite(a1)
emb = escala(recorta(lienzo), 0.8)
guarda(azul_fuerte(emb), 'emblema.png', cuantiza=True)
bg = abre('menu/abmake_bg_01.png')
ruta = os.path.join(OUT, 'fondo.jpg')
azul_fuerte(bg).convert('RGB').save(ruta, quality=82, optimize=True)
total += os.path.getsize(ruta)
print('%-18s %5d B' % ('fondo.jpg', os.path.getsize(ruta)))

# ---- barra de desplazamiento (shop_scrollbar1 = pulgar azul; scroll_center = carril), barra de carga y casilla
sb = recorta(abre('shop/shop_scrollbar1_01_c01.png'))
guarda(azul_fuerte(sb), 'scroll_pulgar.png')
guarda(abre('shop/scroll_center.png'), 'scroll_carril.png')
guarda(abre('common/window/loadbar_01_c00.png'), 'carga.png')
ch = abre('common/window/check_01_c00.png')
caja = recorta(ch.crop((0, 0, 16, 16)))
visto = recorta(ch.crop((16, 0, 32, 16)))
cn = Image.new('RGBA', (30, 30), (0, 0, 0, 0))
cn.alpha_composite(escala(caja, 1.5), (0, 3))
guarda(cn, 'casilla.png')
cs = Image.new('RGBA', (30, 30), (0, 0, 0, 0))
cs.alpha_composite(escala(caja, 1.5), (0, 3))
vv = escala(visto, 1.5)
cs.alpha_composite(vv, (4, 0))
guarda(cs, 'casilla_on.png')
# flecha de los desplegables (shop_arrow_01: triangulo naranja)
fl = recorta(abre('shop/shop_arrow_01_c01.png'))
guarda(escala(fl, 18 / fl.width), 'flecha.png')

# ====================================================================================================================
# TEMA OSCURO (img/game/osc/): las mismas piezas en azul marino. Los botones azules, dorados y rojos, las etiquetas,
# los cubos y los orbes valen para los dos temas; solo se oscurecen lo blanco (tarjetas, cuerpo de ventana, botón
# claro, carril), la cabecera de las ventanas, el fondo y el emblema.
# ====================================================================================================================
TARJ = (21, 41, 82)      # relleno de tarjeta
TARJ_L = (74, 120, 205)  # filete de tarjeta
guarda(oscuro(fk, TARJ, TARJ_L), 'osc/fila.png')
guarda(oscuro(fk, (55, 47, 22), (214, 168, 34)), 'osc/fila_oro.png')
guarda(oscuro(fk, TARJ, (255, 200, 20)), 'osc/fila_sel.png')
guarda(oscuro(fk, (55, 47, 22), (255, 200, 20)), 'osc/fila_orosel.png')
guarda(oscuro(azul_fuerte(mw15), (17, 33, 68), (70, 112, 196), azul=0.64, cuerpo=(10, 33, 99, 96)), 'osc/win.png')
guarda(oscuro(tono(mw15, 150, 1.0, 1.0), (50, 22, 34), (190, 80, 90), azul=0.66, cuerpo=(10, 33, 99, 96)), 'osc/win_rojo.png')
guarda(oscuro(cl_img, (30, 62, 128), (86, 134, 224)), 'osc/btn_claro.png')
guarda(oscuro(abre('shop/scroll_center.png'), (13, 26, 56), (30, 54, 108)), 'osc/scroll_carril.png')
guarda(tono(azul_fuerte(escala(franja, 2)), 0, 1.0, 0.78), 'osc/titulo.png')


def fondo_oscuro(im):
    """Cuadricula del juego en azul noche: luminosidad -> degradado marino (las lineas de la cuadricula quedan algo mas claras)."""
    g = ImageOps.autocontrast(im.convert('L'), cutoff=1)
    col = ImageOps.colorize(g, black=(7, 16, 40), white=(26, 56, 118), mid=(14, 33, 78), midpoint=150)
    return col


ruta = os.path.join(OUT, 'osc', 'fondo.jpg')
fondo_oscuro(bg).save(ruta, quality=82, optimize=True)
total += os.path.getsize(ruta)
print('%-18s %5d B' % ('osc/fondo.jpg', os.path.getsize(ruta)))
# emblema: mas claro y azulado, para que se lea sobre el fondo oscuro (la pagina le baja la opacidad)
r, g, b, a = azul_fuerte(emb).split()
guarda(Image.merge('RGBA', (r.point(lambda x: 70 + x * 100 // 255), g.point(lambda x: 130 + x * 90 // 255), b.point(lambda x: 230), a)), 'osc/emblema.png', cuantiza=True)

print('TOTAL', total, 'B =', round(total / 1024, 1), 'KB')
