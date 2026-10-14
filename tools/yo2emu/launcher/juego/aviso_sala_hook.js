// YO2-Online: AÑADIDO NUESTRO (no 1:1; pedido por el owner el 07-10 «para ver qué tal queda»; APAGADO por defecto).
// Con sala de duelo propia (esperando duelo / Autoduelo) el cliente borra EN SILENCIO el clic de MOVE de la barra del
// mapa (yo2AreaMapScene, FUN_009ff900: 0xa0071a mira la marca +0xd8b y salta a 0xa00bb5, que acaba en 0xa00c16-0xa00c35
// borrando los clics de MAIN (hueco 0) y MOVE (hueco 2) con FUN_00a7fb00). El texto 5092 «No puedes ir de un mapa a
// otro cuando estás esperando un Duelo.» existe en yo2text pero ningún código de esta versión lo usa. Esto lo enseña
// en una ventana OK del propio cliente cuando el clic borrado era MOVE. Detalle y C/P/D: research/lobby/NOTES.md,
// «Aviso al pulsar MOVE con sala abierta».
//
// Cómo se hace (todo con funciones del cliente, en el hilo principal, sin NativeCallback):
//   - Interceptor en 0xa00c21 (mov esi,eax / push 0 / mov ecx,esi, justo antes de borrar los clics): eax = barra de
//     menús (CWindowModelMenu), esi = escena del mapa. El clic de cada hueco es el byte barra+0x3cc+hueco
//     (FUN_00a7f9d0 lo lee, FUN_00a7fb00 lo pone a 0); aquí aún no está borrado.
//   - Solo si la marca de sala propia ([[0x12711ac]+0xc6c]+0xd8b) está puesta: 0xa00bb5 también se alcanza por otros
//     estados de la escena (FUN_00863660/6e0/720) y esos no son lo nuestro.
//   - La ventana es la misma que usa el cliente para el aviso «mazo no válido» al pulsar DUEL (m_pDeckErr,
//     yo2LobbyEntry FUN_00863ec0, 0x864d98-0x86547a): CWindowModelDialogOk creada con FUN_005e79f0(padre, 420.0, 280.0,
//     botón 0xfa2, 1), guardada en el yo2TaskPtr de la escena +0x150/+0x154 (FUN_00628fa0), FUN_005df6d0(1), retrollamada
//     {vtable 0xbee8a0, escena, 0x40fa88} con FUN_005df480 y el texto con TextMng(0x1271840)->vtable[1] + ventana->vtable[0x30].
//     Al pulsar OK la retrollamada del cliente (FUN_008633c0) pone escena+0xc4 = 1 y el propio cliente la cierra en el
//     siguiente fotograma (FUN_00863ec0, 0x865dc8-0x865f2c). Nada que limpiar de nuestro lado.
//
// Encender (se mira en cada clic: no hace falta relanzar el juego para el fichero):
//   - fichero «yo2-aviso-sala.txt» (vacío vale) junto a yo2.exe, o
//   - variable de entorno YO2_AVISO_SALA=1 del juego (run.py la hereda de su entorno). YO2_AVISO_SALA=0 lo apaga
//     aunque exista el fichero.
//
// COPIA LITERAL en ../net_hook.js (cliente del PC, run.py / run_client.py): si se cambia este fichero, cambiar los dos
// (solo difiere la última línea: allí el registro va por send() a run.py; aquí por console.log al log del lanzador).
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
})(function (o) { try { console.log("[aviso_sala] " + JSON.stringify(o)); } catch (e) {} });
