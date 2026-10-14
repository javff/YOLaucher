'use strict';
// Red para que el proyecto dure años (NET, 12-10; launcher/NOTES-red.md §13). Se carga después de app.js y usa sus
// ayudantes ($, el, t, tm, call, capa, toast, icon, ponTog...):
//   - «Join by address»: IP o nombre del anfitrión + código de sala, contraseña o nada (EntrarPorDireccion);
//   - Ajustes → Network: «Fixed port» (PonerPuertoFijo) y de dónde salen los nodos de arranque (InfoRed);
//   - «Your server»: chip «Internet» y, si se llega desde Internet, la dirección para compartir (CopiarDireccion).
// La dirección no es secreta; el código de sala sí y nunca se enseña ni se copia desde aquí.

const NET = { modo: 'codigo', info: null };

// ------------------------------------------------------------------ Join by address
function ultimaDir() { try { return localStorage.getItem('yo2.dir_ultima') || ''; } catch (_) { return ''; } }
function guardaDir(d) { try { localStorage.setItem('yo2.dir_ultima', d); } catch (_) {} }

function ponModoDir(m) {
  NET.modo = m;
  $$('#dirModo [role=radio]').forEach(b => { b.setAttribute('aria-checked', String(b.dataset.modo === m)); b.classList.toggle('on', b.dataset.modo === m); });
  $('#dirSecretoBox').hidden = m === 'publico';
  const clave = m === 'clave';
  $('#dirSecretoLbl').textContent = t(clave ? 'net.secret_pw' : 'net.secret_code');
  $('#dirSecreto').placeholder = clave ? '' : 'YO2-XXXXXX-XXXXXX-…';
  $('#dirSecreto').maxLength = clave ? 64 : 120;
  $('#dirSecreto').style.letterSpacing = clave ? '' : '.08em';
  $('#dirVer').hidden = !clave;
  verClave('#dirVer', false);
  if (!clave) $('#dirSecreto').type = 'text';
}
function errDir(texto) { const e = $('#dirErr'); e.textContent = texto || ''; e.hidden = !texto; }
function abreDir(error, modo, dir) {
  ponModoDir(modo || NET.modo);
  $('#dirHost').value = dir || $('#dirHost').value || ultimaDir();
  $('#dirSecreto').value = '';
  errDir(error);
  capa('capaDir', true);
  ($('#dirHost').value ? (NET.modo === 'publico' ? $('#dirEntrar') : $('#dirSecreto')) : $('#dirHost')).focus();
}
function cierraDir() { $('#dirSecreto').value = ''; capa('capaDir', false); }
async function entrarDir() {
  const dir = $('#dirHost').value.trim(), sec = $('#dirSecreto').value;
  if (!dir) { errDir(t('net.dir_empty')); $('#dirHost').focus(); return; }
  if (NET.modo === 'codigo' && !sec.trim()) { errDir(t('code.paste')); $('#dirSecreto').focus(); return; }
  if (NET.modo === 'clave' && !sec) { errDir(t('pw.empty')); $('#dirSecreto').focus(); return; }
  try {
    await call('EntrarPorDireccion', dir, NET.modo, NET.modo === 'publico' ? '' : sec);
    guardaDir(dir);
    cierraDir();
  } catch (e) {
    errDir(msg(e));
    if (errK(e) === 'msg.err.sin_juego') { cierraDir(); mostrar('ajustes'); }
  }
}
// la contraseña falta o no vale (fase «error» con PideClave y Direccion; app.js): el diálogo otra vez, en modo contraseña
function dirPideClave(e) { abreDir(tm(e.Error), 'clave', e.Direccion); }

$('#bDireccion').onclick = () => abreDir();
$('#dirCancelar').onclick = cierraDir;
$('#dirEntrar').onclick = entrarDir;
$$('#dirModo [role=radio]').forEach(b => b.addEventListener('click', () => { ponModoDir(b.dataset.modo); errDir(''); }));
['#dirHost', '#dirSecreto'].forEach(s => $(s).addEventListener('keydown', e => {
  if (e.key === 'Enter') entrarDir();
  if (e.key === 'Escape') cierraDir();
}));

// ------------------------------------------------------------------ Ajustes → Network
function fechaVersion(v) { const s = String(v || ''); return s.length === 14 ? s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8) : s; }
async function pintaRed() {
  let i;
  try { i = await call('InfoRed'); } catch (_) { return; }
  NET.info = i;
  ponTog('#aPuertoFijo', i.PuertoFijo);
  if (document.activeElement !== $('#aPuerto')) $('#aPuerto').value = String(i.Puerto || i.PorDefecto);
  $('#aPuerto').disabled = !i.PuertoFijo;
  const est = $('#aPuertoEst');
  est.className = 'est-aj';
  let txt = i.PuertoFijo ? t('net.port_on_hint', { port: i.Puerto }) : t('net.port_off_hint');
  if (i.Reiniciar) { txt += ' ' + t('net.port_restart', { port: i.EnUso }); est.className = 'est-aj aviso'; }
  est.textContent = txt;
  const origen = { paquete: t('net.from_paquete'), guardada: t('net.from_guardada') }[i.OrigenFirmada] || '';
  $('#aNodos').textContent = [
    i.VersionFirmada ? t('net.nodes_signed', { v: fechaVersion(i.VersionFirmada), from: origen, n: i.NFirmada }) : t('net.nodes_signed_none'),
    i.MalasUsuario ? t('net.nodes_user_bad', { n: i.NUsuario, bad: i.MalasUsuario }) : t('net.nodes_user', { n: i.NUsuario }),
    t('net.nodes_factory', { n: i.NFabrica })].join(' · ');
  $('#aNodosAyuda').textContent = t('net.nodes_hint', { file: i.FicheroUsuario });
}
async function guardaPuerto(fijo) {
  const p = parseInt($('#aPuerto').value, 10) || 0;
  try {
    await call('PonerPuertoFijo', fijo, p);
    toast(t('net.port_saved'), 'ok');
  } catch (e) {
    const est = $('#aPuertoEst');
    est.className = 'est-aj aviso';
    est.textContent = msg(e);
    return;
  }
  pintaRed();
}
$('#aPuertoFijo').onclick = () => guardaPuerto($('#aPuertoFijo').getAttribute('aria-checked') !== 'true');
$('#aPuerto').addEventListener('change', () => guardaPuerto(true));
$('#aPuerto').addEventListener('keydown', e => { if (e.key === 'Enter') e.currentTarget.blur(); });
conmutador('#aPuertoFijo');

// ------------------------------------------------------------------ «Your server»: Internet
// «Your server» es compacto y sin scroll (SRV, 12-10): Internet es un 5.º chip a lo ancho (texto corto; el detalle y cómo abrir
// el puerto en el router van en el title) y la dirección pública, una sola fila con Copy y solo si se llega desde Internet.
// a = sala.AccesoRed; null = servidor parado.
function chipInternet(a) {
  if (!a) { const c = chip(false, t('net.reach_title'), '—', t('net.reach_title')); c.classList.add('ancho'); return c; }
  const pub = a.Alcance === 'Public';
  // puerto fijo ocupado: eso es lo importante (el alcance queda en el title); si no, alcance · puerto
  const corto = a.Ocupado ? [t('net.ck_busy', { wanted: a.Pedido, port: a.Puerto })]
    : [t(pub ? 'net.ck_reachable' : a.Alcance === 'Private' ? 'net.ck_router' : 'net.ck_checking')];
  if (a.Puerto && !a.Ocupado) corto.push(a.Fijo ? t('net.ck_port', { port: a.Puerto }) : t('net.ck_random', { port: a.Puerto }));
  const det = [t('net.reach_title') + ': ' + t(pub ? 'net.reach_public' : a.Alcance === 'Private' ? 'net.reach_private' : 'net.reach_unknown')];
  if (a.Ocupado) det.push(t('net.port_busy', { wanted: a.Pedido, port: a.Puerto }));
  if (a.Puerto && !tieneDir(a)) {
    if (a.Fijo) det.push(a.IPLocal ? t('net.open_port', { port: a.Puerto, ip: a.IPLocal }) : t('net.open_port_noip', { port: a.Puerto }));
    else if (!a.Ocupado) det.push(t('net.random_port', { port: a.Puerto }));
  }
  const c = chip(pub ? true : 'warn', t('net.reach_title'), corto.join(' · '), det.join('\n'));
  c.classList.add('ancho');
  return c;
}
const tieneDir = a => !!(a && a.Puerto && a.DirPublica && (a.Alcance === 'Public' || a.Confirmada));
// la dirección para «Join by address» (no es secreta): una fila como la del código de sala
function filaDireccion(a) {
  if (!tieneDir(a)) return null;
  const ayuda = t('net.addr_label') + (a.Fijo ? '' : '\n' + t('net.addr_random'));
  return el('div', { class: 'cod dir', title: ayuda },
    el('span', { class: 'cl', text: t('net.addr_short') }), el('b', { text: a.DirPublica, title: a.DirPublica }), el('span', { class: 'sp' }),
    el('button', { class: 'b claro mini', title: t('net.addr_copy_tip'), 'aria-label': t('net.copy_addr'),
      onclick: () => call('CopiarDireccion').then(() => toast(t('net.addr_copied'), 'ok')).catch(x => toast(msg(x), 'mal')) },
      icon('i-copy')));   // solo el icono: así la dirección entera cabe en la fila también a 900 px
}
