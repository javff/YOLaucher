package sala

// Vía P2P: go-libp2p (la pila de IPFS) sobre la DHT pública.
//   - Anfitrión: su identidad (identidad.key; el peer ID va en el código), QUIC + TCP, UPnP (NATPortMap), AutoRelay con relays públicos de la red
//     IPFS (solo para la cita), DCUtR (hole punching) y se anuncia en la DHT con Provide(CID de la sala). mDNS para LAN.
//   - Invitado: busca el CID en la DHT (y el peer ID con FindPeer), conecta (directo o por relay), espera a que DCUtR
//     consiga la conexión DIRECTA y abre un stream. Los relays públicos tienen límites (2 min / 128 KiB): el juego no
//     va nunca por ellos; si no hay conexión directa, se pasa a Tor.

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/ipfs/go-cid"
	"github.com/libp2p/go-libp2p"
	dht "github.com/libp2p/go-libp2p-kad-dht"
	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/core/event"
	"github.com/libp2p/go-libp2p/core/host"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/core/protocol"
	"github.com/libp2p/go-libp2p/core/routing"
	"github.com/libp2p/go-libp2p/p2p/discovery/mdns"
	"github.com/libp2p/go-libp2p/p2p/host/autorelay"
	"github.com/libp2p/go-libp2p/p2p/protocol/holepunch"
	ma "github.com/multiformats/go-multiaddr"
	manet "github.com/multiformats/go-multiaddr/net"
)

const ProtoTunel = protocol.ID("/yo2online/tunel/1.0.0")

type NodoP2P struct {
	H   host.Host
	DHT *dht.IpfsDHT
	md  mdns.Service   // invitado: el de la sala que se busca
	mdA []mdns.Service // anfitrión: uno por nombre anunciado (sala privada y, si es pública, la pública)

	fin     chan struct{}
	o       OpcP2P
	nat     atomic.Value // string: tipo de NAT de esta red (lo deduce libp2p con lo que ven otros pares)
	alcance atomic.Value // string: Public / Private / Unknown (AutoNAT)
	ultHP   atomic.Value // string: resultado del último hole punching
	natM    atomic.Value // Msg: lo mismo que nat, con clave para la ventana
	hpM     atomic.Value // Msg: lo mismo que ultHP
	// NET (12-10): direcciones que AutoNAT v2 ha confirmado accesibles desde Internet ([]ma.Multiaddr) y si el puerto
	// fijo pedido estaba ocupado (se escucha en uno aleatorio). Ver puerto.go.
	alcanzables atomic.Value
	puertoErr   error
}

// ErrSinDHT: no se llega a la DHT pública (sin Internet o con UDP/TCP saliente bloqueado).
var ErrSinDHT = errors.New("no se llega a la DHT pública (¿sin Internet o UDP/TCP saliente bloqueado?)")

type OpcP2P struct {
	Clave   crypto.PrivKey // nil = aleatoria (invitado)
	Anfitr  bool
	SinMDNS bool
	Logf    func(string, ...any)
	Diag    func(string) // mensajes de diagnóstico para la consola (nil = solo al log)
	// Puerto: puerto FIJO para TCP y UDP/QUIC (0 = aleatorio, lo de siempre). Si está ocupado se usa uno aleatorio y
	// Acceso() lo dice (puerto.go). Para que el anfitrión pueda abrirlo en su router (y UPnP/NAT-PMP lo pida igual).
	Puerto int
	// Relays: candidatos fijos a relay (lista de nodos firmada / del usuario, red_nodos.go): se ofrecen a AutoRelay
	// antes que los pares de la tabla de la DHT. Solo anfitrión.
	Relays []peer.AddrInfo

	// Solo pruebas (red en memoria de este proceso, sin tocar la DHT pública):
	Arranque     []peer.AddrInfo // nodos de arranque de la DHT (nil = los públicos de IPFS)
	SoloLoopback bool            // escuchar solo en 127.0.0.1 y sin UPnP
	SinNAT       bool            // sin UPnP/NAT-PMP (pruebas en la red local de verdad: no tocar el router)
	MinPares     int             // pares en la tabla de rutas para dar la DHT por alcanzada (0 = 3)
}

func (n *NodoP2P) diag(m string) {
	n.o.Logf("[p2p] %s", m)
	if n.o.Diag != nil {
		n.o.Diag(m)
	}
}

// trazaHP recibe los sucesos del hole punching (DCUtR) para el log.
type trazaHP struct{ n *NodoP2P }

func (t trazaHP) Trace(e *holepunch.Event) {
	n := t.n
	if n == nil || n.o.Logf == nil {
		return
	}
	switch ev := e.Evt.(type) {
	case *holepunch.StartHolePunchEvt:
		n.o.Logf("[p2p] hole punching con %s: empieza (RTT por el relay %v; sus direcciones %v)", e.Remote, ev.RTT, ev.RemoteAddrs)
	case *holepunch.EndHolePunchEvt:
		if ev.Success {
			n.ultHP.Store("hole punching OK")
			n.hpM.Store(M("msg.red.hp_ok", "hole punching OK"))
			n.diag(fmt.Sprintf("Hole punching conseguido en %v: conexión directa.", ev.EllapsedTime.Round(time.Millisecond)))
		} else {
			n.ultHP.Store("hole punching fallido: " + ev.Error)
			// el error de libp2p (direcciones, «black hole»...) es larguísimo: solo al log (línea de abajo), no a la ventana
			n.hpM.Store(M("msg.red.hp_fallo", "la última conexión directa falló: ese jugador entra por Tor"))
			n.o.Logf("[p2p] hole punching fallido tras %v: %s", ev.EllapsedTime.Round(time.Millisecond), ev.Error)
		}
	case *holepunch.DirectDialEvt:
		n.o.Logf("[p2p] marcado directo (antes del hole punching): ok=%v %v %s", ev.Success, ev.EllapsedTime.Round(time.Millisecond), ev.Error)
	case *holepunch.HolePunchAttemptEvt:
		n.o.Logf("[p2p] hole punching: intento %d", ev.Attempt)
	case *holepunch.ProtocolErrorEvt:
		n.o.Logf("[p2p] hole punching: error de protocolo: %s", ev.Error)
	}
}

// vigilarRed: lo que libp2p averigua de la red de ESTE equipo (AutoNAT, tipo de NAT, direcciones públicas).
func (n *NodoP2P) vigilarRed() {
	sub, err := n.H.EventBus().Subscribe([]any{new(event.EvtLocalReachabilityChanged),
		new(event.EvtNATDeviceTypeChanged), new(event.EvtHostReachableAddrsChanged)})
	if err != nil {
		n.o.Logf("[p2p] eventos de red: %v", err)
		return
	}
	defer sub.Close()
	for {
		select {
		case <-n.fin:
			return
		case e, ok := <-sub.Out():
			if !ok {
				return
			}
			switch ev := e.(type) {
			case event.EvtLocalReachabilityChanged:
				n.alcance.Store(ev.Reachability.String())
				switch ev.Reachability {
				case network.ReachabilityPublic:
					n.diag("Red: este equipo es accesible desde Internet (puerto abierto o UPnP): entrarán directo.")
				case network.ReachabilityPrivate:
					if n.o.Anfitr {
						n.diag("Red: este equipo está detrás de NAT sin puerto abierto. Los de fuera entran con relay + " +
							"hole punching; si su red no lo permite (p. ej. datos móviles con NAT simétrico), por Tor.")
					} else {
						n.o.Logf("[p2p] alcanzabilidad: privada (detrás de NAT)")
					}
				default:
					n.o.Logf("[p2p] alcanzabilidad: %v", ev.Reachability)
				}
			case event.EvtNATDeviceTypeChanged:
				tipo, k := "cono (hole punching posible)", "msg.red.nat_cono"
				if ev.NatDeviceType == network.NATDeviceTypeSymmetric {
					tipo, k = "simétrico (el hole punching casi nunca funciona)", "msg.red.nat_simetrico"
				} else if ev.NatDeviceType == network.NATDeviceTypeUnknown {
					tipo, k = "desconocido", "msg.red.nat_desconocido"
				}
				n.nat.Store(fmt.Sprintf("NAT %s para %v", tipo, ev.TransportProtocol))
				n.natM.Store(M(k, fmt.Sprintf("NAT %s para %v", tipo, ev.TransportProtocol), "proto", fmt.Sprint(ev.TransportProtocol)))
				n.diag(fmt.Sprintf("Red: NAT de esta red para %v: %s.", ev.TransportProtocol, tipo))
			case event.EvtHostReachableAddrsChanged:
				n.alcanzables.Store(append([]ma.Multiaddr(nil), ev.Reachable...))
				n.o.Logf("[p2p] direcciones públicas: accesibles=%v no accesibles=%v sin saber=%v",
					ev.Reachable, ev.Unreachable, ev.Unknown)
			}
		}
	}
}

// Red: resumen de lo que se sabe de la red de este equipo (para los mensajes de error).
func (n *NodoP2P) Red() string {
	var p []string
	if v, _ := n.alcance.Load().(string); v != "" {
		p = append(p, "alcanzabilidad "+v)
	}
	if v, _ := n.nat.Load().(string); v != "" {
		p = append(p, v)
	}
	if v, _ := n.ultHP.Load().(string); v != "" {
		p = append(p, v)
	}
	if len(p) == 0 {
		return "sin datos de la red todavía"
	}
	return strings.Join(p, "; ")
}

// RedMsgs: lo mismo que Red(), como mensajes con clave para la ventana (si no se sabe nada, un solo mensaje «sin datos»).
func (n *NodoP2P) RedMsgs() []Msg {
	var r []Msg
	if v, _ := n.alcance.Load().(string); v != "" {
		switch v { // en la ventana, en palabras; el valor crudo de AutoNAT sigue en Red() y en el log
		case "Public":
			r = append(r, M("msg.red.alcance_publica", "accesible desde internet"))
		case "Private":
			r = append(r, M("msg.red.alcance_privada", "detrás de un router sin puerto abierto"))
		default:
			r = append(r, M("msg.red.alcance", "alcanzabilidad "+v, "valor", v))
		}
	}
	if v, ok := n.natM.Load().(Msg); ok {
		r = append(r, v)
	}
	if v, ok := n.hpM.Load().(Msg); ok {
		r = append(r, v)
	}
	if len(r) == 0 {
		r = append(r, M("msg.red.sin_datos", "sin datos de la red todavía"))
	}
	return r
}

func NuevoNodo(o OpcP2P) (*NodoP2P, error) {
	n := &NodoP2P{fin: make(chan struct{}), o: o}
	var mu sync.Mutex
	fuente := func(ctx context.Context, num int) <-chan peer.AddrInfo {
		ch := make(chan peer.AddrInfo)
		go func() {
			defer close(ch)
			for _, ai := range o.Relays { // los fijos primero (si la DHT pública cambia, siguen ahí)
				select {
				case ch <- ai:
				case <-ctx.Done():
					return
				}
				num--
				if num <= 0 {
					return
				}
			}
			mu.Lock()
			d := n.DHT
			mu.Unlock()
			if d == nil {
				return
			}
			for _, p := range d.RoutingTable().ListPeers() {
				ai := peer.AddrInfo{ID: p, Addrs: n.H.Peerstore().Addrs(p)}
				select {
				case ch <- ai:
				case <-ctx.Done():
					return
				}
				num--
				if num <= 0 {
					return
				}
			}
		}()
		return ch
	}
	puerto := 0
	if o.Puerto > 0 {
		if err := PuertoLibre(o.Puerto, o.SoloLoopback); err != nil {
			n.puertoErr = err
			n.diag(fmt.Sprintf("El puerto fijo %d está ocupado (%v): se usa uno aleatorio. Cámbialo en Ajustes → Red.", o.Puerto, err))
		} else {
			puerto = o.Puerto
		}
	}
	arranque := o.Arranque
	if arranque == nil {
		arranque = dht.GetDefaultBootstrapPeerAddrInfos()
	}
	opts := []libp2p.Option{
		libp2p.EnableHolePunching(holepunch.WithTracer(trazaHP{n})),
		libp2p.Routing(func(h host.Host) (routing.PeerRouting, error) {
			d, err := dht.New(h, dht.Mode(dht.ModeClient), dht.BootstrapPeers(arranque...))
			mu.Lock()
			n.DHT = d
			mu.Unlock()
			return d, err
		}),
	}
	if !o.SoloLoopback && !o.SinNAT {
		opts = append(opts, libp2p.NATPortMap()) // UPnP / NAT-PMP: con puerto fijo, pide ese mismo puerto al router
	}
	if o.Clave != nil {
		opts = append(opts, libp2p.Identity(o.Clave))
	}
	if o.Anfitr {
		opts = append(opts, libp2p.EnableAutoRelayWithPeerSource(fuente,
			autorelay.WithBootDelay(0), autorelay.WithMinCandidates(2), autorelay.WithNumRelays(2)))
		if !o.SoloLoopback {
			// AutoNAT v2: confirma dirección por dirección (IP pública:puerto) si es accesible desde Internet («Your server»)
			opts = append(opts, libp2p.EnableAutoNATv2())
		}
	}
	h, err := libp2p.New(append(opts, libp2p.ListenAddrStrings(dirsEscucha(puerto, o.SoloLoopback)...))...)
	if err != nil && puerto != 0 {
		// el puerto se ocupó entre la comprobación y ahora: aleatorio, como si estuviera ocupado desde el principio
		n.puertoErr = err
		n.diag(fmt.Sprintf("No se pudo escuchar en el puerto fijo %d (%v): se usa uno aleatorio.", puerto, err))
		h, err = libp2p.New(append(opts, libp2p.ListenAddrStrings(dirsEscucha(0, o.SoloLoopback)...))...)
	}
	if err != nil {
		return nil, err
	}
	n.H = h
	go n.vigilarRed()
	if err := n.DHT.Bootstrap(context.Background()); err != nil {
		o.Logf("[p2p] bootstrap: %v", err)
	}
	return n, nil
}

func (n *NodoP2P) Close() {
	select {
	case <-n.fin:
	default:
		close(n.fin)
	}
	if n.md != nil {
		n.md.Close()
	}
	for _, m := range n.mdA {
		m.Close()
	}
	if n.DHT != nil {
		n.DHT.Close()
	}
	n.H.Close()
}

// EsperarDHT: hasta que la tabla de rutas tenga pares (hace falta para Provide/FindProviders).
func (n *NodoP2P) EsperarDHT(ctx context.Context) error {
	min := n.o.MinPares
	if min <= 0 {
		min = 3
	}
	for n.DHT.RoutingTable().Size() < min {
		select {
		case <-ctx.Done():
			return ErrSinDHT
		case <-time.After(300 * time.Millisecond):
		}
	}
	return nil
}

// ---------------------------------------------------------------- anfitrión

type notifMDNS struct{ f func(peer.AddrInfo) }

func (m notifMDNS) HandlePeerFound(ai peer.AddrInfo) { m.f(ai) }

// Publicar: atiende streams del túnel y se anuncia en la DHT (y por mDNS). Vuelve enseguida; trabaja en segundo plano.
// (Lo de OpenServer: solo el CID de la sala privada. El lanzador nuevo usa Anunciar con la lista pública.)
func (n *NodoP2P) Publicar(ctx context.Context, s *Sala, a *Anfitrion, sinMDNS bool, listo func()) {
	n.Anunciar(ctx, a, Anuncio{Claves: []cid.Cid{s.Encuentro()}, SinMDNS: sinMDNS, NombresMDNS: []string{s.NombreMDNS()},
		Listo: listo})
}

// Anuncio: qué anuncia un anfitrión en la DHT.
type Anuncio struct {
	// Claves: los CID que se anuncian (Provide). Sala privada: solo el suyo. Sala pública: el suyo, el de la sala
	// pública y CIDLista() (el punto de encuentro de la lista pública). Una sala privada NUNCA lleva CIDLista().
	Claves  []cid.Cid
	SinMDNS bool
	// NombresMDNS: en la red local (mDNS) se anuncia con cada uno: el de la sala privada y, si es pública, el de la
	// pública (el invitado de la lista solo conoce ese). Hace falta: cuando libp2p ve que el anfitrión está detrás de
	// NAT deja de dar sus direcciones de la red local, y sin hairpin NAT en el router los de la misma red no llegan.
	NombresMDNS []string
	Listo       func()          // tras el primer anuncio en que TODAS las claves salieron bien (puede ser nil)
	Ronda       func(err error) // tras cada ronda de anuncios (nil = nada); err = el primero que falló
}

// Anunciar: como Publicar, con varias claves. Vuelve enseguida; trabaja en segundo plano hasta ctx.
func (n *NodoP2P) Anunciar(ctx context.Context, a *Anfitrion, an Anuncio) {
	n.H.SetStreamHandler(ProtoTunel, func(st network.Stream) {
		c := st.Conn()
		via := "P2P directo"
		if c.Stat().Limited {
			via = "P2P relay" // no debería pasar: el invitado no abre streams limitados
		}
		a.Logf("[p2p] stream de %s por %s", c.RemotePeer(), c.RemoteMultiaddr())
		if via == "P2P directo" {
			via += " (" + n.Describir(c) + ")"
		}
		a.Servir(st, via)
	})
	for _, nombre := range an.NombresMDNS {
		if an.SinMDNS || nombre == "" {
			break
		}
		m := mdns.NewMdnsService(n.H, nombre, notifMDNS{func(peer.AddrInfo) {}})
		if err := m.Start(); err != nil {
			a.Logf("[p2p] mdns: %v", err)
		}
		n.mdA = append(n.mdA, m)
	}
	go func() {
		if err := n.EsperarDHT(ctx); err != nil {
			a.Logf("[p2p] %v", err)
			return
		}
		sub, _ := n.H.EventBus().Subscribe(new(event.EvtLocalAddressesUpdated))
		var cambio <-chan any
		if sub != nil {
			defer sub.Close()
			cambio = sub.Out()
		}
		primera := true
		ultimo := time.Time{}
		for {
			// Se re-anuncia al principio, cada 10 min y cuando cambian las direcciones (p. ej. al conseguir relay):
			// los registros de proveedor guardan las direcciones del momento del anuncio.
			if time.Since(ultimo) > 20*time.Second {
				c2, cancel := context.WithTimeout(ctx, 2*time.Minute)
				t := time.Now()
				err := n.proveer(c2, an.Claves)
				cancel()
				ultimo = time.Now()
				a.Logf("[p2p] anuncio en la DHT (%d clave(s), %v): err=%v addrs=%v", len(an.Claves),
					time.Since(t).Round(time.Millisecond), err, n.H.Addrs())
				if an.Ronda != nil && ctx.Err() == nil {
					an.Ronda(err)
				}
				if err == nil && primera {
					primera = false
					if an.Listo != nil {
						an.Listo()
					}
				}
			}
			select {
			case <-ctx.Done():
				return
			case <-cambio:
				time.Sleep(5 * time.Second) // agrupar cambios seguidos
			case <-time.After(10 * time.Minute):
			}
		}
	}()
}

// proveer anuncia todas las claves a la vez; devuelve el primer error.
func (n *NodoP2P) proveer(ctx context.Context, claves []cid.Cid) error {
	errs := make(chan error, len(claves))
	for _, c := range claves {
		go func() { errs <- n.DHT.Provide(ctx, c, true) }()
	}
	var primero error
	for range claves {
		if err := <-errs; err != nil && primero == nil {
			primero = err
		}
	}
	return primero
}

// ---------------------------------------------------------------- invitado

// Conectar busca al anfitrión y devuelve un stream del túnel sobre una conexión DIRECTA.
func (n *NodoP2P) Conectar(ctx context.Context, s *Sala, sinMDNS bool, logf func(string, ...any)) (network.Stream, error) {
	return n.ConectarCon(ctx, s, sinMDNS, logf, nil)
}

// ConectarCon: como Conectar, con una pista (las direcciones del anfitrión que ya se conocen, p. ej. de la lista
// pública): se prueba enseguida, sin esperar a la DHT; si no vale, se sigue buscando como siempre.
func (n *NodoP2P) ConectarCon(ctx context.Context, s *Sala, sinMDNS bool, logf func(string, ...any), pista *peer.AddrInfo) (network.Stream, error) {
	id := s.PeerID()
	encontrado := make(chan peer.AddrInfo, 16)
	poner := func(ai peer.AddrInfo) {
		if ai.ID != id || len(ai.Addrs) == 0 {
			return
		}
		// Al peerstore ya: si se está esperando la conexión directa de otra vía (p. ej. relay), el marcado directo
		// de abajo las prueba en su siguiente intento sin esperar a que acabe esa espera (mDNS llega tarde a menudo).
		n.H.Peerstore().AddAddrs(id, ai.Addrs, 10*time.Minute)
		select {
		case encontrado <- ai:
		default:
		}
	}
	if pista != nil {
		if len(pista.Addrs) == 0 && len(n.H.Network().ConnsToPeer(id)) > 0 {
			// ya hay conexión con él (la de pedir la ficha): basta con su peerstore
			poner(peer.AddrInfo{ID: id, Addrs: n.H.Peerstore().Addrs(id)})
		}
		poner(*pista)
	}
	if !sinMDNS {
		if n.md != nil { // nodo compartido (lanzador): el mDNS de la entrada anterior
			n.md.Close()
		}
		n.md = mdns.NewMdnsService(n.H, s.NombreMDNS(), notifMDNS{func(ai peer.AddrInfo) {
			logf("[p2p] mDNS: anfitrión en la red local %v", ai.Addrs)
			poner(ai)
		}})
		n.md.Start()
	}
	// Hasta dónde se llegó (para explicar el fallo): 0 nada, 1 DHT alcanzada, 2 sala encontrada, 3 conectado por relay
	var etapa atomic.Int32
	t0 := time.Now()
	go func() {
		if err := n.EsperarDHT(ctx); err != nil {
			if ctx.Err() == context.DeadlineExceeded { // si es Canceled, ya se conectó por otro lado (mDNS): no es un fallo
				logf("[p2p] %v", err)
			}
			return
		}
		etapa.CompareAndSwap(0, 1)
		logf("[p2p] DHT pública alcanzada en %v (%d pares)", time.Since(t0).Round(time.Millisecond), n.DHT.RoutingTable().Size())
		for ctx.Err() == nil {
			c2, cancel := context.WithTimeout(ctx, 20*time.Second)
			for ai := range n.DHT.FindProvidersAsync(c2, s.Encuentro(), 3) {
				logf("[p2p] DHT: proveedor %s %v", ai.ID, ai.Addrs)
				if ai.ID == id {
					etapa.CompareAndSwap(1, 2)
				}
				poner(ai)
			}
			if ai, err := n.DHT.FindPeer(c2, id); err == nil {
				logf("[p2p] DHT: FindPeer %v", ai.Addrs)
				etapa.CompareAndSwap(1, 2)
				poner(ai)
			}
			cancel()
			select {
			case <-ctx.Done():
			case <-time.After(3 * time.Second):
			}
		}
	}()

	var ultimoErr error
	for {
		select {
		case <-ctx.Done():
			if ultimoErr == nil {
				switch etapa.Load() {
				case 0:
					ultimoErr = errors.New("no se llega a la DHT pública de libp2p (¿sin Internet, o la red bloquea UDP/TCP saliente?)")
				default:
					ultimoErr = errors.New("la sala no aparece en la DHT: ¿está OpenServer abierto? (recién abierto tarda ~1 min en anunciarse)")
				}
			}
			return nil, fmt.Errorf("P2P sin éxito en %v: %w [red de este equipo: %s]", time.Since(t0).Round(time.Second), ultimoErr, n.Red())
		case ai := <-encontrado:
			n.H.Peerstore().AddAddrs(ai.ID, ai.Addrs, 10*time.Minute)
			c2, cancel := context.WithTimeout(ctx, 15*time.Second)
			err := n.H.Connect(c2, peer.AddrInfo{ID: id})
			cancel()
			if err != nil {
				ultimoErr = fmt.Errorf("se encontró la sala pero no se pudo conectar con el anfitrión (ni por relay): %w", err)
				logf("[p2p] connect: %v", err)
				continue
			}
			etapa.Store(3)
			for _, c := range n.H.Network().ConnsToPeer(id) {
				logf("[p2p] conectado con el anfitrión por %s limitada=%v", c.RemoteMultiaddr(), c.Stat().Limited)
			}
			// Si solo hay conexión por relay, DCUtR (lo inicia el anfitrión al recibirla) intenta la directa. A la vez se
			// prueban TODAS las direcciones que el anfitrión anuncia por identify (también las privadas): cubre la misma
			// red local / el mismo equipo cuando el router no hace hairpin NAT y mDNS no llega.
			if !n.directa(id) {
				go func() {
					// identify tarda un poco en traer todas las direcciones: varios intentos
					for _, espera := range []time.Duration{time.Second, 3 * time.Second, 4 * time.Second, 6 * time.Second} {
						time.Sleep(espera)
						if n.directa(id) || ctx.Err() != nil {
							return
						}
						var dir []ma.Multiaddr
						for _, a := range n.H.Peerstore().Addrs(id) {
							if !strings.Contains(a.String(), "p2p-circuit") {
								dir = append(dir, a)
							}
						}
						c3, cancel := context.WithTimeout(network.WithForceDirectDial(ctx, "directa"), 6*time.Second)
						err := n.H.Connect(c3, peer.AddrInfo{ID: id, Addrs: dir})
						cancel()
						logf("[p2p] marcado directo a %d direcciones: err=%v", len(dir), err)
					}
				}()
			}
			fin := time.Now().Add(20 * time.Second)
			for time.Now().Before(fin) && ctx.Err() == nil && !n.directa(id) {
				time.Sleep(250 * time.Millisecond)
			}
			if !n.directa(id) {
				ultimoErr = errors.New("se llegó al anfitrión por relay, pero el hole punching no ha conseguido la conexión " +
					"directa (los NAT de las dos redes no lo permiten; típico de datos móviles)")
				logf("[p2p] %v", ultimoErr)
				continue
			}
			for _, c := range n.H.Network().ConnsToPeer(id) {
				logf("[p2p] conexión %s %s limitada=%v", c.RemoteMultiaddr(), c.Stat().Direction, c.Stat().Limited)
			}
			st, err := n.H.NewStream(ctx, id, ProtoTunel)
			if err != nil {
				ultimoErr = err
				logf("[p2p] stream: %v", err)
				continue
			}
			return st, nil
		}
	}
}

// DescribirMsg: lo mismo que Describir, como mensaje con clave para la ventana: «P2P directo (...)».
func (n *NodoP2P) DescribirMsg(c network.Conn) Msg {
	a := c.RemoteMultiaddr()
	tr := "TCP"
	if strings.Contains(a.String(), "/quic") {
		tr = "QUIC/UDP"
	}
	es := "P2P directo (" + n.Describir(c) + ")"
	if manet.IsPrivateAddr(a) || manet.IsIPLoopback(a) {
		return M("msg.via.p2p_lan", es, "tr", tr)
	}
	if v, _ := n.ultHP.Load().(string); v == "hole punching OK" {
		return M("msg.via.p2p_holepunch", es, "tr", tr)
	}
	return M("msg.via.p2p_internet", es, "tr", tr)
}

// Describir: qué tipo de conexión directa es (para que el diagnóstico diga el camino real).
func (n *NodoP2P) Describir(c network.Conn) string {
	a := c.RemoteMultiaddr()
	tr := "TCP"
	if strings.Contains(a.String(), "/quic") {
		tr = "QUIC/UDP"
	}
	if manet.IsPrivateAddr(a) || manet.IsIPLoopback(a) {
		return "red local, " + tr
	}
	if v, _ := n.ultHP.Load().(string); v == "hole punching OK" {
		return "por Internet con hole punching, " + tr
	}
	return "por Internet, " + tr
}

func (n *NodoP2P) directa(id peer.ID) bool {
	for _, c := range n.H.Network().ConnsToPeer(id) {
		if !c.Stat().Limited {
			return true
		}
	}
	return false
}
