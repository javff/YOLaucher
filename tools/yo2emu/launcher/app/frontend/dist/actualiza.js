'use strict';
// Aviso de la actualización del PAQUETE (UPD, 12-10; Go: app/nucleo/paquete_act.go). Va aparte de app.js a propósito
// (se trabajó en paralelo con otros cambios de la página): una tarjeta abajo a la derecha que dice «Downloading
// update…» o «Update ready: vX — restart to apply» con su botón. Textos en inglés aquí mismo (los jugadores solo ven
// inglés); si se pasan a i18n/en.json, las claves irían en «upd.*».
(() => {
  const TXT = {
    bajando: 'Downloading update {v}… {pct}',
    lista: 'Update ready: {v} — restart to apply',
    listaSrv: 'Update ready: {v} — stop your server, then restart to apply',
    boton: 'Restart now',
    conectado: 'You are connected to a server: restarting will disconnect you.',
    botonSeguro: 'Restart anyway',
    reiniciando: 'Restarting…',
    servidor: 'Stop your server first, then restart to apply the update.',
    error: 'Could not restart. Close and reopen the launcher to apply the update.',
  };
  const api = () => (window.go && window.go.main && window.go.main.App) || null;
  const rt = () => window.runtime || null;

  // AAAAMMDDhhmmss -> v2026.10.12.0955
  const ver = v => {
    const s = String(v || '');
    return s.length === 14 ? `v${s.slice(0, 4)}.${s.slice(4, 6)}.${s.slice(6, 8)}.${s.slice(8, 12)}` : (s ? 'v' + s : '');
  };
  const fmt = (s, p) => s.replace(/\{(\w+)\}/g, (m, k) => (k in p ? p[k] : m));

  const css = document.createElement('style');
  css.textContent = `
#upd{position:fixed;right:16px;bottom:62px;z-index:35;max-width:min(360px,calc(100% - 32px));display:flex;gap:10px;
  align-items:center;background:var(--caja-bg,#fff);color:var(--ink,#1a3566);border:1px solid var(--line2,#7fb9ec);
  border-left:5px solid var(--green,#21c55d);border-radius:9px;padding:9px 12px;font:13px var(--f,system-ui,sans-serif);
  box-shadow:0 8px 24px rgba(0,30,80,.3)}
#upd[hidden]{display:none}
#upd.bajando{border-left-color:var(--b2,#1f62e0)}
#upd span{flex:1;overflow-wrap:anywhere}
#upd button{font:600 12.5px var(--t,system-ui,sans-serif);color:#fff;background:var(--b2,#1f62e0);border:0;border-radius:7px;
  padding:6px 11px;cursor:pointer;white-space:nowrap}
#upd button:hover{background:var(--b3,#0f4fc8)}
#upd button:disabled{opacity:.6;cursor:default}`;
  document.head.appendChild(css);

  const caja = document.createElement('div');
  caja.id = 'upd';
  caja.hidden = true;
  caja.setAttribute('role', 'status');
  const texto = document.createElement('span');
  const boton = document.createElement('button');
  boton.type = 'button';
  boton.textContent = TXT.boton;
  caja.append(texto, boton);
  document.body.appendChild(caja);

  let ultimo = null;
  let srvEnMarcha = false;
  let conectado = false;
  let avisado = false; // conectado: el primer clic avisa, el segundo reinicia
  function pinta(e) {
    if (e) ultimo = e;
    e = ultimo;
    if (!e || !e.Actualizable || !e.Fase) { caja.hidden = true; return; }
    caja.hidden = false;
    caja.classList.toggle('bajando', e.Fase === 'descargando');
    if (e.Fase === 'descargando') {
      const pct = e.Total > 0 ? Math.floor(e.Hecho * 100 / e.Total) + '%' : '';
      texto.textContent = fmt(TXT.bajando, { v: ver(e.Version), pct });
      boton.hidden = true;
    } else {
      texto.textContent = avisado ? TXT.conectado : fmt(srvEnMarcha ? TXT.listaSrv : TXT.lista, { v: ver(e.Version) });
      boton.textContent = avisado ? TXT.botonSeguro : TXT.boton;
      boton.hidden = srvEnMarcha;
    }
  }

  boton.addEventListener('click', async () => {
    const a = api();
    if (!a) return;
    if (conectado && !avisado) { avisado = true; pinta(); return; }
    boton.disabled = true;
    texto.textContent = TXT.reiniciando;
    try {
      const r = await a.ReiniciarParaActualizar();
      if (r && r.OK) return;
      texto.textContent = r && r.Motivo === 'servidor' ? TXT.servidor : (r && r.Motivo === 'nada' ? '' : TXT.error);
      if (r && r.Motivo === 'nada') caja.hidden = true;
    } catch (x) {
      texto.textContent = TXT.error;
    }
    boton.disabled = false;
  });

  async function arrancar() {
    for (let i = 0; i < 200 && !(api() && rt()); i++) await new Promise(r => setTimeout(r, 50));
    if (!api() || !rt()) return;
    rt().EventsOn('paquete', pinta);
    try { pinta(await api().EstadoActualizacion()); } catch (x) { /* lanzador sin esta función */ }
    // con el servidor propio en marcha no se puede aplicar: el botón se esconde (se mira poco: es barato)
    setInterval(async () => {
      try {
        const e = await api().Estado();
        const s = !!(e && e.Servidor && e.Servidor.EnMarcha);
        const c = !!(e && (e.Conexion || e.JugandoLocal));
        if (s !== srvEnMarcha || c !== conectado) {
          srvEnMarcha = s; conectado = c;
          if (!c) avisado = false;
          pinta();
        }
      } catch (x) { /* nada */ }
    }, 3000);
  }
  arrancar();
})();
