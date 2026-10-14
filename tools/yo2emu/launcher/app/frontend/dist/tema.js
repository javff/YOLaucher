'use strict';
// Aspecto de la ventana (por defecto «light», decisión del owner 11-10): «system» (el de Windows, prefers-color-scheme), «light» u «oscuro». Se carga en el <head>, antes de
// pintar, para que no parpadee: lo ultimo elegido se guarda tambien en localStorage (copia rapida); la fuente de verdad es
// el ajuste «tema» de lanzador.json y app.js lo vuelve a aplicar al arrancar.
const TEMA = (() => {
  const CLAVE = 'yo2tema';
  const VALIDOS = ['system', 'light', 'dark'];
  const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  let actual = 'light';
  const norm = v => (VALIDOS.includes(v) ? v : 'light');
  function pinta() {
    const oscuro = actual === 'dark' || (actual === 'system' && !!(mq && mq.matches));
    document.documentElement.classList.toggle('dark', oscuro);
  }
  function aplicar(v, guardar) {
    actual = norm(v);
    pinta();
    if (guardar !== false) { try { localStorage.setItem(CLAVE, actual); } catch (_) {} }
  }
  if (mq) { const f = () => { if (actual === 'system') pinta(); }; if (mq.addEventListener) mq.addEventListener('change', f); else if (mq.addListener) mq.addListener(f); }
  try { actual = norm(localStorage.getItem(CLAVE)); } catch (_) {}
  pinta();
  return { aplicar, valor: () => actual };
})();
