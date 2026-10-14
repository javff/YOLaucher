// Frida: red del cliente original. Registra resoluciones DNS, conexiones y lo que se envía/recibe,
// y redirige toda conexión TCP a YO2_SERVER (por defecto 127.0.0.1; mismo puerto) para que la atienda nuestro servidor.
//
// Variables de entorno (las hereda yo2.exe del entorno de run.py; se leen con GetEnvironmentVariableA):
//   YO2_PRINTF        sin definir = printf filtrado (se descartan las texturas de avatar y demás ruido por fotograma,
//                     que eran el 98 % del log, B31) · 0/no/off = sin enganche de printf · 1/all/todo = todo, sin filtro
//   YO2_HOOK_VERBOSE  1 = una línea [cli] seq y [cli] reader por mensaje recibido (comportamiento antiguo). Sin ella
//                     solo se mandan las anomalías (desfase de numeración, errores del lector) y un resumen por minuto.
//   YO2_SERVER        IPv4 del servidor (run.py) al que van TODAS las conexiones y el HTTP; por defecto 127.0.0.1.
//                     PvP V03 (social_pvp.md P0-3): antes todo iba a 127.0.0.1 y un 2.º cliente en otro PC no llegaba.
//                     run_client.py la pone (resuelve antes el nombre si se le da un host).
//   YO2_PORT_OFFSET   se suma al puerto 5730 (la puerta, lo único que el cliente saca de sí mismo) y al HTTP 8730; los
//                     demás puertos ya los anuncia el servidor con el desplazamiento (instancia de prueba).
//   YO2_LOCAL_IP      IP de loopback de origen (127.0.0.N) para dos clientes en el MISMO PC: la puerta 5730 sigue la
//                     numeración por IP del cliente (FUN_0068bac0 + Conn.GATE_SEQ de run.py).
// Si run.py descarga el script (script.unload()), rpc.exports.dispose deja los ret originales en 0x52c360/0x693170
// para que el cliente no salte a un callback liberado (B32), y se puede volver a cargar en el mismo proceso.
// Se carga ya (al arrancar en suspenso aún no está) para poder enganchar sus funciones
const ws2 = Module.load("ws2_32.dll");
const fn = (name) => ws2.getExportByName(name);

// ---- Configuración por variables de entorno
const k32 = Module.load("kernel32.dll");
let _getEnv = null;
try { _getEnv = new NativeFunction(k32.getExportByName("GetEnvironmentVariableA"), "uint32", ["pointer", "pointer", "uint32"], "stdcall"); } catch (e) {}
function envVar(name) {
  if (_getEnv === null) return null;
  try {
    const buf = Memory.alloc(256);
    const n = _getEnv(Memory.allocAnsiString(name), buf, 256);
    if (n === 0 || n >= 256) return null;
    return buf.readAnsiString(n).trim();
  } catch (e) { return null; }
}
const _pf = (envVar("YO2_PRINTF") || "").toLowerCase();
const PRINTF_MODE = ["0", "no", "off", "false"].includes(_pf) ? "off" : (["1", "all", "todo", "true", "yes", "si", "sí"].includes(_pf) ? "all" : "filtro");
const VERBOSE = ["1", "true", "yes", "si", "sí"].includes((envVar("YO2_HOOK_VERBOSE") || "").toLowerCase());
function parseIp(t) {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec((t || "").trim());
  if (!m) return null;
  const b = m.slice(1, 5).map(Number);
  return b.every((x) => x >= 0 && x <= 255) ? b : null;
}
const SERVER = parseIp(envVar("YO2_SERVER")) || [127, 0, 0, 1];
const SERVER_TXT = SERVER.join(".");
const PORT_OFFSET = parseInt(envVar("YO2_PORT_OFFSET") || "0", 10) || 0;
const LOCAL_IP = parseIp(envVar("YO2_LOCAL_IP"));
send({ t: "hook_cfg", printf: PRINTF_MODE, verbose: VERBOSE, server: SERVER_TXT, port_offset: PORT_OFFSET,
       local_ip: LOCAL_IP ? LOCAL_IP.join(".") : null });

// ---- Socket -> puerto del servidor (para decir en qué conexión hay un desfase de numeración, B59).
// Se rellena en connect; si el script se cargó con el cliente ya conectado, se pregunta con getpeername (una vez por socket).
const sockPort = new Map();
let _getpeername = null;
try { _getpeername = new NativeFunction(fn("getpeername"), "int", ["uint32", "pointer", "pointer"], "stdcall"); } catch (e) {}
function portOf(sock) {
  if (sock === null || sock === undefined) return null;
  if (sockPort.has(sock)) return sockPort.get(sock);
  let port = null;
  if (_getpeername !== null) {
    try {
      const sa = Memory.alloc(16), len = Memory.alloc(4);
      len.writeS32(16);
      if (_getpeername(sock, sa, len) === 0 && sa.readU16() === 2) port = (sa.add(2).readU8() << 8) | sa.add(3).readU8();
    } catch (e) {}
  }
  if (sockPort.size > 256) sockPort.clear();
  sockPort.set(sock, port);
  return port;
}
// Estado del desfase por socket: { delta, desde (ms del último aviso), callados (avisos repetidos no mandados), malos }
const seqState = new Map();

// ---- Contadores del resumen por minuto (lo que ya no va línea a línea)
const RES = { printfOmit: 0, printfTop: new Map(), seq: new Map(), reader: 0, readerIds: new Map() };
function bump(map, key, n, cap) {
  if (!map.has(key) && map.size >= cap) key = "(otros)";
  map.set(key, (map.get(key) || 0) + (n || 1));
}
function topOf(map, n) {
  return Array.from(map.entries()).sort((a, b) => b[1] - a[1]).slice(0, n).map(([k, v]) => k + " x" + v);
}
function flushResumen() {
  const r = { t: "hook_resumen", seg: 60 };
  let any = false;
  if (RES.printfOmit) { r.printf_omitidos = RES.printfOmit; r.printf_top = topOf(RES.printfTop, 6); any = true; }
  if (RES.seq.size) {
    r.seq = {};
    for (const [port, s] of RES.seq) r.seq[String(port)] = s;
    any = true;
  }
  if (RES.reader) { r.reader_ok = RES.reader; r.reader_ids = topOf(RES.readerIds, 8); any = true; }
  RES.printfOmit = 0; RES.printfTop.clear(); RES.seq.clear(); RES.reader = 0; RES.readerIds.clear();
  if (any) { try { send(r); } catch (e) {} }
}
const resumenTimer = setInterval(flushResumen, 60000);

function hex(ptr, len) {
  const n = Math.min(len, 512);
  if (n <= 0) return "";
  return hexdump(ptr, { length: n, header: false, ansi: false });
}

const localName = Memory.allocAnsiString(SERVER_TXT);
Interceptor.attach(fn("gethostbyname"), {
  onEnter(args) {
    this.name = args[0].readCString();
    // Los servidores de Konami ya no existen: todo nombre *.yugioh-online.net resuelve a nuestro servidor (YO2_SERVER)
    if (/yugioh-online\.net$/i.test(this.name)) args[0] = localName;
  },
  onLeave(ret) {
    send({ t: "dns", name: this.name, ok: !ret.isNull() });
  },
});

// getaddrinfo por si lo usa
try {
  Interceptor.attach(fn("getaddrinfo"), {
    onEnter(args) {
      send({ t: "dns2", name: args[0].isNull() ? null : args[0].readCString(), port: args[1].isNull() ? null : args[1].readCString() });
    },
  });
} catch (e) {}

let _bind = null;
try { _bind = new NativeFunction(fn("bind"), "int", ["uint32", "pointer", "int"], "stdcall"); } catch (e) {}
Interceptor.attach(fn("connect"), {
  onEnter(args) {
    const sa = args[1];
    const family = sa.readU16();
    if (family !== 2) return; // AF_INET
    const port = (sa.add(2).readU8() << 8) | sa.add(3).readU8();
    const ip = [4, 5, 6, 7].map((i) => sa.add(i).readU8()).join(".");
    const sock = args[0].toUInt32();
    sockPort.set(sock, port); seqState.delete(sock);
    send({ t: "connect", sock: args[0].toInt32(), ip, port });
    // Redirigir a nuestro servidor (YO2_SERVER). La puerta 5730 es el único puerto que el cliente no recibe del
    // servidor: se le suma el desplazamiento; los demás llegan ya desplazados en la lista 0x2003 y en 0x4306.
    sa.add(4).writeByteArray(SERVER);
    if (PORT_OFFSET && port === 5730) {
      const np = port + PORT_OFFSET;
      sa.add(2).writeByteArray([(np >> 8) & 0xff, np & 0xff]);
    }
    // Dos clientes en el mismo PC: cada uno sale de su 127.0.0.N (bind antes de connect; si falla, sigue igual)
    if (LOCAL_IP !== null && _bind !== null) {
      try {
        const la = Memory.alloc(16);
        la.writeU16(2); la.add(2).writeU16(0); la.add(4).writeByteArray(LOCAL_IP);
        _bind(sock, la, 16);
      } catch (e) {}
    }
  },
  onLeave(ret) {
    send({ t: "connect_ret", ret: ret.toInt32() });
  },
});

Interceptor.attach(fn("send"), {
  onEnter(args) {
    const len = args[2].toInt32();
    send({ t: "send", sock: args[0].toInt32(), len, data: hex(args[1], len) });
  },
});

Interceptor.attach(fn("recv"), {
  onEnter(args) {
    this.buf = args[1];
    this.sock = args[0].toInt32();
  },
  onLeave(ret) {
    const n = ret.toInt32();
    if (n > 0) send({ t: "recv", sock: this.sock, len: n, data: hex(this.buf, n) });
    else send({ t: "recv", sock: this.sock, len: n });
  },
});

send({ t: "ready" });

// ---- WinINet (comprobación de parches / descargas HTTP del arranque)
const wininet = Module.load("wininet.dll");
const wi = (name) => wininet.getExportByName(name);
const cstr = (p) => (p.isNull() ? null : p.readCString());
Interceptor.attach(wi("InternetOpenA"), { onEnter(a) { send({ t: "inet_open", agent: cstr(a[0]), access: a[1].toInt32(), proxy: cstr(a[2]) }); } });
// Las URL de Konami se reescriben a nuestro servidor HTTP (run.py, puerto 8730 + desplazamiento, en YO2_SERVER)
const keep = [];
Interceptor.attach(wi("InternetOpenUrlA"), {
  onEnter(a) {
    this.url = cstr(a[1]);
    send({ t: "open_url", url: this.url, headers: cstr(a[2]) });
    const local = this.url.replace(/^http:\/\/[^/]+/, "http://" + SERVER_TXT + ":" + (8730 + PORT_OFFSET));
    const mem = Memory.allocAnsiString(local);
    keep.push(mem);
    a[1] = mem;
  },
  onLeave(r) { send({ t: "open_url_ret", url: this.url, handle: r.toString() }); },
});
Interceptor.attach(wi("InternetConnectA"), {
  onEnter(a) { send({ t: "inet_connect", host: cstr(a[1]), port: a[2].toInt32(), user: cstr(a[3]), service: a[5].toInt32() }); },
  onLeave(r) { send({ t: "inet_connect_ret", handle: r.toString() }); },
});
Interceptor.attach(wi("HttpOpenRequestA"), { onEnter(a) { send({ t: "http_open", verb: cstr(a[1]), path: cstr(a[2]), ver: cstr(a[3]), ref: cstr(a[4]) }); } });
Interceptor.attach(wi("HttpSendRequestExA"), { onLeave(r) { send({ t: "http_sendex_ret", ok: r.toInt32() }); } });
Interceptor.attach(wi("InternetReadFile"), {
  onEnter(a) { this.buf = a[1]; this.n = a[3]; },
  onLeave(r) { const n = this.n.readU32(); send({ t: "inet_read", ok: r.toInt32(), n, data: n ? hex(this.buf, n) : "" }); },
});

// ---- Registro interno del juego (direcciones de YGO2_ADDR_200811.h del parche offline, MIT)
// El juego llama a estas funciones con un formato printf; en la versión publicada no escriben nada.
// 07-10 (sjis): el formato y los %s van en Shift-JIS (cp932). Antes se leían con readCString (UTF-8) y el japonés
// salía roto («�`�����») en la mitad derecha de «[game:…] formato  ||  texto». Ahora se pasan por
// MultiByteToWideChar(932) (la tabla del propio Windows, como el cliente), solo si hay bytes >= 0x80.
let _mb2wc = null;
try { _mb2wc = new NativeFunction(k32.getExportByName("MultiByteToWideChar"), "int", ["uint32", "uint32", "pointer", "int", "pointer", "int"], "stdcall"); } catch (e) {}
function readSjisStr(p, max) {
  if (p.isNull()) return "(null)";
  const lim = max || 1024;
  let n = 0, alto = false;
  for (; n < lim; n++) { const b = p.add(n).readU8(); if (!b) break; if (b >= 0x80) alto = true; }
  if (!alto || _mb2wc === null) return p.readCString(n);
  const out = Memory.alloc(2 * n + 2);
  const w = _mb2wc(932, 0, p, n, out, n);
  return w > 0 ? out.readUtf16String(w) : p.readCString(n);
}
// Secretos que el propio cliente imprime (ID[%s], PW[%s], contraseña guardada, password:%s de la lista de salas):
// no salen del proceso (run.py los tapa también: research/cuentas/logseguro.py).
const FMT_SECRETO = /(?:^|[^\w:])(?:ID|PW)\[%s\]|パスワード|password:%s/;
function fmtArgs(fmt, args, first) {
  let i = first;
  let tpl = fmt;
  try { tpl = readSjisStr(args[0], 400); } catch (e) {}
  const secreto = FMT_SECRETO.test(tpl);
  try {
    return tpl.replace(/%([-+ #0]*)(\d*)(?:\.(\d+))?(hh|h|ll|l|I64|L)?([sdiuxXcpfeEgG%])/g, (m, fl, wd, pr, len, t) => {
      if (t === "%") return "%";
      const a = args[i++];
      if (a === undefined) return m;
      let s;
      if (t === "s") s = secreto ? "***" : readSjisStr(a);
      else if (t === "c") s = String.fromCharCode(a.toInt32() & 0xff);
      else if (t === "x" || t === "p") s = (a.toInt32() >>> 0).toString(16);
      else if (t === "X") s = (a.toInt32() >>> 0).toString(16).toUpperCase();
      else if (t === "u") s = String(a.toInt32() >>> 0);
      else if ("feEgG".includes(t)) {      // double: ocupa dos huecos de 32 bits en la pila (cdecl x86)
        const b = args[i++];
        const buf = new ArrayBuffer(8), dv = new DataView(buf);
        dv.setUint32(0, a.toInt32() >>> 0, true); dv.setUint32(4, b === undefined ? 0 : b.toInt32() >>> 0, true);
        const v = dv.getFloat64(0, true);
        s = pr !== undefined ? v.toFixed(Number(pr)) : String(v);
      }
      else s = String(a.toInt32());
      if (wd && s.length < Number(wd)) {
        const W = Number(wd);
        s = fl.includes("-") ? s.padEnd(W) : (fl.includes("0") && t !== "s" ? s.padStart(W, "0") : s.padStart(W));
      }
      return s;
    });
  } catch (e) {
    return tpl;
  }
}
function readSjis(p) {
  // Las cadenas van en Shift-JIS: se mandan en bytes y run.py las decodifica
  const out = [];
  for (let k = 0; k < 400; k++) { const b = p.add(k).readU8(); if (!b) break; out.push(b); }
  return out;
}
// El registro de verdad (FUN_0052c360, «get msg:YG_MSG_...», avisos en japonés) y el de red (FUN_00693170)
// están vacíos en la versión publicada (ret + nops) y Frida no puede engancharlos: se cambia el ret por un salto
// a un callback. Memory.patchCode NO se deshace al descargar el script, y el callback sí se libera: si el script se
// descarga con el cliente vivo, la siguiente llamada saltaría a memoria liberada (B32). Por eso dispose (abajo)
// vuelve a escribir los bytes originales. Si al cargar ya hay un salto (E9) de un script anterior que murió sin
// limpiar, se repara apuntándolo al callback nuevo; con cualquier otro contenido (otro exe) no se toca.
const LOG_ORIG = [0xc3, 0x90, 0x90, 0x90, 0x90];   // ret + nops del exe de 2008-11 (comprobado en el fichero)
const logCbs = [];
const logPatched = [];
for (const [name, addr] of [["log", 0x0052c360], ["netlog", 0x00693170]]) {
  try {
    const p = ptr(addr);
    const cur = Array.from(new Uint8Array(p.readByteArray(5)));
    const intacto = cur.every((b, k) => b === LOG_ORIG[k]);
    if (!intacto && cur[0] !== 0xe9) {
      send({ t: "hook_error", name, e: "bytes inesperados en " + p + ": " + cur.map(b => b.toString(16)).join(" ") + "; no se parchea" });
      continue;
    }
    if (!intacto) send({ t: "hook_aviso", name, e: "salto huérfano de un script anterior en " + p + "; se repara" });
    const cb = new NativeCallback(function (f, a1, a2, a3, a4, a5) {
      if (f.isNull() || f.compare(ptr(0x10000)) < 0) return;
      let fmt; try { fmt = f.readCString(); } catch (e) { return; }
      if (fmt) send({ t: "glog", fn: name, sjis: readSjis(f), args: fmtArgs(fmt, [f, a1, a2, a3, a4, a5], 1) });
    }, "void", ["pointer", "pointer", "pointer", "pointer", "pointer", "pointer"], "mscdecl");
    logCbs.push(cb);
    Memory.patchCode(p, 5, code => { const w = new X86Writer(code, { pc: p }); w.putJmpAddress(cb); w.flush(); });
    logPatched.push(p);
  } catch (e) {
    send({ t: "hook_error", name, e: String(e) });
  }
}
function restaurarLogs() {
  let n = 0;
  while (logPatched.length) {
    const p = logPatched.pop();
    try { Memory.patchCode(p, 5, code => { code.writeByteArray(LOG_ORIG); }); n++; } catch (e) {}
  }
  return n;
}

// printf interna (0x40818e). El juego la llama en cada fotograma con los nombres de las texturas de los avatares
// (unas 6.000 por segundo en el mapa; el 98 % del log, B31). En modo «filtro» esas se cuentan y no se mandan.
// El formato casi siempre es un literal del exe: se guarda por dirección para no leer la cadena en cada llamada.
const PRINTF_SPAM = /^(?:h1|h2|ey|mo|bo|ac|a1|cw|dc)_|^%d\[%s\]|^m_pMotEngine|^\[%d\]disa/;
const fmtCache = new Map();   // dirección (número) del literal -> formato, o null si es ruido
// Solo se guardan los de .text/.rdata del exe (0x401000-0xc7c000, sin escritura; ahí empieza .data):
// un búfer del montón o de .data puede cambiar de contenido entre llamadas.
const RO_LO = 0x00401000, RO_HI = 0x00c7c000;
function fmtLiteral(key) { return key >= RO_LO && key < RO_HI; }
if (PRINTF_MODE !== "off") {
  for (const [name, addr] of [["printf", 0x0040818e]]) {
    try {
      Interceptor.attach(ptr(addr), {
        onEnter(args) {
          const f = args[0];
          const key = f.toUInt32();   // sin crear punteros: esto corre miles de veces por segundo
          if (key < 0x10000) return;
          let fmt;
          if (PRINTF_MODE === "filtro") {
            const hit = fmtCache.get(key);
            if (hit === null) { RES.printfOmit++; bump(RES.printfTop, fmtCache.get(-key) || "?", 1, 32); return; }
            if (hit !== undefined) fmt = hit;
            else {
              try { fmt = f.readCString(); } catch (e) { return; }
              if (!fmt) return;
              const spam = PRINTF_SPAM.test(fmt);
              if (fmtLiteral(key) && fmtCache.size < 4096) {
                fmtCache.set(key, spam ? null : fmt);
                if (spam) fmtCache.set(-key, fmt);   // nombre para el resumen
              }
              if (spam) { RES.printfOmit++; bump(RES.printfTop, fmt, 1, 32); return; }
            }
          } else {
            try { fmt = f.readCString(); } catch (e) { return; }
            if (!fmt) return;
          }
          send({ t: "glog", fn: name, sjis: readSjis(f), args: fmtArgs(fmt, args, 1) });
        },
      });
    } catch (e) {
      send({ t: "hook_error", name, e: String(e) });
    }
  }
}

// ---- Otras formas de leer el socket
for (const name of ["WSARecv", "select", "WSAAsyncSelect", "WSAEventSelect", "ioctlsocket", "closesocket"]) {
  try {
    Interceptor.attach(fn(name), {
      onEnter(args) {
        this.a0 = args[0].toInt32(); this.a2 = args[2];
        if (name === "closesocket") { const s = args[0].toUInt32(); sockPort.delete(s); seqState.delete(s); }
      },
      onLeave(ret) { if (name !== "select" || ret.toInt32() > 0) send({ t: "ws", fn: name, sock: this.a0, ret: ret.toInt32() }); },
    });
  } catch (e) {}
}

// ---- Lector de mensajes del socket (FUN_00689ea0: 0 = mensaje completo, <0 = error) y manejador de la puerta (vtable[2])
Interceptor.attach(ptr(0x00689ea0), {
  onEnter(args) { this.m = args[0]; },
  onLeave(ret) {
    const r = ret.toInt32();
    if (r === -50) return;  // 0xffffffce = faltan datos, normal
    let id = null, st = null;
    try { id = this.m.readU32(); st = this.m.add(0x43c).readU16(); } catch (e) {}
    const hid = id === null ? null : "0x" + id.toString(16);
    // Sin YO2_HOOK_VERBOSE solo los errores (0xffffff32 = cabecera > 1024 B, etc.); los buenos van al resumen
    if (r === 0 && !VERBOSE) { RES.reader++; bump(RES.readerIds, String(hid), 1, 64); return; }
    send({ t: "reader", ret: r, id: hid, state: st });
  },
});
// FUN_0068bac0(conn, msg): sube conn[1] y exige msg.seq (+8) == conn[1]; si no, 0xffffff35 y el mensaje se tira.
// conn[0] es el SOCKET (FUN_0068ba20 lo cierra). Como el contador del cliente sube igual, un hueco desfasa TODOS
// los mensajes siguientes de esa conexión con la misma diferencia (got - want): por eso se avisa una vez por
// diferencia nueva («nuevo»: true, la que hay que corregir en Conn.SEQ[port]) y las repeticiones se callan
// (como mucho una línea cada 10 s con «callados»). Cuando vuelve a cuadrar se manda «seq_resuelto».
Interceptor.attach(ptr(0x0068bac0), {
  onEnter(args) { this.c = args[0]; this.m = args[1]; },
  onLeave(ret) {
    const r = ret.toInt32();
    if (r === -50) return;
    let want = null, got = null, sock = null;
    try { want = this.c.add(4).readU32(); got = this.m.add(8).readU32(); } catch (e) {}
    try { sock = this.c.readU32(); } catch (e) {}
    const port = portOf(sock);
    const ok = r === 0 && want !== null && want === got;
    const pk = port === null ? "sock" + sock : port;
    let s = RES.seq.get(pk);
    if (s === undefined) { s = { ok: 0, mal: 0 }; RES.seq.set(pk, s); }
    if (ok) s.ok++; else s.mal++;
    const st = seqState.get(sock);
    if (ok) {
      if (st !== undefined) {
        send({ t: "seq_resuelto", sock, port, want, tras: st.malos, callados: st.callados });
        seqState.delete(sock);
      }
      if (VERBOSE) send({ t: "seq", ret: r, want, got, sock, port });
      return;
    }
    const delta = want !== null && got !== null ? ((got - want) | 0) : null;
    const now = Date.now();
    const nuevo = st === undefined || st.delta !== delta;
    if (nuevo) {
      seqState.set(sock, { delta, desde: now, callados: 0, malos: 1 });
      send({ t: "seq", ret: r, want, got, sock, port, delta, nuevo: true });
      return;
    }
    st.malos++;
    if (VERBOSE || now - st.desde >= 10000) {
      send({ t: "seq", ret: r, want, got, sock, port, delta, nuevo: false, callados: st.callados, malos: st.malos });
      st.desde = now; st.callados = 0;
    } else {
      st.callados++;
    }
  },
});
Interceptor.attach(ptr(0x00402892), {
  onEnter(args) {
    let id = null;
    try { id = this.context.ecx.add(0xf0).readPointer().add(0xc).readPointer().readU32(); } catch (e) {}
    send({ t: "gate_handler", id: id === null ? null : "0x" + id.toString(16) });
  },
});

// ---- Vuelta al título tras elegir jugador (sesión 4): CGameMng (DAT_012bb6b0) crea el mundo (CWorldMng,
// DAT_012bc8d0) y lo desmonta (FUN_00a58510) cuando CWorldMng+0x94 se pone a 1. Se registra quién y cuándo.
function worldInfo(tag) {
  const w = ptr(0x12bc8d0).readPointer(); const r = { t: "world", tag, world: w.toString() };
  try { if (!w.isNull()) { r.b94 = w.add(0x94).readU8(); r.d80 = Array.from(new Uint8Array(w.add(0x80).readByteArray(0x20))).map(x => x.toString(16).padStart(2, "0")).join(" "); } } catch (e) {}
  return r;
}
for (const [name, a] of [["create_a", 0xadbef0], ["create_b", 0xadc0c0]]) {
  try { Interceptor.attach(ptr(a), { onEnter() { send(worldInfo(name + "_in")); }, onLeave() { send(worldInfo(name + "_out")); } }); }
  catch (e) { send({ t: "hook_error", name, e: String(e) }); }
}
try {
  Interceptor.attach(ptr(0xa58510), { onEnter() {
    const r = worldInfo("teardown"); r.bt = Thread.backtrace(this.context, Backtracer.ACCURATE).slice(0, 8).map(String); send(r);
  } });
} catch (e) { send({ t: "hook_error", name: "teardown", e: String(e) }); }

// ---- Avatares del mapa: FUN_00a024e0 los crea al entrar (el propio con tipo 0) a partir de la lista del bloque,
// solo los que tienen posición != (0,0); FUN_009d1eb0(id, float* pos, dir, tipo) crea cada uno (0 = no pudo).
try {
  Interceptor.attach(ptr(0xa024e0), { onEnter() { send({ t: "map_avatars" }); } });
  Interceptor.attach(ptr(0x9d1eb0), {
    onEnter(a) { this.r = { t: "mkav", id: a[0].toUInt32(), dir: a[2].toUInt32() & 0xff, kind: a[3].toUInt32() };
      try { this.r.x = a[1].readFloat(); this.r.y = a[1].add(4).readFloat(); } catch (e) {} },
    onLeave(ret) { this.r.ret = ret.toString(); send(this.r); },
  });
} catch (e) { send({ t: "hook_error", name: "avatars", e: String(e) }); }

// ---- Ventanas de error (CRT «abnormal program termination», asserts del juego): texto + pila para saber dónde rompe
for (const [mod, name, wide] of [["user32.dll", "MessageBoxA", false], ["user32.dll", "MessageBoxW", true]]) {
  try {
    Interceptor.attach(Module.load(mod).getExportByName(name), { onEnter(a) {
      let text = null; try { text = wide ? a[1].readUtf16String() : a[1].readAnsiString(); } catch (e) {}
      send({ t: "msgbox", text, bt: Thread.backtrace(this.context, Backtracer.FUZZY).slice(0, 16).map(String) });
    } });
  } catch (e) { send({ t: "hook_error", name, e: String(e) }); }
}

// ---- Excepciones (acceso inválido, etc.): dirección exacta antes de que la CRT lo convierta en «abnormal program
// termination». Devuelve false: el juego sigue su curso normal (se cierra igual), solo lo registramos.
Process.setExceptionHandler(d => {
  if (d.type === "single-step" || d.type === "breakpoint") return false;
  let mem = null; try { mem = d.memory ? { op: d.memory.operation, addr: d.memory.address.toString() } : null; } catch (e) {}
  const c = d.context;
  send({ t: "exception", type: d.type, pc: d.address.toString(), mem,
         regs: { eax: c.eax.toString(), ebx: c.ebx.toString(), ecx: c.ecx.toString(), edx: c.edx.toString(),
                 esi: c.esi.toString(), edi: c.edi.toString(), esp: c.esp.toString() },
         bt: Thread.backtrace(c, Backtracer.FUZZY).slice(0, 16).map(String) });
  return false;
});

// ---- AÑADIDO NUESTRO (no 1:1, tras flag, APAGADO por defecto; pedido por el owner el 07-10): aviso 5092 «No puedes
// ir de un mapa a otro cuando estás esperando un Duelo.» al pulsar MOVE con sala de duelo propia (el cliente lo borra
// en silencio). Encender: fichero yo2-aviso-sala.txt junto a yo2.exe o YO2_AVISO_SALA=1 (=0 apaga). Explicación
// completa en launcher/aviso_sala_hook.js y research/lobby/NOTES.md («Aviso al pulsar MOVE con sala abierta»).
// COPIA LITERAL de launcher/aviso_sala_hook.js (desde «(function (log)»; solo cambia la última línea: aquí el registro
// va por send() a run.py como «[cli] aviso_sala {...}»). Si se cambia uno, cambiar el otro.
(function (log) {
  // hueco de la barra -> id de texto. MAIN (hueco 0) NO tiene texto propio en el cliente: decide el owner (NOTES).
  const AVISOS = { 2: 5092 };
  const AT = ptr(0x00a00c21);
  const ESPERADO = [0x8b, 0xf0, 0x6a, 0x00, 0x8b, 0xce];   // mov esi,eax / push 0 / mov ecx,esi (exe 080912)
  const TD_CTASK = ptr(0x00c83238), TD_ELEMENT = ptr(0x00c83190);
  const TD_OK = ptr(0x00c87b08), TD_YESNO = ptr(0x00c878c8);
  const CB_VTABLE = ptr(0x00bee8a0), CB_METODO = ptr(0x0040fa88);   // retrollamada de botones de yo2LobbyEntry
  const WSTR_VACIA = ptr(0x00be610c);
  const P_RED = ptr(0x012711ac), P_UIMODE = ptr(0x012bc618), P_TEXTMNG = ptr(0x01271840);

  let k32 = null, getEnv = null, gfa = null;
  try {
    k32 = Module.load("kernel32.dll");
    getEnv = new NativeFunction(k32.getExportByName("GetEnvironmentVariableA"), "uint32", ["pointer", "pointer", "uint32"], "stdcall");
    gfa = new NativeFunction(k32.getExportByName("GetFileAttributesW"), "uint32", ["pointer"], "stdcall");
  } catch (e) { log({ ev: "error", donde: "kernel32", e: String(e) }); }
  let env = "";
  try {
    const b = Memory.alloc(64);
    const n = getEnv(Memory.allocAnsiString("YO2_AVISO_SALA"), b, 64);
    if (n > 0 && n < 64) env = b.readAnsiString(n).trim().toLowerCase();
  } catch (e) {}
  const ENV_SI = ["1", "si", "on", "true", "yes"].includes(env);
  const ENV_NO = ["0", "no", "off", "false"].includes(env);
  let fichero = null;
  try {
    const m = Process.mainModule || Process.enumerateModules()[0];
    fichero = m.path.replace(/[^\\\/]+$/, "") + "yo2-aviso-sala.txt";
  } catch (e) {}
  function activo() {
    if (ENV_NO) return false;
    if (ENV_SI) return true;
    if (fichero === null || gfa === null) return false;
    try { return gfa(Memory.allocUtf16String(fichero)) !== 0xffffffff; } catch (e) { return false; }
  }

  let dyn, crearOk, enlazar, modal, ponCb, borrarWstr;
  try {
    dyn = new NativeFunction(ptr(0x00b0b20e), "pointer", ["pointer", "int", "pointer", "pointer", "int"], "mscdecl");
    crearOk = new NativeFunction(ptr(0x005e79f0), "pointer", ["pointer", "uint32", "uint32", "uint32", "uint32"], "mscdecl");
    enlazar = new NativeFunction(ptr(0x00628fa0), "void", ["pointer", "pointer"], "thiscall");
    modal = new NativeFunction(ptr(0x005df6d0), "void", ["pointer", "int"], "thiscall");
    ponCb = new NativeFunction(ptr(0x005df480), "void", ["pointer", "pointer"], "thiscall");
    borrarWstr = new NativeFunction(ptr(0x00530120), "void", ["pointer", "int"], "thiscall");
  } catch (e) { log({ ev: "error", donde: "funciones", e: String(e) }); return; }

  // Lo mismo que hace el cliente para m_pDeckErr; devuelve null si la abre o el motivo por el que no.
  function abrirAviso(esc, texto) {
    if (!dyn(esc.add(0x154).readPointer(), 0, TD_CTASK, TD_OK, 0).isNull()) return "ya hay una ventana OK de la escena";
    if (!dyn(esc.add(0x1d8).readPointer(), 0, TD_CTASK, TD_YESNO, 0).isNull()) return "hay un Si/No abierto";
    const padre = dyn(esc.add(0x78).readPointer(), 0, TD_CTASK, TD_ELEMENT, 0);
    if (padre.isNull()) return "escena sin elemento raiz";
    esc.add(0xc4).writeS32(-1);
    const dlg = crearOk(padre, 0x43d20000, 0x438c0000, 0xfa2, 1);   // x 420.0, y 280.0, botón 4002 (como m_pDeckErr)
    if (dlg.isNull()) return "FUN_005e79f0 devolvio 0";
    const tp = esc.add(0x150);                                        // yo2TaskPtr m_pDeckErr
    tp.add(4).writePointer(dlg);
    enlazar(dlg, tp);
    modal(dlg, 1);
    const cb = Memory.alloc(12);                                       // la ventana guarda un clon (vtable[1])
    cb.writePointer(CB_VTABLE); cb.add(4).writePointer(esc); cb.add(8).writePointer(CB_METODO);
    ponCb(dlg, cb);
    const tm = P_TEXTMNG.readPointer();
    const tmp = Memory.alloc(0x20);                                    // std::wstring que construye el gestor de textos
    const leeTexto = new NativeFunction(tm.readPointer().add(4).readPointer(), "pointer", ["pointer", "pointer", "uint32"], "thiscall");
    const ws = leeTexto(tm, tmp, texto);
    let w = ws.add(4).readPointer();
    if (w.isNull()) w = WSTR_VACIA;
    const ponTexto = new NativeFunction(dlg.readPointer().add(0x30).readPointer(), "void", ["pointer", "pointer"], "thiscall");
    ponTexto(dlg, w);
    borrarWstr(tmp, 1);
    return null;
  }

  try {
    const cur = Array.from(new Uint8Array(AT.readByteArray(ESPERADO.length)));
    if (!cur.every((b, k) => b === ESPERADO[k])) {
      log({ ev: "error", donde: "bytes", e: "inesperados en " + AT + ": " + cur.map(b => b.toString(16)).join(" ") + "; no se engancha" });
      return;
    }
    Interceptor.attach(AT, {
      onEnter() {
        try {
          const menu = this.context.eax, esc = this.context.esi;
          if (menu.isNull() || esc.isNull()) return;
          let hueco = -1;
          for (const k of Object.keys(AVISOS)) {
            const h = Number(k);
            if (menu.add(0x3cc + h).readU8() !== 0 && !menu.add(0x3a4 + 4 * h).readPointer().isNull()) { hueco = h; break; }
          }
          if (hueco < 0) return;
          const red = P_RED.readPointer();
          if (red.isNull()) return;
          const t = red.add(0xc6c).readPointer();
          if (t.isNull() || t.add(0xd8b).readU8() === 0) return;        // bloqueo por otra cosa: no es lo nuestro
          if (!activo()) { log({ ev: "clic_bloqueado", hueco, aviso: "apagado" }); return; }
          const modo = P_UIMODE.readS32();
          if (modo !== 0) { log({ ev: "clic_bloqueado", hueco, aviso: "no", motivo: "modo de interfaz " + modo }); return; }
          const no = abrirAviso(esc, AVISOS[hueco]);
          log(no === null ? { ev: "aviso_abierto", hueco, texto: AVISOS[hueco] } : { ev: "clic_bloqueado", hueco, aviso: "no", motivo: no });
        } catch (e) { log({ ev: "error", donde: "onEnter", e: String(e) }); }
      },
    });
    log({ ev: "cargado", en: String(AT), activo_ahora: activo(), env: env || null, fichero });
  } catch (e) { log({ ev: "error", donde: "attach", e: String(e) }); }
})(function (o) { try { send(Object.assign({ t: "aviso_sala" }, o)); } catch (e) {} });

// ---- Descarga del script (B32). Frida llama a rpc.exports.dispose justo antes de descargarlo: script.unload()
// desde run.py o la sesión que se cierra porque run.py se fue. Se vuelven a poner los ret originales de
// 0x52c360/0x693170 (si no, la siguiente llamada saltaría al NativeCallback ya liberado y el cliente se caería).
// Los Interceptor.attach los deshace Frida solo; ojo: con ellos se va la redirección de DNS/URL a 127.0.0.1
// (las conexiones a lobby, salas y duelo ya usan 127.0.0.1 porque así lo anuncia la puerta).
// Después se puede volver a cargar net_hook.js en el mismo proceso (recarga del enganche sin relanzar el juego).
rpc.exports = {
  dispose() {
    try { clearInterval(resumenTimer); } catch (e) {}
    const n = restaurarLogs();
    flushResumen();
    try { send({ t: "hook_adios", restaurados: n }); } catch (e) {}
  },
};
