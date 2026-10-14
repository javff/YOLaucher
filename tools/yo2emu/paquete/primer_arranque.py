"""Preparación del anfitrión antes de arrancar el servidor desde el PAQUETE (P5, 11-10). La lanza YO2OnlineLauncher.exe
(«Host a server») cada vez, con el Python de dentro del paquete; es idempotente y tarda < 1,5 s.

    python primer_arranque.py --juego <carpeta del cliente 2008> --salida <YO2_JUEGO_DATOS> --vendor <YO2_VENDOR>

1. paquete/extraer_juego.py: saca del merge.dat del anfitrión los datos que lee el servidor (en el zip no va nada de
   Konami). Comprueba que es yo2.exe Ver.080912 y el sha256 de cada fichero.
2. paquete/generar_tablas.py: rehace junto al servidor las tablas que salen del cliente (cards_yo2.csv, npc_decks.json,
   npc_profiles.json, valid_cards.json y titles.json; esta última desde yo2.exe). PUB 12-10: ni el zip ni el código
   abierto las llevan.

Todo lo que imprime es para el jugador que hospeda: en INGLÉS. Salidas: 0 bien; 2 carpeta del juego mala (no es el
cliente 2008, falta merge.dat...); 3 datos del juego distintos de los esperados; 4 no se pudieron hacer las tablas.
"""
import argparse
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
if AQUI not in sys.path:
    sys.path.insert(0, AQUI)
import extraer_juego  # noqa: E402
import generar_tablas  # noqa: E402


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Prepare the game data the YO2 server needs (first start of a host).")
    ap.add_argument("--juego", required=True, help="game folder (the 2008 client, Ver.080912)")
    ap.add_argument("--salida", required=True, help="where the extracted game data goes (YO2_JUEGO_DATOS)")
    ap.add_argument("--vendor", required=True, help="folder with cards.cdb (YO2_VENDOR)")
    a = ap.parse_args()
    rc, _ = extraer_juego.extraer(a.juego, a.salida)
    if rc != 0:
        return rc
    rc, texto = generar_tablas.generar(os.path.abspath(a.salida), os.path.abspath(a.vendor), juego=os.path.abspath(a.juego))
    print(texto)
    if rc != 0:
        return 4
    print("Game data ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
