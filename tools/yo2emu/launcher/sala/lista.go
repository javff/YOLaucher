package sala

// Búsqueda de servidores públicos para el lanzador nuevo (ficha.go explica el protocolo; NOTES-red.md §8 el diseño).
//
// BuscarServidores:
//  1. devuelve AL MOMENTO lo guardado de la última vez (Estado = EstadoCache), para que la lista no salga vacía;
//  2. pregunta directamente (por su peer ID, sin pasar por la lista) a los favoritos y a los del caché: así un
//     aluvión de servidores basura en la lista pública no los puede tapar;
//  3. FindProvidersAsync(CIDLista()) y, a cada proveedor (sin repetir peer ID, como mucho MaxProveedores), le pide la
//     ficha con MaxConsultas peticiones a la vez y EsperaFicha por cada una: uno lento o malicioso solo ocupa un hueco
//     durante ese tiempo;
//  4. al acabar, guarda en %LOCALAPPDATA%\YO2Online\lista\servidores.json lo visto (fichas firmadas: al leerlas se
//     vuelven a comprobar).

import (
	"context"
	"crypto/ed25519"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/core/peer"
	ma "github.com/multiformats/go-multiaddr"
)

const (
	EstadoCache        = "cache"         // de la última vez; aún sin confirmar (sin ping, sin entrar: «Checking…»)
	EstadoVivo         = "vivo"          // ficha recibida ahora
	EstadoSinRespuesta = "sin-respuesta" // estaba en el caché (o es favorito) y ahora no contesta
	EstadoPrivado      = "privado"       // contesta, pero ya no es público (pasó a privado): «Private now»; fuera del caché

	maxCache    = 300
	edadCache   = 7 * 24 * time.Hour
	nombreCache = "servidores.json"
	nombreFavs  = "favoritos.json"
)

// ServidorVisto: una fila de la lista.
type ServidorVisto struct {
	ID         string    // peer ID: ES la identidad del servidor (favoritos, repetidos). El nombre no identifica.
	Huella     string    // XXXX-XXXX de su clave pública (para comprobarlo de palabra con el anfitrión)
	Ficha      Ficha     // lo que dice el anfitrión de sí mismo
	PingMs     int       // ida y vuelta al pedir la ficha; -1 = no medido
	PorRelay   bool      // la ficha llegó por un relay público: el ping es orientativo (el juego irá directo o por Tor)
	Estado     string    // EstadoCache | EstadoVivo | EstadoSinRespuesta | EstadoPrivado
	Visto      time.Time // cuándo llegó la ficha
	Favorito   bool
	Propio     bool // es el servidor que tiene abierto este mismo lanzador
	Compatible bool // misma versión del juego que este cliente

	firmada *FichaFirmada
	addrs   []ma.Multiaddr
}

// Lleno: no caben más (según la ficha).
func (s ServidorVisto) Lleno() bool { return s.Ficha.Jugadores >= s.Ficha.Max }

func (l *Lanzador) visto(ff *FichaFirmada, addrs []ma.Multiaddr, rtt time.Duration, relay bool, estado string,
	cuando time.Time) ServidorVisto {
	sv := ServidorVisto{ID: ff.ID.String(), Ficha: ff.Ficha, PingMs: -1, PorRelay: relay, Estado: estado, Visto: cuando,
		Compatible: ff.Ficha.VersionJuego == VersionJuegoCliente, firmada: ff, addrs: addrs}
	if s, err := ff.Sala(); err == nil {
		sv.Huella = s.Huella()
	}
	if rtt > 0 {
		sv.PingMs = max(1, int(rtt.Round(time.Millisecond)/time.Millisecond))
	}
	sv.Favorito = l.esFavorito(sv.ID)
	if id := l.IDAnfitrion(); id != "" && id == sv.ID {
		sv.Propio = true
	}
	return sv
}

// IDAnfitrion: el peer ID de la identidad de anfitrión de ESTE equipo (<Datos>anfitrionidentidad.key), haya o no un
// servidor en marcha: así una ficha vieja de nuestro propio servidor (del caché) también se reconoce como «tuyo». ""
// si todavía no hay identidad (nunca la crea).
func (l *Lanzador) IDAnfitrion() string {
	b, err := os.ReadFile(filepath.Join(l.Datos, "anfitrion", "identidad.key"))
	if err != nil || len(b) != ed25519.SeedSize {
		return ""
	}
	k, err := crypto.UnmarshalEd25519PublicKey(ed25519.NewKeyFromSeed(b).Public().(ed25519.PublicKey))
	if err != nil {
		return ""
	}
	id, err := peer.IDFromPublicKey(k)
	if err != nil {
		return ""
	}
	return id.String()
}

// ServidorPublicoEnMarcha: este lanzador tiene su servidor en marcha Y en la lista pública (solo entonces su fila
// tiene sentido en la lista).
func (l *Lanzador) ServidorPublicoEnMarcha() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.srv != nil && l.srv.publica != nil
}

// ---------------------------------------------------------------- caché

type entradaCache struct {
	ID    string   `json:"id"`
	Addrs []string `json:"addrs,omitempty"`
	Nonce []byte   `json:"nonce"`
	Datos []byte   `json:"ficha"`
	Firma []byte   `json:"firma"`
	Visto int64    `json:"visto"`
	Ping  int      `json:"ping_ms"`
	Relay bool     `json:"relay,omitempty"`
}

type ficheroCache struct {
	V          int            `json:"v"`
	Servidores []entradaCache `json:"servidores"`
}

func (l *Lanzador) dirLista() string { return filepath.Join(l.Datos, "lista") }

// leerCache: las entradas guardadas que siguen valiendo (firma comprobada otra vez, menos de una semana).
func (l *Lanzador) leerCache() []ServidorVisto {
	b, err := os.ReadFile(filepath.Join(l.dirLista(), nombreCache))
	if err != nil {
		return nil
	}
	var fc ficheroCache
	if json.Unmarshal(b, &fc) != nil || fc.V != 1 {
		return nil
	}
	var r []ServidorVisto
	for _, e := range fc.Servidores {
		id, err := peer.Decode(e.ID)
		// los favoritos no caducan: si hoy no contestan salen en «Offline» y reaparecen solos cuando vuelven
		if err != nil || (time.Since(time.Unix(e.Visto, 0)) > edadCache && !l.esFavorito(e.ID)) {
			continue
		}
		ff, err := VerificarFicha(id, e.Nonce, e.Datos, e.Firma)
		if err != nil {
			continue
		}
		var addrs []ma.Multiaddr
		for _, a := range e.Addrs {
			if m, err := ma.NewMultiaddr(a); err == nil {
				addrs = append(addrs, m)
			}
		}
		// OJO: el ping guardado NO se carga: es de otra sesión y no dice nada de ahora (PingMs = -1 hasta que conteste)
		r = append(r, l.visto(ff, addrs, 0, e.Relay, EstadoCache, time.Unix(e.Visto, 0)))
	}
	return r
}

func (l *Lanzador) guardarCache(svs []ServidorVisto) {
	sort.Slice(svs, func(i, j int) bool { return svs[i].Visto.After(svs[j].Visto) })
	fc := ficheroCache{V: 1}
	for _, s := range svs {
		if s.firmada == nil || len(fc.Servidores) >= maxCache || (time.Since(s.Visto) > edadCache && !l.esFavorito(s.ID)) {
			continue
		}
		e := entradaCache{ID: s.ID, Nonce: s.firmada.Nonce, Datos: s.firmada.Datos, Firma: s.firmada.Firma,
			Visto: s.Visto.Unix(), Ping: s.PingMs, Relay: s.PorRelay}
		for i, a := range s.addrs {
			if i < 8 {
				e.Addrs = append(e.Addrs, a.String())
			}
		}
		fc.Servidores = append(fc.Servidores, e)
	}
	b, _ := json.MarshalIndent(fc, "", " ")
	escribirAtomico(filepath.Join(l.dirLista(), nombreCache), b)
}

func escribirAtomico(p string, b []byte) error {
	if err := os.MkdirAll(filepath.Dir(p), 0o700); err != nil {
		return err
	}
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}

// ---------------------------------------------------------------- favoritos (por peer ID, nunca por nombre)

type ficheroFavs struct {
	V   int      `json:"v"`
	IDs []string `json:"ids"`
}

func (l *Lanzador) cargarFavs() {
	l.favOnce.Do(func() {
		l.favs = map[string]bool{}
		b, err := os.ReadFile(filepath.Join(l.dirLista(), nombreFavs))
		if err != nil {
			return
		}
		var f ficheroFavs
		if json.Unmarshal(b, &f) == nil {
			for _, id := range f.IDs {
				if _, err := peer.Decode(id); err == nil {
					l.favs[id] = true
				}
			}
		}
	})
}

func (l *Lanzador) esFavorito(id string) bool {
	l.cargarFavs()
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.favs[id]
}

// MarcarFavorito guarda (o quita) un servidor de favoritos. Los favoritos se preguntan siempre directamente.
func (l *Lanzador) MarcarFavorito(id string, si bool) error {
	if _, err := peer.Decode(id); err != nil {
		return err
	}
	l.cargarFavs()
	l.mu.Lock()
	if si {
		l.favs[id] = true
	} else {
		delete(l.favs, id)
	}
	f := ficheroFavs{V: 1}
	for k := range l.favs {
		f.IDs = append(f.IDs, k)
	}
	l.mu.Unlock()
	sort.Strings(f.IDs)
	b, _ := json.MarshalIndent(f, "", " ")
	return escribirAtomico(filepath.Join(l.dirLista(), nombreFavs), b)
}

// Favoritos: los peer ID marcados.
func (l *Lanzador) Favoritos() []string {
	l.cargarFavs()
	l.mu.Lock()
	defer l.mu.Unlock()
	var r []string
	for k := range l.favs {
		r = append(r, k)
	}
	sort.Strings(r)
	return r
}

// ---------------------------------------------------------------- búsqueda

type candidato struct {
	id      peer.ID
	addrs   []ma.Multiaddr
	antes   *ServidorVisto // del caché (para avisar si ya no contesta)
	directo bool           // favorito o del caché: se le pregunta aunque no salga en la lista
}

// BuscarServidores llama a cb por cada servidor (primero los del caché, marcados EstadoCache; luego cada ficha que
// llega). cb se llama de uno en uno (nunca a la vez) y tiene que volver pronto. Termina cuando acaba la búsqueda
// (como mucho EsperaBusqueda) o se cancela ctx; devuelve error solo si no se pudo ni empezar (sin red).
func (l *Lanzador) BuscarServidores(ctx context.Context, cb func(ServidorVisto)) error {
	l.buscando.Add(1)
	defer l.buscando.Add(-1)
	var cbMu sync.Mutex
	emitir := func(s ServidorVisto) {
		cbMu.Lock()
		defer cbMu.Unlock()
		cb(s)
	}
	cache := l.leerCache()
	for _, s := range cache {
		emitir(s)
	}
	n, err := l.nodoLista()
	if err != nil {
		return err
	}
	// 12-10: la espera a la DHT cuenta dentro del tope de la búsqueda. Antes iba sin tope: sin pares (sin red, cortafuegos)
	// BuscarServidores no acababa nunca y lo del caché se quedaba «Checking…» para siempre en la lista.
	ctx, cancel := context.WithTimeout(ctx, l.esperaBusqueda())
	defer cancel()
	if err := n.EsperarDHT(ctx); err != nil {
		return err
	}
	t0 := time.Now()

	var (
		mu       sync.Mutex
		vistos   = map[peer.ID]bool{}
		frescos  []ServidorVisto
		privados = map[string]bool{} // del caché que ahora dicen no ser públicos
	)
	maxProv, maxCons, espera := l.limites()
	cola := make(chan candidato, maxProv+maxCache+len(l.Favoritos())+1)
	encolar := func(c candidato) bool {
		mu.Lock()
		defer mu.Unlock()
		if vistos[c.id] || c.id == n.H.ID() {
			return false
		}
		vistos[c.id] = true
		cola <- c
		return true
	}
	l.vistos.Store(0)
	var wg sync.WaitGroup
	for range maxCons {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for c := range cola {
				if ctx.Err() != nil {
					continue // se vacía la cola sin hacer nada
				}
				if len(c.addrs) > 0 {
					n.H.Peerstore().AddAddrs(c.id, c.addrs, 10*time.Minute)
				}
				esp := espera
				if c.directo {
					esp = espera * 3 / 2 // puede tener que buscar su dirección en la DHT
				}
				ff, rtt, relay, err := PedirFicha(ctx, n.H, c.id, esp)
				if err != nil {
					l.logf("[lista] %s: %v", c.id, err)
					if c.antes != nil && ctx.Err() == nil {
						s := *c.antes
						s.Estado = EstadoSinRespuesta
						if errors.Is(err, ErrNoPublico) {
							s.Estado = EstadoPrivado
							mu.Lock()
							privados[s.ID] = true
							mu.Unlock()
						}
						emitir(s)
					}
					continue
				}
				s := l.visto(ff, n.H.Peerstore().Addrs(c.id), rtt, relay, EstadoVivo, time.Now())
				mu.Lock()
				frescos = append(frescos, s)
				mu.Unlock()
				l.vistos.Add(1)
				emitir(s)
			}
		}()
	}
	for i := range cache {
		id, _ := peer.Decode(cache[i].ID)
		encolar(candidato{id: id, addrs: cache[i].addrs, antes: &cache[i], directo: true})
	}
	for _, f := range l.Favoritos() {
		if id, err := peer.Decode(f); err == nil {
			encolar(candidato{id: id, directo: true})
		}
	}
	prov := 0
	for ai := range n.DHT.FindProvidersAsync(ctx, CIDLista(), maxProv) {
		if encolar(candidato{id: ai.ID, addrs: ai.Addrs}) {
			prov++
		}
	}
	close(cola)
	wg.Wait()
	l.logf("[lista] búsqueda terminada en %v: %d proveedores nuevos, %d fichas válidas", time.Since(t0).Round(time.Millisecond),
		prov, len(frescos))

	// caché = lo de ahora + lo de antes que no se ha visto esta vez (sigue valiendo una semana)
	todo := append([]ServidorVisto{}, frescos...)
	for _, s := range cache {
		id, _ := peer.Decode(s.ID)
		ok := false
		for _, f := range frescos {
			if f.ID == s.ID {
				ok = true
			}
		}
		mu.Lock()
		priv := privados[s.ID]
		mu.Unlock()
		if !ok && id != "" && !priv {
			todo = append(todo, s)
		}
	}
	l.guardarCache(todo)
	return nil
}

// ConsultarServidor: la ficha de UN servidor por su peer ID (sin buscar en la lista). Lo usa la ventana para volver a
// entrar en el mismo servidor tras reiniciarse por una autoactualización: primero mira el caché (direcciones) y le pide
// la ficha directamente, igual que a los favoritos. Devuelve error si no contesta.
func (l *Lanzador) ConsultarServidor(ctx context.Context, id string) (*ServidorVisto, error) {
	pid, err := peer.Decode(id)
	if err != nil {
		return nil, err
	}
	n, err := l.nodoLista()
	if err != nil {
		return nil, err
	}
	for _, s := range l.leerCache() {
		if s.ID == id && len(s.addrs) > 0 {
			n.H.Peerstore().AddAddrs(pid, s.addrs, 10*time.Minute)
		}
	}
	if err := n.EsperarDHT(ctx); err != nil {
		return nil, err
	}
	_, _, espera := l.limites()
	ff, rtt, relay, err := PedirFicha(ctx, n.H, pid, espera*3/2)
	if err != nil {
		return nil, err
	}
	s := l.visto(ff, n.H.Peerstore().Addrs(pid), rtt, relay, EstadoVivo, time.Now())
	return &s, nil
}
