'use strict';
// Go de mentira para ver la página del lanzador en un navegador normal (sin Wails): window.go.main.App.<Método>() y
// window.runtime.EventsOn. Los métodos de gestión actúan sobre datos en memoria (expulsar quita al jugador, vetar lo pasa a
// «Bans»...), así se prueba el flujo entero. Se carga ANTES de app.js (servir.py lo inyecta en index.html).
//   ?tema=dark|light   ?srv=0  (servidor parado)   ?abierto=0  (registro abierto)   ?lento=1  (la API tarda)   ?vacio=1  (sin datos)
//   ?sinapi=1 (run.py sin la API de gestión)   ?nivel=N (nivel inicial del formulario)
//   ?dp=N (puntos Duelpass iniciales del formulario)   ?gratis=1 (duelos gratis)   ?lista=1 (lista con filas en cada estado:
//   vivo, comprobando, privado ahora y offline)
//   12-10, modo «Password»: ?modo=publico|clave|oculto (formulario) ?tiene=1 (ya hay contraseña guardada) ?srvclave=1 (servidor
//   en marcha con contraseña) ?pw=1 (en ?lista=1: abre el diálogo de la contraseña del servidor con candado) ?pwerr=1 (y con
//   «Wrong password») ?sel=<IDCorto> (fila elegida en ?lista=1) ?escribe=1 (escribe una contraseña en el campo)
//   12-10, «My servers»: ?mis=0 (ninguno guardado) ?form=nuevo|editar (abre el formulario; editar = el 2º, el de contraseña)
//   ?borrar=N (diálogo Delete del N-ésimo, 0 = el primero; ?nombre=1 escribe su nombre) ?cambiar=1 (Start en otro con uno en
//   marcha: la pregunta «Switch server»)
//   12-10, red (NET): ?acceso=publico|privado|ocupado|aleatorio («Your server» → Internet) ?fijo=1 (Fixed port encendido)
//   ?red=ajustes (abre Settings → Network) ?red=dir (abre «Join by address»; con ?dirmodo=codigo|clave|publico, ?escribe=1 y
//   ?direrr=1 = la dirección no contesta)
(() => {
  const Q = new URLSearchParams(location.search);
  const quiere = k => Q.get(k);
  const ya = Date.now();
  const msg = (k, p) => ({ k, p });
  const estricto = quiere('abierto') !== '1';
  const D = {
    jugadores: quiere('vacio') === '1' ? [] : [
      { corto: 'a1b2c3', nombre: 'Player1', dueno: true, estado: 'mapa', mundo: 'Domino', zona: 'Clock Square', desde_s: 5400, vetado: false, hueco: 0, nivel: 20, dp: 987, mp: 15200 },
      { corto: '77e9d0', nombre: 'Kaiba_fan', dueno: false, estado: 'duelo', mundo: 'Domino', zona: 'Game Shop', desde_s: 1260, vetado: false, hueco: 0, nivel: 5, dp: 3, mp: 640 },
      { corto: '3c5d10', nombre: 'Mai', dueno: false, estado: 'sala', mundo: null, zona: null, desde_s: 310, vetado: false, hueco: 0, nivel: 1, dp: 0, mp: 10000 },
      { corto: '9f01aa', nombre: 'Jaden <b>x</b>', dueno: false, estado: 'mapa', mundo: 'Academia', zona: 'Duel Academy', desde_s: 95, vetado: false, hueco: 1, nivel: 1, dp: 10, mp: 9500 },
      { corto: 'be4410', nombre: 'Atem', dueno: false, estado: 'menu', mundo: null, zona: null, desde_s: 12, vetado: false, hueco: -1, nivel: -1, dp: -1, mp: -1 }],
    vetados: quiere('vacio') === '1' ? [] : [
      { corto: 'd00d11', nombres: ['Troll99'], motivo: 'Spam in the chat', fecha: '2026-10-10 21:14' },
      { corto: 'c0ffee', nombres: [], motivo: '', fecha: '2026-10-09 08:02' }],
    cuentas: [
      { corto: 'a1b2c3', usuario: '', dueno: true, personajes: ['Player1'], fichas: [{ hueco: 0, nombre: 'Player1', nivel: 20, dp: 987, mp: 15200 }], en_linea: true, vetado: false },
      { corto: '77e9d0', usuario: 'kaiba', dueno: false, personajes: ['Kaiba_fan', 'Seto', 'Mokuba', 'Roland'], fichas: [{ hueco: 0, nombre: 'Kaiba_fan', nivel: 5, dp: 3, mp: 640 },
        { hueco: 1, nombre: 'Seto', nivel: 1, dp: 0, mp: 10000 }, { hueco: 2, nombre: 'Mokuba', nivel: 1, dp: 10, mp: 10000 }, { hueco: 3, nombre: 'Roland', nivel: -1, dp: -1, mp: -1 }], en_linea: true, vetado: false },
      { corto: '3c5d10', usuario: 'mai', dueno: false, personajes: ['Mai'], fichas: [{ hueco: 0, nombre: 'Mai', nivel: 1, dp: 0, mp: 10000 }], en_linea: true, vetado: false },
      { corto: 'd00d11', usuario: 'troll', dueno: false, personajes: ['Troll99'], fichas: [{ hueco: 0, nombre: 'Troll99', nivel: 3, dp: 41, mp: 200 }], en_linea: false, vetado: true },
      { corto: '5a5a5a', usuario: 'nuevo', dueno: false, personajes: [], fichas: [], en_linea: false, vetado: false }],
    copias: [
      { fichero: 'state.json.bak-admin-20261011-201500', kb: 412, fecha: '2026-10-11 20:15:00' },
      { fichero: 'state.json.bak-admin-20261010-230000', kb: 398, fecha: '2026-10-10 23:00:00' }],
  };
  if (quiere('vacio') === '1') D.cuentas = D.cuentas.slice(0, 1);
  const err = (k, p) => Promise.reject(new Error(JSON.stringify({ k, p })));
  const lat = v => new Promise(r => setTimeout(() => r(v), quiere('lento') === '1' ? 1200 : 40));
  const regs = [];
  const log = (...a) => { regs.push(a.map(String).join(' ')); window.__llamadas = regs; };
  const srvOn = quiere('srv') !== '0';
  const ajustes = { v: 2, actualizar: true, mostrar_ocultos: false, tema: quiere('tema') || 'light', ocultos: [], servidores: null,
    red: { puerto_fijo: quiere('fijo') === '1', puerto: 47300 } };
  const formDe = (nombre, desc, extra) => ({ nombre, descripcion: desc, max: 30, publica: true, reglas: 'vanilla', cuentas: estricto ? 'cerradas' : 'libre',
    nivel_inicial: parseInt(quiere('nivel') || '1', 10), dp_inicio: parseInt(quiere('dp') || '10', 10), duelos_gratis: quiere('gratis') === '1',
    modo: quiere('modo') || 'publico', tiene_clave: quiere('tiene') === '1', ...extra });
  // «My servers»: el de antes (en marcha salvo ?srv=0), uno con contraseña y reglas Enhanced, y uno oculto sin estrenar
  const mis = quiere('mis') === '0' ? [] : [
    { ID: 'a1a1a1a1a1a1a1a1', Legado: true, Form: formDe('Duel Arena', 'Friendly duels, vanilla rules'), CreadoHaceS: 86400 * 20, UsadoHaceS: 5400, BorraDatos: false, Huella: 'AB12-CD34' },
    { ID: 'b2b2b2b2b2b2b2b2', Legado: false, Form: formDe('Friends Only Club', 'Enhanced rules, invite only', { reglas: 'mejorado', modo: 'clave', tiene_clave: true, max: 16, nivel_inicial: 5 }),
      CreadoHaceS: 86400 * 6, UsadoHaceS: 2 * 86400, BorraDatos: true, Huella: 'QW7R-Z2K9' },
    { ID: 'c3c3c3c3c3c3c3c3', Legado: false, Form: formDe('Test World', '', { modo: 'oculto', max: 8, dp_inicio: 1000, duelos_gratis: true }),
      CreadoHaceS: 3600, UsadoHaceS: -1, BorraDatos: true, Huella: 'MN4P-8HTX' }];
  // «Your server» → Internet (sala.AccesoRed)
  const acceso = () => {
    const fijo = quiere('fijo') === '1', a = quiere('acceso') || 'publico';
    const base = { Puerto: fijo ? 47300 : 51874, PuertoUDP: fijo ? 47300 : 60212, Pedido: fijo ? 47300 : 0, Fijo: fijo, Ocupado: false,
      Alcance: 'Public', DirPublica: '', Confirmada: false, IPLocal: '192.0.2.43' };
    if (a === 'publico') return { ...base, DirPublica: '203.0.113.7:' + base.Puerto, Confirmada: true };
    if (a === 'ocupado') return { ...base, Puerto: 52311, PuertoUDP: 52311, Pedido: 47300, Fijo: false, Ocupado: true, Alcance: 'Private' };
    return { ...base, Alcance: a === 'privado' ? 'Private' : 'Unknown' };
  };
  const infoRed = () => ({ PuertoFijo: ajustes.red.puerto_fijo, Puerto: ajustes.red.puerto, PorDefecto: 47300, EnUso: srvOn ? acceso().Puerto : 0,
    Reiniciar: srvOn && quiere('reiniciar') === '1', VersionFirmada: 20261012110000, OrigenFirmada: 'paquete', NFirmada: 5, NUsuario: 1,
    MalasUsuario: quiere('malas') === '1' ? 2 : 0, NFabrica: 5, FicheroUsuario: 'C:\\Users\\you\\AppData\\Local\\YO2Online\\red-nodos.txt' });
  // ?lista=1: una fila en cada estado de la lista (12-10): vivo, comprobando, privado ahora y offline (visto hace 3 h y 2 días)
  const fila = (id, nombre, estado, extra) => ({ ID: '12D3KooW' + id, IDCorto: id.slice(0, 8), Huella: 'AB12-CD34', Nombre: nombre,
    Descripcion: 'Friendly duels', Idioma: 'en', Reglas: 'vanilla', Cuentas: 'libre', Jugadores: 0, Max: 30, EnDuelo: -1, PingMs: -1,
    PorRelay: false, Estado: estado, VistoHace: 0, Version: '080912', Compatible: true, Salas: [], Favorito: false, Propio: false,
    Lleno: false, Oculto: false, ...extra });
  const filasLista = quiere('lista') === '1' ? [
    fila('Vivo0001', 'Duel Arena EU', 'vivo', { Jugadores: 12, EnDuelo: 4, PingMs: 38 }),
    fila('Clave001', 'Friends Only Club', 'vivo', { Jugadores: 5, EnDuelo: 2, PingMs: 22, Contrasena: true, Reglas: 'mejorado' }),
    fila('Clave002', 'Team Pharaoh', 'vivo', { Jugadores: 3, Max: 16, EnDuelo: 0, PingMs: 61, Contrasena: true, ClaveGuard: true, Idioma: 'es' }),
    fila('Cache002', 'Kaiba Corp Tournament', 'cache', { VistoHace: 1800 }),
    fila('Priv0003', 'Ahmed’s server', 'privado', { VistoHace: 40, Favorito: true }),
    fila('Off00004', 'Old Domino City', 'sin-respuesta', { VistoHace: 3 * 3600 }),
    fila('Off00005', 'Academia Night', 'sin-respuesta', { VistoHace: 2 * 86400, Favorito: true })] : [];
  const estado = () => ({ JuegoOK: true, CarpetaJuego: 'C:\\Games\\YO2', VersionJuego: '080912', Actualizar: true, Version: '2026.10.11', RedP2P: 'conectada', Pares: 12,
    Buscando: false, Vistos: 2, Entrando: false, EntrandoA: '', Conexion: null, JugandoLocal: false, Parando: false, OpenServer: false,
    Servidor: srvOn && mis.length ? { EnMarcha: true, ID: mis[0].ID, Nombre: mis[0].Form.nombre, DesdeS: Math.floor((Date.now() - ya) / 1000) + 5400, Huella: 'AB12-CD34', Publica: true,
      ConClave: quiere('srvclave') === '1', JuegoOK: true,
      Juego: msg('msg.juego.escuchando'), P2POK: true, P2P: msg('msg.p2p.anunciado'), ListadoOK: true, Listado: msg('msg.listado.si'), TorOK: false,
      Tor: msg('msg.tor.desactivado'), Jugadores: D.jugadores.length, EnDuelo: 1, Max: 30, Invitados: 3, FuenteJug: 'panel', RunPyPropio: true, Aviso: {},
      Acceso: acceso() }
      : { EnMarcha: false, ID: '', Nombre: '', DesdeS: 0, Huella: '', Publica: true, JuegoOK: false, Juego: msg('msg.juego.parado'), P2POK: false, P2P: msg('msg.nada'), ListadoOK: false,
        Listado: msg('msg.nada'), TorOK: false, Tor: msg('msg.nada'), Jugadores: 0, EnDuelo: -1, Max: 0, Invitados: 0, FuenteJug: '', RunPyPropio: false, Aviso: {} } });
  const sinApi = () => (quiere('sinapi') === '1' ? err('msg.gestion.sin_token') : null);
  const quita = (arr, corto) => { const i = arr.findIndex(x => x.corto === corto); if (i < 0) throw new Error(JSON.stringify({ k: 'msg.gestion.no_existe' })); return arr.splice(i, 1)[0]; };
  const App = {
    Inicio: () => lat({ Version: '2026.10.11', Reanudando: false, Ajustes: ajustes, Estado: estado() }),
    Listo: () => lat(null), Estado: () => Promise.resolve(estado()), Ajustes: () => Promise.resolve(ajustes),
    PonerTema: t => { ajustes.tema = t; return lat(null); }, PonerVentana: v => { ajustes.ventana = v; return lat(null); }, ErrorJS: s => { console.error('[js]', s); (window.__erroresJS = window.__erroresJS || []).push(s); },
    Servidores: () => lat({ Filas: filasLista, Buscando: filasLista.length > 0, Vivos: filasLista.filter(f => f.Estado === 'vivo').length,
      EnCache: filasLista.filter(f => f.Estado === 'cache').length }), Buscar: () => lat(null),
    Minimizar() {}, Maximizar() {}, CerrarVentana() {},
    MisServidores: () => lat(mis.map((m, i) => ({ ...m, Max: m.Form.max, EnMarcha: srvOn && i === 0, Jugadores: srvOn && i === 0 ? D.jugadores.length : 0 }))),
    FormNuevo: () => lat(formDe('', '', { tiene_clave: false, cuentas: 'libre' })),
    CrearMiServidor: f => { log('CrearMiServidor', f.nombre, f.modo, (f.contrasena || '').length + ' caracteres'); mis.push({ ID: 'd4d4d4d4d4d4d4d4', Form: f, UsadoHaceS: -1, BorraDatos: true, Huella: 'ZZ11-YY22' }); return lat(null); },
    EditarMiServidor: (id, f) => { log('EditarMiServidor', id, f.nombre); const m = mis.find(x => x.ID === id); if (m && m.Form.reglas !== f.reglas) return err('msg.srv.reglas_fijas'); if (m) m.Form = f; return lat(null); },
    BorrarMiServidor: (id, n) => { log('BorrarMiServidor', id, n); const i = mis.findIndex(x => x.ID === id); if (i >= 0) mis.splice(i, 1); return lat(null); },
    ArrancarMiServidor: id => { log('ArrancarMiServidor', id); return lat(null); },
    Entrar: id => { log('Entrar', id); const f = filasLista.find(x => x.ID === id); return f && f.Contrasena && !f.ClaveGuard ? err('msg.err.pide_clave') : lat(null); },
    EntrarConClave: (id, c, r) => { log('EntrarConClave', id, c.length + ' caracteres', r); return c === 'secret' ? lat(null) : err('msg.err.clave_mala'); },
    OlvidarClave: id => { log('OlvidarClave', id); const f = filasLista.find(x => x.ID === id); if (f) f.ClaveGuard = false; return lat(null); },
    // ---- red (NET)
    InfoRed: () => lat(infoRed()),
    PonerPuertoFijo: (si, p) => { log('PonerPuertoFijo', si, p); if (p < 1024 || p > 65535) return err('msg.red.puerto_rango'); ajustes.red = { puerto_fijo: si, puerto: p }; return lat(null); },
    EntrarPorDireccion: (d, m, s) => { log('EntrarPorDireccion', d, m, (s || '').length + ' caracteres'); return quiere('direrr') === '1' ? err('msg.err.dir_formato', { ejemplo: '203.0.113.7:47300' }) : lat(null); },
    CopiarDireccion: () => lat(null),
    CopiarCodigo: () => lat(null), PararServidor: () => lat({ OK: true, Forzada: false, Partes: [msg('msg.parar.parado')], Segundos: 1 }), JugarAqui: () => lat(null),
    // ---- gestión
    GestionJugadores: async () => { log('GestionJugadores'); await lat(); const e = sinApi(); if (e) return e; return D.jugadores.map(j => ({ ...j, desde_s: j.desde_s + Math.floor((Date.now() - ya) / 1000) })); },
    GestionExpulsar: async c => { log('GestionExpulsar', c); await lat(); quita(D.jugadores, c); },
    GestionVetar: async (c, m) => { log('GestionVetar', c, m); await lat(); const j = quita(D.jugadores, c); D.vetados.unshift({ corto: c, nombres: [j.nombre], motivo: m, fecha: '2026-10-11 22:40' }); },
    GestionVetados: async () => { await lat(); return D.vetados.slice(); },
    GestionQuitarVeto: async c => { log('GestionQuitarVeto', c); await lat(); quita(D.vetados, c); },
    GestionCuentas: async () => { await lat(); return { estricto, max: 16, cuentas: D.cuentas.slice() }; },
    GestionCrearCuenta: async (u, c) => {
      log('GestionCrearCuenta', u, c.length + ' caracteres'); await lat();
      if (D.cuentas.some(a => a.usuario === u)) return err('msg.gestion.ya_existe');
      D.cuentas.push({ corto: Math.random().toString(16).slice(2, 8), usuario: u, dueno: false, personajes: [], en_linea: false, vetado: false });
    },
    GestionCambiarClave: async (c, p, u) => { log('GestionCambiarClave', c, p.length + ' caracteres', u); await lat(); },
    GestionBorrarCuenta: async c => { log('GestionBorrarCuenta', c); await lat(); quita(D.cuentas, c); },
    GestionNivel: async (c, h, n) => {
      log('GestionNivel', c, h, n); await lat();
      const j = D.jugadores.find(x => x.corto === c); let antes = 1;
      if (j && (h < 0 || h === j.hueco)) { antes = j.nivel; j.nivel = n; }
      for (const a of D.cuentas.filter(x => x.corto === c)) for (const f of a.fichas) if (f.hueco === (h < 0 && j ? j.hueco : h)) { antes = f.nivel; f.nivel = n; }
      return { antes, ahora: n, al_momento: !!j && j.estado !== 'duelo', en_linea: !!j };
    },
    GestionPuntos: async (c, h, dp, mp, modo) => {
      log('GestionPuntos', c, h, dp, mp, modo); await lat();
      const j = D.jugadores.find(x => x.corto === c), hh = h < 0 && j ? j.hueco : h;
      const pon = (o, k, v) => { if (v !== null && v !== undefined) o[k] = Math.max(0, Math.min(9999999, modo === 'sumar' ? Math.max(0, o[k]) + v : v)); };
      const todos = [];
      if (j && j.hueco === hh) todos.push(j);
      for (const a of D.cuentas.filter(x => x.corto === c)) for (const f of a.fichas) if (f.hueco === hh) todos.push(f);
      if (!todos.length) return err('msg.gestion.sin_personaje');
      const antes = { dp: todos[0].dp, mp: todos[0].mp };
      for (const o of todos) { pon(o, 'dp', dp); pon(o, 'mp', mp); }
      return { antes, ahora: { dp: todos[0].dp, mp: todos[0].mp }, al_momento: !!j && j.estado !== 'duelo', en_linea: !!j };
    },
    GestionAviso: async t => { log('GestionAviso', t); await lat(); return D.jugadores.length; },
    GestionCopia: async () => { log('GestionCopia'); await lat(); const f = 'state.json.bak-admin-20261011-224500'; D.copias.unshift({ fichero: f, kb: 415, fecha: '2026-10-11 22:45:00' }); return f; },
    GestionCopias: async () => { await lat(); return D.copias.slice(); },
  };
  window.go = { main: { App } };
  const oyentes = {};
  window.runtime = { EventsOn(n, f) { (oyentes[n] = oyentes[n] || []).push(f); } };
  // ?lista=1: la lista llega por el evento «lista» (como desde Go)
  if (filasLista.length) window.addEventListener('load', () => setTimeout(() => {
    for (const f of oyentes.lista || []) f({ Filas: filasLista, Buscando: false, Vivos: 1, EnCache: 1 });
    const sel = quiere('sel');
    const filas = [...document.querySelectorAll('#lista .fila')];
    const x = sel ? filas.find(f => f.textContent.includes(sel === 'Clave001' ? 'Friends Only' : sel === 'Clave002' ? 'Team Pharaoh' : sel))
      : document.querySelector('#lista .fila.sin');
    if (x) x.click();                                                                   // detalle de «Private now» (o ?sel=)
    if (quiere('pw') === '1') setTimeout(async () => {                                  // Join en el del candado: diálogo
      const f = [...document.querySelectorAll('#lista .fila')].find(r => r.textContent.includes('Friends Only'));
      f.querySelector('button.b').click();
      if (quiere('escribe') === '1' || quiere('pwerr') === '1') {
        document.querySelector('#pwClave').value = quiere('pwerr') === '1' ? 'secreto' : 'my secret';
        document.querySelector('#pwRecordar').click();
      }
      if (quiere('pwerr') === '1') { document.querySelector('#pwSi').click(); await new Promise(r => setTimeout(r, 200)); document.querySelector('#pwClave').value = ''; }
      if (quiere('ver') === '1') document.querySelector('#pwVer').click();
    }, 300);
  }, 900));
  window.__D = D;
  // ?red=ajustes | dir (NET): Settings → Network o el diálogo «Join by address»
  const red = quiere('red');
  if (red) window.addEventListener('load', async () => {
    const pausa = ms => new Promise(r => setTimeout(r, ms));
    await pausa(700);
    if (red === 'ajustes') {
      document.querySelector('nav a[data-v=ajustes]').click();
      await pausa(500);
      const s = document.querySelector('#v-ajustes .scroll'); s.scrollTop = s.scrollHeight;
      return;
    }
    document.querySelector('#bDireccion').click();
    await pausa(200);
    const m = quiere('dirmodo');
    if (m) document.querySelector('#dirModo [data-modo=' + m + ']').click();
    if (quiere('escribe') === '1' || quiere('direrr') === '1') {
      document.querySelector('#dirHost').value = quiere('direrr') === '1' ? '203.0.113.7:' : 'myhost.example.org:47300';
      document.querySelector('#dirSecreto').value = m === 'clave' ? 'my secret' : 'YO2-ABCDEF-GHIJKL-MNOPQR-STUVWX-YZ2345-67ABCD-EFGHIJ-KLMNOP-QRSTUV-WXYZ23-4567AB-CDEFGH';
    }
    if (quiere('direrr') === '1') document.querySelector('#dirEntrar').click();
  });
  // ?ir=<sección>[:<diálogo>] abre la pestaña Manage y esa sección (para capturar sin tocar nada): jugadores, vetos, cuentas, aviso,
  // copias, servidor; con :vetar :expulsar :quitar :clave :borrar abre además ese diálogo
  const ir = quiere('ir');
  if (ir) window.addEventListener('load', async () => {
    const pausa = ms => new Promise(r => setTimeout(r, ms));
    const [sec, dlg] = ir.split(':');
    await pausa(700);
    document.querySelector('nav a[data-v=crear]').click();
    await pausa(1300);
    if (sec === 'servidor') {
      const fm = quiere('form');
      if (fm) {
        if (fm === 'nuevo') document.querySelector('#bNuevo').click();
        else [...document.querySelectorAll('#misLista .fila')][1].querySelector('.gact .b.editar').click();
        await pausa(400);
        if (fm === 'nuevo' && quiere('modo')) { document.querySelector('#sVis [data-v=' + quiere('modo') + ']').click(); }
        if (fm === 'nuevo') { document.querySelector('#sNombre').value = 'Saturday League'; document.querySelector('#sDesc').value = 'Weekly tournament, vanilla rules'; }
      }
      const filasMis = [...document.querySelectorAll('#misLista .fila')];
      if (quiere('borrar') != null) {
        filasMis[parseInt(quiere('borrar'), 10)].querySelector('.b.borrar').click();
        await pausa(200);
        if (quiere('nombre') === '1') { const i = document.querySelector('#bNombre'); i.value = mis[parseInt(quiere('borrar'), 10)].Form.nombre; i.dispatchEvent(new Event('input')); }
      }
      if (quiere('cambiar') === '1') filasMis[2].querySelector('.b.oro').click();
      if (quiere('escribe') === '1') { document.querySelector('#sClave').value = 'my secret'; if (quiere('ver') === '1') document.querySelector('#sClaveVer').click(); }
      if (quiere('bajar') === '1') { const pc = document.querySelector('#form .pc'); pc.scrollTop = pc.scrollHeight; }
      // «Your server» no debe tener scroll (SRV + NET, 12-10): si lo tiene, borde rojo en la captura y la medida en <body data-vivo>.
      // Se mide otra vez en cada resize: Edge sin ventana carga con el viewport menos el marco y lo ajusta antes de la captura.
      await pausa(300);
      const v = document.querySelector('#vivo'), cols = v.closest('.cols'), pie = document.querySelector('#vivoPie');
      const desborda = x => x.scrollHeight > x.clientHeight + 1 || x.scrollWidth > x.clientWidth + 1;
      const medir = () => {
        const fuera = !pie.hidden && pie.getBoundingClientRect().bottom > innerHeight + 1;   // botones del pie fuera de la ventana
        const mal = desborda(v) || desborda(cols) || fuera;
        document.body.dataset.vivo = innerWidth + 'x' + innerHeight + ' vivo ' + v.scrollHeight + '/' + v.clientHeight + ' cols ' +
          cols.scrollHeight + '/' + cols.clientHeight + (fuera ? ' pie-fuera' : '') + (mal ? ' SCROLL' : ' ok');
        v.style.outline = mal ? '4px solid red' : '';
      };
      medir();
      addEventListener('resize', () => requestAnimationFrame(medir));
      return;
    }
    document.querySelector('#tGestion').click();
    await pausa(300);
    document.querySelector('#gNav [data-s=' + sec + ']').click();
    await pausa(600);
    if (sec === 'aviso') { const t = document.querySelector('#gaTexto'); t.value = 'Server restart in 10 minutes. Finish your duels!'; t.dispatchEvent(new Event('input')); }
    const filas = [...document.querySelectorAll('#g-' + (sec === 'jugadores' ? 'jugadores' : sec) + ' .fila.g')];
    const boton = (f, cls) => f && f.querySelector('.b' + cls);
    if (dlg === 'vetar') boton(filas[1], '.rojo').click();
    if (dlg === 'expulsar') boton(filas[1], '.claro').click();
    if (dlg === 'quitar') boton(filas[0], '.claro').click();
    if (dlg === 'clave') boton(filas[0], '.claro').click();
    if (dlg === 'borrar') boton(filas[4], '.rojo').click();
    if (dlg === 'nivel') boton(filas[sec === 'cuentas' ? 1 : 2], '.claro').click();   // jugadores: Mai (LV 1); cuentas: kaiba (4 personajes)
    if (dlg === 'dp' || dlg === 'dpsuma') {                                          // jugadores: Mai (0 DP); cuentas: kaiba (4 personajes)
      const f = filas[sec === 'cuentas' ? 1 : 2];
      [...f.querySelectorAll('.b.claro')].find(b => b.textContent === 'Duelpass').click();
      await pausa(200);
      if (dlg === 'dpsuma') { document.querySelector('#dModo [data-modo=sumar]').click(); document.querySelector('#dDP').value = '500'; }
    }
  });
})();
