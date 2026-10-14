"""Empaqueta duelcore (motor de duelos, tools/yo2emu/duelcore/duelcore.mts) en UN solo fichero: duelcore.mjs.

    python paquete/bundle_duelcore.py                      # -> paquete/_build/duelcore.mjs
    python paquete/bundle_duelcore.py --salida <fichero.mjs>

Por qué: en el paquete del Discord no va node_modules (pnpm, tsx, TypeScript). Con el .mjs basta un node.exe >= 22.13
(duelcore usa node:sqlite para leer cards.cdb): `node duelcore.mjs`. Ver paquete/DUELCORE.md.

Usa el esbuild que YA está instalado en node_modules (pnpm: .pnpm/@esbuild+win32-x64@<versión>); no descarga nada.
- El núcleo WASM de ocgcore-wasm ya viene incrustado en base64 en su dist/ (ocgcore.sync-*.js), así que el .mjs no
  necesita ningún .wasm al lado.
- Los `node:*` (fs, path, sqlite, readline, crypto, url) quedan como imports del propio Node.
- cards.cdb, strings.conf y CardScripts NO se incrustan: los lee de VENDOR_DIR (variable de entorno). Sin ella, data.ts
  los busca en <carpeta del .mjs>/../../../../_vendor, que en el paquete no existe: hay que pasar VENDOR_DIR siempre.

Stdlib pura (Python 3.12). Tarda < 1 s.
"""
import argparse
import glob
import hashlib
import os
import re
import subprocess
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))           # tools/yo2emu/paquete
EMU = os.path.dirname(AQUI)                                  # tools/yo2emu
ROOT = os.path.normpath(os.path.join(EMU, "..", ".."))       # raíz del repo (DuelCards)
ENTRADA = os.path.join(EMU, "duelcore", "duelcore.mts")
SALIDA = os.path.join(AQUI, "_build", "duelcore.mjs")
ESBUILD_VERSION = "0.28.2"


def buscar_esbuild(root=ROOT):
    """esbuild.exe del node_modules de pnpm (la versión fijada primero; si no, cualquiera)."""
    pnpm = os.path.join(root, "node_modules", ".pnpm")
    fijo = os.path.join(pnpm, f"@esbuild+win32-x64@{ESBUILD_VERSION}", "node_modules", "@esbuild", "win32-x64",
                        "esbuild.exe")
    if os.path.isfile(fijo):
        return fijo
    otros = sorted(glob.glob(os.path.join(pnpm, "@esbuild+win32-x64@*", "node_modules", "@esbuild", "win32-x64",
                                          "esbuild.exe")))
    if otros:
        return otros[-1]
    raise SystemExit(f"no encuentro esbuild.exe en {pnpm} (hace falta `pnpm install` en {root})")


def empaquetar(salida=SALIDA, esbuild=None):
    esbuild = esbuild or buscar_esbuild()
    os.makedirs(os.path.dirname(os.path.abspath(salida)), exist_ok=True)
    cmd = [esbuild, ENTRADA, "--bundle", "--platform=node", "--format=esm", "--target=node22",
           f"--outfile={salida}", "--log-level=warning", "--charset=utf8"]
    r = subprocess.run(cmd, cwd=EMU, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"esbuild falló ({r.returncode}):\n{r.stdout}{r.stderr}")
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)
    datos = open(salida, "rb").read()
    # Comprobaciones baratas de que el resultado es autónomo: sin require() de CommonJS (en ESM fallaría) y sin
    # imports que no sean de Node.
    texto = datos.decode("utf-8", "replace")
    externos = sorted(set(re.findall(r'^import\s.*?from\s+"([^"]+)";?$', texto, re.M)))
    raros = [x for x in externos if not x.startswith("node:")]
    if raros:
        raise SystemExit(f"el .mjs importa módulos que no son de Node: {raros}")
    if re.search(r"\brequire\(", texto):
        raise SystemExit("el .mjs lleva require() de CommonJS: en ESM fallaría al ejecutarse (revisar dependencias)")
    return {"salida": os.path.abspath(salida), "bytes": len(datos), "sha256": hashlib.sha256(datos).hexdigest(),
            "imports": externos, "esbuild": esbuild}


def main():
    ap = argparse.ArgumentParser(description="duelcore.mts -> un solo duelcore.mjs (esbuild de node_modules)")
    ap.add_argument("--salida", default=SALIDA, help="fichero .mjs de salida (por defecto paquete/_build/duelcore.mjs)")
    a = ap.parse_args()
    r = empaquetar(a.salida)
    print(f"{r['salida']}: {r['bytes']:,} B  sha256 {r['sha256']}")
    print("imports de Node:", ", ".join(r["imports"]))


if __name__ == "__main__":
    main()
