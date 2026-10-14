package nucleo

// Pruebas de qué filas se enseñan y en qué estado (lista_estado.go). Sin red: solo la función pura.

import (
	"fmt"
	"testing"

	"yo2online/sala"
)

func sv(id, estado string, ping int, fav, propio bool) sala.ServidorVisto {
	return sala.ServidorVisto{ID: id, Estado: estado, PingMs: ping, PorRelay: ping > 0, Favorito: fav, Propio: propio}
}

func porID(r []sala.ServidorVisto) map[string]sala.ServidorVisto {
	m := map[string]sala.ServidorVisto{}
	for _, s := range r {
		m[s.ID] = s
	}
	return m
}

// Mientras dura la búsqueda, lo de la última vez se ve como «sin confirmar»: estado cache y sin ping.
func TestListaMientrasBuscaLoDelCacheNoLlevaPing(t *testing.T) {
	r := porID(vistaLista([]sala.ServidorVisto{
		sv("cache", sala.EstadoCache, 37, false, false),
		sv("vivo", sala.EstadoVivo, 52, false, false),
	}, true, false))
	if c := r["cache"]; c.Estado != sala.EstadoCache || c.PingMs != -1 || c.PorRelay {
		t.Fatalf("cache: %+v", c)
	}
	if v := r["vivo"]; v.Estado != sala.EstadoVivo || v.PingMs != 52 {
		t.Fatalf("el vivo debe conservar su ping: %+v", v)
	}
}

// 12-10: en cuanto falla el primer intento sale «Offline» (con su «visto hace»), sea o no favorito; nada se queda
// «Checking…» esperando al final de la pasada.
func TestListaMientrasBuscaElQueFallaSaleOfflineYa(t *testing.T) {
	r := porID(vistaLista([]sala.ServidorVisto{
		sv("normal", sala.EstadoSinRespuesta, 9, false, false),
		sv("fav", sala.EstadoSinRespuesta, 9, true, false),
	}, true, false))
	for _, id := range []string{"normal", "fav"} {
		if n := r[id]; n.Estado != sala.EstadoSinRespuesta || n.PingMs != -1 {
			t.Fatalf("%s: %+v", id, n)
		}
	}
}

// Al acabar la pasada: los que no contestaron (o a los que no se llegó a preguntar) quedan «Offline», favoritos o no;
// los vivos, igual.
func TestListaAlAcabarLosQueNoContestanQuedanOffline(t *testing.T) {
	r := vistaLista([]sala.ServidorVisto{
		sv("muerto", sala.EstadoSinRespuesta, 5, false, false),
		sv("sin-preguntar", sala.EstadoCache, 5, false, false), // la búsqueda se cortó (o no había red) antes de preguntarle
		sv("fav-muerto", sala.EstadoSinRespuesta, 5, true, false),
		sv("fav-sin-preguntar", sala.EstadoCache, 5, true, false),
		sv("vivo", sala.EstadoVivo, 20, false, false),
		sv("fav-vivo", sala.EstadoVivo, 30, true, false),
	}, false, false)
	m := porID(r)
	if len(r) != 6 {
		t.Fatalf("debían quedar los 6: %+v", r)
	}
	for _, id := range []string{"muerto", "sin-preguntar", "fav-muerto", "fav-sin-preguntar"} {
		if s := m[id]; s.Estado != sala.EstadoSinRespuesta || s.PingMs != -1 {
			t.Errorf("%s debía quedar como Offline sin ping: %+v", id, s)
		}
	}
	if m["vivo"].PingMs != 20 || m["fav-vivo"].PingMs != 30 || m["fav-vivo"].Estado != sala.EstadoVivo {
		t.Errorf("los vivos no se tocan: %+v %+v", m["vivo"], m["fav-vivo"])
	}
}

// Uno que vuelve a contestar pasa solo de «Offline» a activo (vivo, con ping nuevo), favorito o no.
func TestListaOfflineVuelveSolo(t *testing.T) {
	for _, fav := range []bool{true, false} {
		antes := vistaLista([]sala.ServidorVisto{sv("f", sala.EstadoSinRespuesta, -1, fav, false)}, false, false)
		if len(antes) != 1 || antes[0].Estado != sala.EstadoSinRespuesta {
			t.Fatalf("fav=%v antes: %+v", fav, antes)
		}
		despues := vistaLista([]sala.ServidorVisto{sv("f", sala.EstadoVivo, 44, fav, false)}, true, false)
		if len(despues) != 1 || despues[0].Estado != sala.EstadoVivo || despues[0].PingMs != 44 {
			t.Fatalf("fav=%v despues: %+v", fav, despues)
		}
	}
}

// El servidor propio: si no está en marcha y público no sale NUNCA (ni vivo, ni del caché, ni como favorito).
func TestListaServidorPropio(t *testing.T) {
	filas := []sala.ServidorVisto{
		sv("yo-cache", sala.EstadoCache, 9, false, true),
		sv("yo-vivo", sala.EstadoVivo, 1, false, true),
		sv("yo-fav", sala.EstadoSinRespuesta, 9, true, true),
		sv("otro", sala.EstadoVivo, 8, false, false),
	}
	for _, buscando := range []bool{true, false} {
		r := vistaLista(filas, buscando, false)
		if len(r) != 1 || r[0].ID != "otro" {
			t.Fatalf("parado (buscando=%v): solo debía salir «otro»: %+v", buscando, r)
		}
	}
	// en marcha y público: sale (la página ofrece «Play here», no entrar)
	m := porID(vistaLista(filas, false, true))
	if _, ok := m["yo-vivo"]; !ok {
		t.Fatalf("el propio en marcha debía salir: %+v", m)
	}
	if !m["yo-vivo"].Propio {
		t.Fatal("debía seguir marcado Propio")
	}
	// ...y si no ha contestado al acabar la búsqueda (aún no anunciado), tampoco sale «Offline»: la vista del servidor
	// está en «Host a server»
	m = porID(vistaLista(filas, false, true))
	for _, id := range []string{"yo-cache", "yo-fav"} {
		if _, ok := m[id]; ok {
			t.Errorf("%s no debía salir como Offline", id)
		}
	}
	if _, ok := porID(vistaLista(filas, true, true))["yo-cache"]; !ok {
		t.Error("mientras se busca, el propio del caché se comprueba como los demás")
	}
}

// Nada que no esté vivo lleva ping, venga como venga.
func TestListaNuncaPingViejo(t *testing.T) {
	for _, estado := range []string{sala.EstadoCache, sala.EstadoSinRespuesta, sala.EstadoPrivado} {
		for _, buscando := range []bool{true, false} {
			for _, s := range vistaLista([]sala.ServidorVisto{sv("a", estado, 77, true, false)}, buscando, false) {
				if s.Estado != sala.EstadoVivo && (s.PingMs != -1 || s.PorRelay) {
					t.Fatalf("estado %s buscando=%v: ping viejo %+v", estado, buscando, s)
				}
			}
		}
	}
}

// 12-10: un servidor que contesta pero ya no es público se queda en la lista como «Private now» (sin ping; la página no
// deja entrar y dice que pidan el código), también siendo favorito. El propio, nunca.
func TestListaServidorQuePasoAPrivadoSaleComoPrivado(t *testing.T) {
	filas := []sala.ServidorVisto{
		sv("priv", sala.EstadoPrivado, 12, false, false),
		sv("priv-fav", sala.EstadoPrivado, -1, true, false),
		sv("yo-priv", sala.EstadoPrivado, -1, false, true),
		sv("ok", sala.EstadoVivo, 10, false, false),
	}
	for _, buscando := range []bool{true, false} {
		m := porID(vistaLista(filas, buscando, true))
		if len(m) != 3 || m["priv"].Estado != sala.EstadoPrivado || m["priv"].PingMs != -1 || m["priv-fav"].Estado != sala.EstadoPrivado {
			t.Fatalf("buscando=%v: %+v", buscando, m)
		}
		if _, ok := m["yo-priv"]; ok {
			t.Fatalf("el propio no sale como privado: %+v", m)
		}
	}
}

// El rechazo explícito del anfitrión se traduce a su mensaje concreto y no deja «Retry».
func TestTraducirRechazosDelAnfitrion(t *testing.T) {
	priv := fmt.Errorf("x: %w", sala.ErrPrivado)
	if m, _, _ := traducirError(priv); m.K != "msg.err.privado" {
		t.Fatalf("privado: %+v", m)
	}
	if m, _, _ := traducirError(sala.ErrLleno); m.K != "msg.err.lleno" {
		t.Fatalf("lleno: %+v", m)
	}
}
