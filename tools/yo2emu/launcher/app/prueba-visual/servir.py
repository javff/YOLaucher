"""Sirve frontend/dist con el Go de mentira (fakego.js) para ver y capturar la ventana del lanzador en un navegador normal.

    python servir.py [puerto]            (por defecto 8919; solo 127.0.0.1)
    http://127.0.0.1:8919/?tema=dark&srv=1&abierto=0

No es parte del exe (frontend/dist es lo que se incrusta; esta carpeta no). Ver fakego.js para los parámetros de la URL.
"""
import http.server
import os
import socketserver
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.normpath(os.path.join(AQUI, "..", "frontend", "dist"))


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DIST, **k)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        ruta = self.path.split("?")[0]
        if ruta == "/fakego.js":
            data = open(os.path.join(AQUI, "fakego.js"), "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if ruta in ("/", "/index.html"):
            html = open(os.path.join(DIST, "index.html"), "rb").read().decode("utf-8")
            html = html.replace('<script src="tema.js"></script>', '<script src="fakego.js"></script>\n<script src="tema.js"></script>', 1)
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()


if __name__ == "__main__":
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 8919
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", puerto), H) as s:
        print(f"http://127.0.0.1:{puerto}/", flush=True)
        s.serve_forever()
