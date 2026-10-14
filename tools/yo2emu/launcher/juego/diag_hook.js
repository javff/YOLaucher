// YO2-Online: modo DIAGNOSTICO del lanzador (JoinServer -diag, o yo2-diag.txt junto a JoinServer.exe o a yo2.exe).
// Se anade DETRAS de redirect_hook.js (mismo script). Todo sale por console.log -> frida-inject -> log del lanzador.
// Copia de lo util de ../net_hook.js para buscar por que el cliente del portatil SALE (sin WER) al recibir 0x6012:
//   - registro interno del juego (FUN_0052c360 «get msg...», avisos; FUN_00693170 red): en el exe publicado son
//     «ret + nops»; se cambia el ret por un salto a un callback y se restaura en dispose (B32 de net_hook.js)
//   - asertos del juego (FUN_0060cff0: «NULL access»/«Wild Task» de yo2TaskPtr.h, devuelve 0 -> exit(1)) con pila
//   - salidas: exit/doexit del CRT (0xb0ae52), _amsg_exit (0xb0dedd), ExitProcess, TerminateProcess, con pila
//   - excepciones (Process.setExceptionHandler; no las trata: el juego sigue su curso)
//   - CreateFileA/W: las que fallan y todas las de profiles/duellogs/list.txt (ruta, acceso, disposicion, error)
//   - MessageBoxA/W
//   - FUN_00694840 (0x6012 DUELLOG_FINISH) y FUN_006944b0 (0x6010) entrada/salida
// Las salidas no vuelven: tras escribir se duerme 1,5 s para que el mensaje llegue a frida-inject.
(function () {
  const out = (s) => { try { console.log("[diag] " + s); } catch (e) {} };
  const bt = (ctx) => { try { return Thread.backtrace(ctx, Backtracer.FUZZY).slice(0, 16).map(String).join(" "); } catch (e) { return "?"; } };
  const k32 = Module.load("kernel32.dll");
  const cstr = (p) => { try { return p.isNull() ? "(null)" : p.readCString(); } catch (e) { return "?"; } };
  const hexs = (p, n) => { try { return Array.from(new Uint8Array(p.readByteArray(n))).map(b => b.toString(16).padStart(2, "0")).join(" "); } catch (e) { return "?"; } };
  const espera = () => { try { Thread.sleep(1.5); } catch (e) {} };

  function fmtArgs(fmt, args, first) {
    let i = first;
    try {
      return fmt.replace(/%[-0-9.]*[lh]?([sdxuc])/g, (m, t) => {
        const a = args[i++];
        if (t === "s") return cstr(a);
        if (t === "c") return String.fromCharCode(a.toInt32() & 0xff);
        if (t === "x") return (a.toInt32() >>> 0).toString(16);
        return String(a.toInt32());
      });
    } catch (e) { return fmt; }
  }

  // ---- registro interno (ret -> salto a callback); ruido por fotograma fuera
  const SPAM = /^(?:h1|h2|ey|mo|bo|ac|a1|cw|dc)_|^%d\[%s\]|^m_pMotEngine|^\[%d\]disa|^DUEL_VIEW/;
  const ORIG = [0xc3, 0x90, 0x90, 0x90, 0x90];
  const cbs = [], parcheados = [];
  // Solo el de red: el registro general (0x52c360) lo llaman al nacer los hilos del cargador de texturas, que tienen
  // una pila minima; el callback de Frida no cabe y revienta (access-violation read@0xffe7, visto en el portatil).
  // Ninguno: el de red (0x693170) tambien reventaba en el portatil (access-violation dentro de Frida al llamar al
  // callback desde el hilo principal, al empezar el duelo). Se deja el bucle por si se quiere probar en otro equipo.
  for (const [name, addr] of []) {
    try {
      const p = ptr(addr);
      const cur = Array.from(new Uint8Array(p.readByteArray(5)));
      if (!cur.every((b, k) => b === ORIG[k])) { out(name + ": bytes inesperados " + cur.map(b => b.toString(16)).join(" ") + ", no se parchea"); continue; }
      const cb = new NativeCallback(function (f, a1, a2, a3, a4, a5) {
        if (f.isNull() || f.compare(ptr(0x10000)) < 0) return;
        let fmt; try { fmt = f.readCString(); } catch (e) { return; }
        if (!fmt || SPAM.test(fmt)) return;
        out(name + " " + fmtArgs(fmt, [f, a1, a2, a3, a4, a5], 1));
      }, "void", ["pointer", "pointer", "pointer", "pointer", "pointer", "pointer"], "mscdecl");
      cbs.push(cb);
      Memory.patchCode(p, 5, code => { const w = new X86Writer(code, { pc: p }); w.putJmpAddress(cb); w.flush(); });
      parcheados.push(p);
    } catch (e) { out(name + ": " + e); }
  }

  // ---- asertos y salidas del exe
  const engancha = (addr, nombre, onEnter) => { try { Interceptor.attach(ptr(addr), { onEnter }); } catch (e) { out(nombre + ": " + e); } };
  // FUN_0060cff0(flag, buf, fichero, linea): dialogo de aserto; si devuelve 0 el juego llama a exit(1)
  engancha(0x0060cff0, "aserto", function (a) {
    out("ASERTO fichero=" + cstr(a[2]) + " linea=" + a[3].toInt32() + " pila " + bt(this.context));
  });
  engancha(0x00b0ae52, "doexit", function (a) { out("EXIT codigo=" + a[0].toInt32() + " pila " + bt(this.context)); espera(); });
  engancha(0x00b0dedd, "amsg_exit", function (a) { out("AMSG_EXIT (error del CRT) R6" + a[0].toInt32() + " pila " + bt(this.context)); espera(); });
  for (const n of ["ExitProcess", "TerminateProcess"]) {
    try {
      Interceptor.attach(k32.getExportByName(n), { onEnter(a) {
        out(n + " codigo=" + (n === "ExitProcess" ? a[0] : a[1]).toInt32() + " pila " + bt(this.context)); espera();
      } });
    } catch (e) { out(n + ": " + e); }
  }
  Process.setExceptionHandler(d => {
    if (d.type === "single-step" || d.type === "breakpoint") return false;
    let mem = ""; try { mem = d.memory ? " mem=" + d.memory.operation + "@" + d.memory.address : ""; } catch (e) {}
    const c = d.context;
    out("EXCEPCION " + d.type + " pc=" + d.address + mem + " eax=" + c.eax + " ecx=" + c.ecx + " edx=" + c.edx +
        " esi=" + c.esi + " edi=" + c.edi + " pila " + bt(c));
    espera();
    return false;
  });

  // ---- ficheros
  const INTERES = /profiles|duellog|list\.txt|\.log$/i;
  for (const [n, wide] of [["CreateFileA", false], ["CreateFileW", true]]) {
    try {
      Interceptor.attach(k32.getExportByName(n), {
        onEnter(a) {
          try { this.ruta = a[0].isNull() ? "(null)" : (wide ? a[0].readUtf16String() : a[0].readAnsiString()); } catch (e) { this.ruta = "?"; }
          this.acc = a[1].toUInt32(); this.disp = a[4].toUInt32();
        },
        onLeave(r) {
          const fallo = r.toInt32() === -1;
          if (fallo || INTERES.test(this.ruta || "")) {
            out(n + " " + JSON.stringify(this.ruta) + " acceso=" + this.acc.toString(16) + " disp=" + this.disp +
                (fallo ? " FALLA error=" + this.lastError : " ok"));
          }
        },
      });
    } catch (e) { out(n + ": " + e); }
  }
  for (const n of ["CreateDirectoryA", "CreateDirectoryW"]) {
    try {
      Interceptor.attach(k32.getExportByName(n), {
        onEnter(a) { try { this.ruta = n.endsWith("W") ? a[0].readUtf16String() : a[0].readAnsiString(); } catch (e) { this.ruta = "?"; } },
        onLeave(r) { out(n + " " + JSON.stringify(this.ruta) + " -> " + r.toInt32() + (r.toInt32() ? "" : " error=" + this.lastError)); },
      });
    } catch (e) {}
  }

  // ---- ventanas de error
  for (const [n, wide] of [["MessageBoxA", false], ["MessageBoxW", true]]) {
    try {
      Interceptor.attach(Module.load("user32.dll").getExportByName(n), { onEnter(a) {
        let t = "?"; try { t = wide ? a[1].readUtf16String() : a[1].readAnsiString(); } catch (e) {}
        out(n + " " + JSON.stringify(t) + " pila " + bt(this.context));
      } });
    } catch (e) {}
  }

  // ---- registro del duelo (0x6010 / 0x6012)
  engancha(0x006944b0, "duellog_header", function () { out("0x6010 DUELLOG_HEADER (FUN_006944b0) entra, nd=" + this.context.ecx); });
  try {
    Interceptor.attach(ptr(0x00694840), {
      onEnter() { out("0x6012 DUELLOG_FINISH (FUN_00694840) entra ecx=" + this.context.ecx +
                      " 678=" + hexs(this.context.ecx.add(0x678), 1) + " bcec=" + hexs(this.context.ecx.add(0xbcec), 4) +
                      " bcf4=" + hexs(this.context.ecx.add(0xbcf4), 4)); },
      onLeave() { out("0x6012 DUELLOG_FINISH sale bien"); },
    });
  } catch (e) { out("duellog_finish: " + e); }

  // dispose: deja los ret originales (si se descarga el script con el juego vivo, el callback ya no existe)
  rpc.exports = Object.assign(rpc.exports || {}, {
    dispose() {
      while (parcheados.length) {
        const p = parcheados.pop();
        try { Memory.patchCode(p, 5, code => { code.writeByteArray(ORIG); }); } catch (e) {}
      }
    },
  });
  out("diag_hook cargado (registro interno, asertos, salidas, excepciones, ficheros)");
})();
