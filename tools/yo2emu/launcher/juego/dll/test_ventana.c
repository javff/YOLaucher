/*
 * test_ventana.c - pruebas de yo2win.h (las cuentas del modo "ventana ampliada"). Se compila con un gcc NORMAL del PC
 * (no con el de 32 bits sin CRT de la DLL) y no abre el juego:
 *
 *   gcc -O1 -Wall -o test_ventana.exe test_ventana.c && test_ventana.exe
 *
 * Lo ejecuta tambien "go test" (juego/ventana_test.go) si encuentra un gcc. Sale con 0 si todo va bien; si no, escribe
 * cada fallo y sale con 1.
 */
#include <stdio.h>
#include "yo2win.h"

static int fallos, total;
#define CHECK(c, ...) do { total++; if (!(c)) { fallos++; printf("FALLO linea %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static void t_texto(void) {
    CHECK(vw_modo_de_texto("") == VW_ORIG, "vacio");
    CHECK(vw_modo_de_texto(NULL) == VW_ORIG, "NULL");
    CHECK(vw_modo_de_texto("orig") == VW_ORIG, "orig");
    CHECK(vw_modo_de_texto("Original") == VW_ORIG, "Original");
    CHECK(vw_modo_de_texto("0") == VW_ORIG, "0");
    CHECK(vw_modo_de_texto("1.5") == VW_X15, "1.5");
    CHECK(vw_modo_de_texto("1.5x") == VW_X15, "1.5x");
    CHECK(vw_modo_de_texto(" 1.5X\r\n") == VW_X15, "1.5X con espacios");
    CHECK(vw_modo_de_texto("2") == VW_X2, "2");
    CHECK(vw_modo_de_texto("2x") == VW_X2, "2x");
    CHECK(vw_modo_de_texto("fit") == VW_FIT, "fit");
    CHECK(vw_modo_de_texto("FIT") == VW_FIT, "FIT");
    CHECK(vw_modo_de_texto("3") == -1, "3 no existe");
    CHECK(vw_modo_de_texto("fit2") == -1, "fit2 no existe");
    CHECK(vw_modo_de_texto("1.50") == -1, "1.50 no existe");
}

static void t_division(void) {
    CHECK(vw_fdiv(7, 2) == 3 && vw_fdiv(-7, 2) == -4 && vw_fdiv(-8, 2) == -4 && vw_fdiv(0, 5) == 0, "fdiv");
    CHECK(vw_cdiv(7, 2) == 4 && vw_cdiv(-7, 2) == -3 && vw_cdiv(-8, 2) == -4 && vw_cdiv(0, 5) == 0, "cdiv");
}

/* Casos concretos: ventana 2x, 1.5x y "fit" 1440x1080; esquina (0,0) y esquina opuesta, centro, y fuera. */
static void t_mapa_concreto(void) {
    /* 2x: cliente 2048x1536 en pantalla desde (100, 50) */
    CHECK(vw_a_juego(100, 100, 2048, VW_BASE_W) == 0, "2x esquina izq");
    CHECK(vw_a_juego(100 + 2047, 100, 2048, VW_BASE_W) == 1023, "2x ultimo pixel x = %d", vw_a_juego(100 + 2047, 100, 2048, VW_BASE_W));
    CHECK(vw_a_juego(50 + 1535, 50, 1536, VW_BASE_H) == 767, "2x ultimo pixel y");
    CHECK(vw_a_juego(100 + 1024, 100, 2048, VW_BASE_W) == 512, "2x centro x");
    CHECK(vw_a_juego(99, 100, 2048, VW_BASE_W) == -1, "2x justo a la izquierda de la ventana = -1 (fuera)");
    CHECK(vw_a_juego(100 + 2048, 100, 2048, VW_BASE_W) == 1024, "2x justo a la derecha = 1024 (fuera)");
    /* 1.5x: 1536x1152 */
    CHECK(vw_a_juego(768, 0, 1536, VW_BASE_W) == 512, "1.5x centro");
    CHECK(vw_a_juego(1535, 0, 1536, VW_BASE_W) == 1023, "1.5x ultimo");
    CHECK(vw_a_juego(1, 0, 1536, VW_BASE_W) == 0 && vw_a_juego(2, 0, 1536, VW_BASE_W) == 1, "1.5x pasos de 1,5");
    /* tamano 1:1 = identidad */
    CHECK(vw_a_juego(333, 0, 1024, VW_BASE_W) == 333 && vw_a_juego(333 + 10, 10, 1024, VW_BASE_W) == 333, "1:1 identidad");
    CHECK(vw_a_real(333, 10, 1024, VW_BASE_W) == 343, "1:1 inversa");
    /* fit en un monitor de 1920x1080: 1440x1080, centrado con 240 de banda a cada lado */
    {
        int x, y, w, h;
        vw_ajustar(1920, 1080, &x, &y, &w, &h);
        CHECK(w == 1440 && h == 1080 && x == 240 && y == 0, "fit 1920x1080 = %d,%d %dx%d", x, y, w, h);
        CHECK(vw_a_juego(240 + 720, 240, w, VW_BASE_W) == 512, "fit centro");
    }
    /* sin ventana valida (tam 0): no se divide por cero */
    CHECK(vw_a_juego(500, 100, 0, VW_BASE_W) == 400 && vw_a_real(400, 100, 0, VW_BASE_W) == 500, "tam 0");
}

/* Propiedad: para todo tam >= base y todo pixel del juego g, a_juego(a_real(g)) == g. Y a_juego es monotona. */
static void t_ida_y_vuelta(void) {
    static const int tams[] = {1024, 1025, 1100, 1280, 1366, 1440, 1536, 1600, 1707, 1920, 2048, 2560, 3000, 3840, 4096};
    static const int origenes[] = {0, 1, -7, 100, 509, -1920, 2560};
    int ti, oi, g, mal = 0, malmono = 0;
    for (ti = 0; ti < (int)(sizeof tams / sizeof tams[0]); ti++)
        for (oi = 0; oi < (int)(sizeof origenes / sizeof origenes[0]); oi++) {
            int tam = tams[ti], org = origenes[oi], prev = -100000;
            for (g = 0; g < VW_BASE_W; g++) {
                int r = vw_a_real(g, org, tam, VW_BASE_W);
                if (vw_a_juego(r, org, tam, VW_BASE_W) != g) mal++;
            }
            for (g = org - 50; g < org + tam + 50; g++) {
                int j = vw_a_juego(g, org, tam, VW_BASE_W);
                if (j < prev) malmono++;
                prev = j;
            }
        }
    CHECK(mal == 0, "ida y vuelta (x): %d fallos", mal);
    CHECK(malmono == 0, "monotonia: %d fallos", malmono);
    /* el mismo para y con los tamanos 4:3 de arriba */
    mal = 0;
    for (ti = 0; ti < (int)(sizeof tams / sizeof tams[0]); ti++) {
        int tam = tams[ti] * 3 / 4;
        if (tam < VW_BASE_H) continue;
        for (g = 0; g < VW_BASE_H; g++) {
            int r = vw_a_real(g, 30, tam, VW_BASE_H);
            if (vw_a_juego(r, 30, tam, VW_BASE_H) != g) mal++;
        }
    }
    CHECK(mal == 0, "ida y vuelta (y): %d fallos", mal);
    /* cada pixel real dentro de la ventana cae en 0..1023 */
    mal = 0;
    for (g = 0; g < 2048; g++) {
        int j = vw_a_juego(g, 0, 2048, VW_BASE_W);
        if (j < 0 || j > 1023) mal++;
    }
    CHECK(mal == 0, "los pixeles de dentro caen en 0..1023");
}

static void t_ajustar(void) {
    struct { int mw, mh, x, y, w, h; } c[] = {
        {1920, 1080, 240, 0, 1440, 1080},
        {2560, 1440, 320, 0, 1920, 1440},
        {3840, 2160, 480, 0, 2880, 2160},
        {1024, 768, 0, 0, 1024, 768},
        {1280, 1024, 0, 32, 1280, 960},     /* 5:4: bandas arriba y abajo */
        {1366, 768, 171, 0, 1024, 768},
        {1600, 900, 200, 0, 1200, 900},
        {1280, 800, 106, 0, 1067, 800},
        {800, 600, 0, 0, 800, 600},
    };
    int i;
    for (i = 0; i < (int)(sizeof c / sizeof c[0]); i++) {
        int x, y, w, h;
        vw_ajustar(c[i].mw, c[i].mh, &x, &y, &w, &h);
        CHECK(x == c[i].x && y == c[i].y && w == c[i].w && h == c[i].h, "ajustar %dx%d -> %d,%d %dx%d (esperado %d,%d %dx%d)",
              c[i].mw, c[i].mh, x, y, w, h, c[i].x, c[i].y, c[i].w, c[i].h);
        CHECK(x >= 0 && y >= 0 && x + w <= c[i].mw && y + h <= c[i].mh, "ajustar %dx%d cabe", c[i].mw, c[i].mh);
    }
    {   /* para cualquier monitor razonable: cabe, es (casi) 4:3 y toca dos bordes opuestos */
        int mw, mh, mal = 0, malasp = 0;
        for (mw = 640; mw <= 5120; mw += 37)
            for (mh = 480; mh <= 2880; mh += 41) {
                int x, y, w, h;
                vw_ajustar(mw, mh, &x, &y, &w, &h);
                if (x < 0 || y < 0 || x + w > mw || y + h > mh) mal++;
                if (!((w == mw) || (h == mh))) mal++;
                if (w * 3 - h * 4 > 4 || h * 4 - w * 3 > 4) malasp++;
            }
        CHECK(mal == 0, "ajustar: cabe y toca un borde (%d fallos)", mal);
        CHECK(malasp == 0, "ajustar: 4:3 (%d fallos)", malasp);
    }
}

static void t_cliente(void) {
    int w, h;
    vw_cliente(VW_X2, 4000, 3000, &w, &h);
    CHECK(w == 2048 && h == 1536, "2x cabe: %dx%d", w, h);
    vw_cliente(VW_X15, 4000, 3000, &w, &h);
    CHECK(w == 1536 && h == 1152, "1.5x cabe: %dx%d", w, h);
    vw_cliente(VW_X2, 2560, 1392 - 31, &w, &h);       /* 1440p con barra de tareas y titulo: el alto manda */
    CHECK(h <= 1361 && w * 3 == h * 4 && w >= 1024, "2x en 1440p: %dx%d", w, h);
    vw_cliente(VW_X15, 1920, 1032 - 31, &w, &h);       /* 1080p: 1.5x = 1152 de alto no cabe en 1001 */
    CHECK(h <= 1001 && w * 3 == h * 4 && w >= 1024, "1.5x en 1080p: %dx%d", w, h);
    vw_cliente(VW_X2, 1280, 720, &w, &h);              /* cabe menos que el original: se queda en el original */
    CHECK(w == 1024 && h == 768, "2x en pantalla pequena: %dx%d", w, h);
    vw_cliente(VW_X2, 0, 0, &w, &h);                   /* sin datos de pantalla: lo pedido */
    CHECK(w == 2048 && h == 1536, "2x sin datos: %dx%d", w, h);
    vw_cliente(VW_ORIG, 4000, 3000, &w, &h);
    CHECK(w == 1024 && h == 768, "original");
    {   /* siempre 4:3 exacto */
        int dw, dh, mal = 0, m;
        for (m = VW_X15; m <= VW_X2; m++)
            for (dw = 900; dw <= 5000; dw += 53)
                for (dh = 700; dh <= 3000; dh += 47) {
                    vw_cliente(m, dw, dh, &w, &h);
                    if (w * 3 != h * 4 || w < 1024) mal++;
                    if (w > 1024 && (w > dw || h > dh)) mal++;
                }
        CHECK(mal == 0, "cliente: 4:3 exacto y cabe (%d fallos)", mal);
    }
}

int main(void) {
    t_texto();
    t_division();
    t_mapa_concreto();
    t_ida_y_vuelta();
    t_ajustar();
    t_cliente();
    printf("%d comprobaciones, %d fallos\n", total, fallos);
    return fallos ? 1 : 0;
}
