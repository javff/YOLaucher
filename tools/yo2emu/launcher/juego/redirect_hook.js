// YO2-Online: redirige yo2.exe (Ver.080912) al tunel local del lanzador (127.0.0.1).
// Es la parte FUNCIONAL de ../net_hook.js (sin el registro de diagnostico): el lanzador escucha en 127.0.0.1 en los
// puertos del juego y lo reenvia por Tailscale al PC del owner.
//   - gethostbyname("*.yugioh-online.net") -> "127.0.0.1" (los servidores de Konami ya no existen)
//   - connect(AF_INET, cualquier IP)        -> 127.0.0.1, mismo puerto (la puerta 5730 y los que anuncia el servidor)
//   - InternetOpenUrlA("http://host/...")   -> http://127.0.0.1:8730/... (root.txt y descargas del arranque)
// Lo carga frida-inject (-f yo2.exe -s este fichero); console.log va al log del lanzador. Respaldo de la DLL propia
// (dll/yo2net.c), que hace lo mismo sin frida.
// YO2_OFFSET: el lanzador pone «const YO2_OFFSET = N;» delante (juego.go, Extraer). 0 al jugar; solo «Jugar aquí» en
// pruebas con YO2_PORT_OFFSET lo cambia: entonces la puerta 5730 y el HTTP 8730 van +N (como net_hook.js).
const OFFSET = (typeof YO2_OFFSET === "number") ? YO2_OFFSET : 0;
const LOCAL = "127.0.0.1";
const HTTP_PORT = 8730 + OFFSET;
const ws2 = Module.load("ws2_32.dll");
const wininet = Module.load("wininet.dll");

const localName = Memory.allocAnsiString(LOCAL);
Interceptor.attach(ws2.getExportByName("gethostbyname"), {
  onEnter(a) {
    const n = a[0].isNull() ? "" : a[0].readCString();
    if (/yugioh-online\.net$/i.test(n)) { a[0] = localName; console.log("dns " + n + " -> " + LOCAL); }
  },
});

Interceptor.attach(ws2.getExportByName("connect"), {
  onEnter(a) {
    const sa = a[1];
    if (sa.readU16() !== 2) return; // solo AF_INET
    let port = (sa.add(2).readU8() << 8) | sa.add(3).readU8();
    if (OFFSET && port === 5730) {
      port += OFFSET;
      sa.add(2).writeByteArray([port >> 8, port & 0xff]);
    }
    sa.add(4).writeByteArray([127, 0, 0, 1]);
    console.log("connect -> " + LOCAL + ":" + port);
  },
});

const keep = []; // las cadenas nuevas tienen que vivir mientras WinINet las use
Interceptor.attach(wininet.getExportByName("InternetOpenUrlA"), {
  onEnter(a) {
    if (a[1].isNull()) return;
    const url = a[1].readCString();
    const nu = url.replace(/^http:\/\/[^/]+/i, "http://" + LOCAL + ":" + HTTP_PORT);
    const m = Memory.allocAnsiString(nu);
    keep.push(m);
    a[1] = m;
    console.log("url " + url + " -> " + nu);
  },
});
console.log("redirect_hook cargado");
