/*
 * yo2win.h - matematica pura (sin Windows, sin CRT) del modo "ventana ampliada" de yo2net.c (rama rx/PAN, 24-10).
 *
 * El juego cree que su ventana y su backbuffer miden 1024x768 (interfaz fija). Con la ventana ampliada la ventana real
 * es mas grande, Direct3D (DXVK o el d3d8 de Windows) estira el backbuffer al presentar, y TODO lo que el juego lee del
 * raton se convierte de la ventana real a 1024x768. Aqui estan las cuentas; yo2net.c las usa y test_ventana.c las prueba
 * en el PC (gcc normal) sin abrir el juego. Todo es entero: nada de float en la DLL.
 *
 * Mapa de coordenadas, por eje:   juego = floor((real - origen) * base / tam)        (a_juego)
 *                                 real  = origen + ceil(juego * tam / base)          (a_real)
 * con origen = esquina del area cliente en pantalla, tam = tamano real del area cliente, base = 1024 (x) o 768 (y).
 * El techo en a_real hace que a_juego(a_real(g)) == g para tam >= base (el redondeo nunca pierde un pixel del juego).
 */
#ifndef YO2WIN_H
#define YO2WIN_H

#define VW_BASE_W 1024
#define VW_BASE_H 768

/* Modos (YO2ONLINE_WINDOW). VW_ORIG = el juego tal cual (la DLL no engancha nada de esto). */
enum { VW_ORIG = 0, VW_X15 = 1, VW_X2 = 2, VW_FIT = 3 };

/* Texto -> modo. -1 si no se entiende (la DLL lo trata como "original"). */
static int vw_modo_de_texto(const char *s) {
    char t[12];
    int i = 0, n;
    while (s && (*s == ' ' || *s == '\t')) s++;
    for (; s && *s && *s != ' ' && *s != '\t' && *s != '\r' && *s != '\n' && i < 11; s++, i++)
        t[i] = (char)((*s >= 'A' && *s <= 'Z') ? *s + 32 : *s);
    t[i] = 0;
    n = i;
    if (n == 0 || (n == 1 && t[0] == '0')) return VW_ORIG;
    if (n == 4 && t[0] == 'o' && t[1] == 'r' && t[2] == 'i' && t[3] == 'g') return VW_ORIG;
    if (n == 8 && t[0] == 'o' && t[1] == 'r' && t[2] == 'i' && t[3] == 'g' && t[4] == 'i' && t[5] == 'n' && t[6] == 'a' && t[7] == 'l')
        return VW_ORIG;
    if ((n == 3 && t[0] == '1' && t[1] == '.' && t[2] == '5') || (n == 4 && t[0] == '1' && t[1] == '.' && t[2] == '5' && t[3] == 'x'))
        return VW_X15;
    if ((n == 1 && t[0] == '2') || (n == 2 && t[0] == '2' && t[1] == 'x')) return VW_X2;
    if (n == 3 && t[0] == 'f' && t[1] == 'i' && t[2] == 't') return VW_FIT;
    return -1;
}

/* Division entera redondeando hacia abajo / hacia arriba (b > 0), tambien con a negativo. */
static int vw_fdiv(int a, int b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }
static int vw_cdiv(int a, int b) { return -vw_fdiv(-a, b); }

/* Un eje: coordenada real (pantalla o cliente) -> coordenada del juego. tam <= 0: sin ventana valida, se deja igual. */
static int vw_a_juego(int real, int origen, int tam, int base) {
    if (tam <= 0) return real - origen;
    return vw_fdiv((real - origen) * base, tam);
}

/* Un eje: coordenada del juego -> coordenada real (inversa de la anterior). */
static int vw_a_real(int juego, int origen, int tam, int base) {
    if (tam <= 0) return origen + juego;
    return origen + vw_cdiv(juego * tam, base);
}

/* "Ajustar a pantalla": el mayor rectangulo 4:3 que cabe en el monitor (mon_w x mon_h), centrado en el. x, y son relativos
 * a la esquina del monitor. Si el monitor es mas ancho que 4:3 salen bandas a los lados; si es mas alto, arriba y abajo. */
static void vw_ajustar(int mon_w, int mon_h, int *x, int *y, int *w, int *h) {
    int cw, ch;
    if (mon_w <= 0 || mon_h <= 0) { *x = *y = 0; *w = VW_BASE_W; *h = VW_BASE_H; return; }
    if (mon_w * VW_BASE_H <= mon_h * VW_BASE_W) {      /* tan ancho como alto o mas estrecho: manda el ancho */
        cw = mon_w;
        ch = vw_fdiv(mon_w * VW_BASE_H + VW_BASE_W / 2, VW_BASE_W);
        if (ch > mon_h) ch = mon_h;
    } else {                                            /* mas ancho que 4:3: manda el alto */
        ch = mon_h;
        cw = vw_fdiv(mon_h * VW_BASE_W + VW_BASE_H / 2, VW_BASE_H);
        if (cw > mon_w) cw = mon_w;
    }
    *w = cw; *h = ch;
    *x = (mon_w - cw) / 2;
    *y = (mon_h - ch) / 2;
}

/* Tamano del AREA CLIENTE para los modos 1.5x y 2x, sin pasarse de lo que cabe (disp_w x disp_h = zona de trabajo menos
 * bordes y barra de titulo). Siempre 4:3 y nunca menor que el original. Si no cabe ni el original, se deja el original. */
static void vw_cliente(int modo, int disp_w, int disp_h, int *w, int *h) {
    int cw = VW_BASE_W, ch = VW_BASE_H, lim;
    if (modo == VW_X15) { cw = VW_BASE_W * 3 / 2; ch = VW_BASE_H * 3 / 2; }
    else if (modo == VW_X2) { cw = VW_BASE_W * 2; ch = VW_BASE_H * 2; }
    else { *w = VW_BASE_W; *h = VW_BASE_H; return; }
    if (disp_w > 0 && disp_h > 0) {
        lim = disp_w;
        if (disp_h * VW_BASE_W / VW_BASE_H < lim) lim = disp_h * VW_BASE_W / VW_BASE_H;  /* el ancho que deja el alto */
        if (lim < cw) {
            cw = lim - lim % 4;                         /* multiplo de 4: ch = cw*3/4 exacto */
            ch = cw * VW_BASE_H / VW_BASE_W;
        }
    }
    if (cw < VW_BASE_W || ch < VW_BASE_H) { cw = VW_BASE_W; ch = VW_BASE_H; }
    *w = cw; *h = ch;
}

#endif
