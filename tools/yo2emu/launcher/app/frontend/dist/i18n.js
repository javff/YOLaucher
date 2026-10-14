'use strict';
// Textos de la interfaz (solo inglés): la tabla i18n/en.json, t(clave, parámetros) y los ayudantes de formato. Se carga
// antes que app.js. Las claves que emite Go empiezan por «msg.» (sala.Msg: {k, p}); el resto son de la página. Lo que
// escribe un anfitrión (nombre, descripción, salas) NO pasa por aquí.
//
//   t('play.join')                         -> «Join»
//   t('bar.live', { n: 3 })                -> plural: busca bar.live_one / bar.live_other
//   tm({ k: 'msg.err.lleno', p: {...} })   -> traduce un mensaje de Go (los parámetros que son mensajes o listas de
//                                             mensajes se traducen solos)
const I18N = (() => {
  let tabla = {};
  const avisadas = new Set();
  let informar = () => {};

  async function cargar() {
    try {
      const r = await fetch('i18n/en.json', { cache: 'no-cache' });
      tabla = await r.json();
    } catch (x) {
      informar('i18n: no se pudo cargar en.json: ' + (x && x.message));
    }
  }

  function falta(clave) {
    if (avisadas.has(clave)) return;
    avisadas.add(clave);
    informar('i18n: falta la clave «' + clave + '»');
  }

  const reglas = new Intl.PluralRules('en');
  function plural(clave, n) {
    for (const k of [clave + '_' + reglas.select(n), clave + '_other', clave]) if (tabla[k] != null) return tabla[k];
    return null;
  }

  function valor(v) {
    if (v == null) return '';
    if (Array.isArray(v)) return v.map(valor).join('; ');
    if (typeof v === 'object' && typeof v.k === 'string') return tm(v);
    return String(v);
  }

  function t(clave, params) {
    const n = params && typeof params.n === 'number' ? params.n : null;
    let s = n != null ? plural(clave, n) : (tabla[clave] ?? null);
    if (s == null) { falta(clave); return clave; }
    if (params) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? valor(params[k]) : m));
    return s;
  }

  // Un mensaje de Go: {k, p}. Si lo que llega es un texto, se deja tal cual.
  function tm(m) {
    if (m == null) return '';
    if (typeof m === 'string') return m;
    if (typeof m.k === 'string' && m.k) return t(m.k, m.p || undefined);
    return '';
  }

  // El texto de un error devuelto por un método de Go: si es el JSON de un mensaje ({"k":...}) se traduce.
  function errInfo(e) {
    const s = String((e && e.message) || e || '');
    if (s.startsWith('{"k":')) {
      try { const m = JSON.parse(s); return { k: m.k, text: tm(m) }; } catch (_) { /* texto normal */ }
    }
    return { k: '', text: s || t('app.error_unknown') };
  }

  const fmtN = n => new Intl.NumberFormat('en').format(n);
  const fmtFecha = iso => {
    const d = new Date(iso + 'T12:00:00');
    if (isNaN(d)) return iso;
    return new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short', year: 'numeric' }).format(d);
  };
  // «5 min»: la duración suelta, y con «ago»
  function duracion(s) {
    if (s < 90) return t('time.s', { n: s });
    if (s < 5400) return t('time.min', { n: Math.round(s / 60) });
    if (s < 172800) return t('time.h', { n: Math.round(s / 3600) });
    return t('time.day', { n: Math.round(s / 86400) });
  }
  const hace = s => t('time.ago', { t: duracion(s) });

  return { cargar, t, tm, errInfo, fmtN, fmtFecha, duracion, hace, informarCon(f) { informar = f; } };
})();
const { t, tm } = I18N;
