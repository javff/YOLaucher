package nucleo

// Qué filas de la lista se enseñan y en qué estado. Es una función pura sobre lo que tiene n.filas, para probarla sin red.
// 11-10 (dueño): lo del caché, «sin confirmar» mientras se busca. 12-10 (dueño): nada se queda «Checking…» para siempre ni
// desaparece sin explicar:
//
//   - Lo de la última vez (EstadoCache) es «sin confirmar»: mientras no se le haya preguntado sale atenuado, sin ping y sin
//     poder entrar («Checking…»).
//   - En cuanto falla el primer intento (EstadoSinRespuesta), o al acabar la búsqueda sin que se le haya podido preguntar
//     (la búsqueda se cortó o no había red), sale «Offline · last seen hace X» (gris, sin entrar), sea o no favorito. Si
//     luego contesta, la fila pasa a EstadoVivo sola. Los del caché caducan a la semana (sala.edadCache; favoritos no).
//   - Si contesta pero ya no es público (EstadoPrivado: ya no atiende fichas, o rechazó la entrada con «ahora es privado»),
//     sale «Private now» (sin entrar: hace falta el código del anfitrión). No vuelve al caché (sala.BuscarServidores).
//   - Nuestro propio servidor (misma identidad de anfitrión que este lanzador) solo sale si está en marcha, es público y ha
//     contestado (o se está comprobando); si no, no sale nunca, ni desde el caché ni como favorito ni como «Offline».
//   - Nada que no haya contestado en ESTA sesión lleva ping: ni el del caché ni uno viejo.

import "yo2online/sala"

func vistaLista(svs []sala.ServidorVisto, buscando, propioPublico bool) []sala.ServidorVisto {
	r := make([]sala.ServidorVisto, 0, len(svs))
	for _, sv := range svs {
		if sv.Propio && !propioPublico {
			continue
		}
		if sv.Estado == sala.EstadoCache && !buscando {
			sv.Estado = sala.EstadoSinRespuesta // nadie le preguntó o no contestó: ya no está confirmado
		}
		if sv.Propio && (sv.Estado == sala.EstadoSinRespuesta || sv.Estado == sala.EstadoPrivado) {
			continue
		}
		if sv.Estado != sala.EstadoVivo {
			sv.PingMs, sv.PorRelay = -1, false
		}
		r = append(r, sv)
	}
	return r
}
