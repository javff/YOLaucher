package sala

// «Join by address» (NET, 12-10; NOTES-red.md §13): entrar DIRECTO por libp2p a host:puerto (IP o nombre DNS) del
// anfitrión, sin DHT ni relays, con el código de sala o la contraseña según su modo. Si un día desaparece la DHT pública
// (o los relays de IPFS), se sigue jugando «a la antigua»: el anfitrión abre su puerto fijo (puerto.go) en el router y
// pasa su dirección.
//
// Lo de siempre sigue exigiéndose igual: el túnel es el mismo saludo (YO2T3 con el código, YO2W3 con contraseña, YO2P3
// en un servidor público) y el anfitrión firma con su clave. La dirección solo dice A DÓNDE llamar, nunca da permiso.
//   - Con código: el peer ID sale del código; si quien contesta en esa dirección es otro, libp2p (Noise/TLS) lo corta.
//   - Sin código (Public / Password): no se sabe el peer ID. Se averigua con un primer marcado a un peer ID inventado:
//     el saludo de seguridad de libp2p falla diciendo el peer ID REAL de quien contesta (sec.ErrPeerIDMismatch). Con ese
//     peer ID se pide la ficha firmada (ficha.go) y se sigue como si viniera de la lista. Quien controle esa dirección
//     puede ser cualquiera: se ve su huella en la capa «Entrando…» y, con contraseña, solo recibe una prueba ligada a
//     SU clave (no sirve contra el servidor de verdad); igual que una fila de la lista con el mismo nombre que otra.
//   - Hidden: no atiende fichas → «hace falta el código».

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"net"
	"strconv"
	"strings"
	"time"

	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/core/sec"
	ma "github.com/multiformats/go-multiaddr"
)

var (
	// ErrDireccion: lo escrito no es una dirección (host:puerto, IP o nombre DNS).
	ErrDireccion = errors.New("la dirección no es válida (se espera IP:puerto o nombre:puerto)")
	// ErrDirSinRespuesta: nadie contesta (o no con libp2p) en esa dirección.
	ErrDirSinRespuesta = errors.New("no se llega directo a esa dirección")
	// ErrDirOculta: contesta, pero no es público ni con contraseña: hace falta el código de sala.
	ErrDirOculta = errors.New("ese servidor solo admite el código de sala")
	// ErrDirOtro: en la dirección contesta otro servidor que el del código.
	ErrDirOtro = errors.New("en esa dirección contesta otro servidor (no el del código)")
)

const esperaDireccion = 12 * time.Second

// Direccion: una dirección ya comprobada.
type Direccion struct {
	Texto string         // normalizada («1.2.3.4:47300», «[::1]:47300», «casa.example.org:47300» o la multiaddr)
	Info  peer.AddrInfo  // Info.ID = "" si no venía en la dirección (/p2p/...)
	TCP   []ma.Multiaddr // las de TCP primero (sirven para averiguar el peer ID)
}

func nombreDNSValido(h string) bool {
	if len(h) == 0 || len(h) > 253 {
		return false
	}
	for _, et := range strings.Split(strings.TrimSuffix(h, "."), ".") {
		if et == "" || len(et) > 63 || et[0] == '-' || et[len(et)-1] == '-' {
			return false
		}
		for _, c := range et {
			if !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '-') {
				return false
			}
		}
	}
	return true
}

// ParseDireccion acepta «host:puerto» (IP v4, [IPv6] o nombre DNS; sin puerto = PuertoFijoPorDefecto) o una multiaddr
// directa (/ip4|ip6|dns|dns4|dns6/…/tcp/N o /udp/N/quic-v1, con /p2p/<peer ID> opcional). Nunca relays.
func ParseDireccion(s string) (*Direccion, error) {
	s = strings.TrimSpace(strings.TrimPrefix(s, "\uFEFF"))
	if s == "" || len(s) > 300 || strings.ContainsFunc(s, func(r rune) bool { return r < ' ' || r == 0x7f }) {
		return nil, ErrDireccion
	}
	if strings.HasPrefix(s, "/") {
		return parseMultiaddrDir(s)
	}
	for _, pre := range []string{"yo2://", "tcp://", "udp://", "http://", "https://"} {
		s = strings.TrimPrefix(strings.ToLower(s), pre)
	}
	s = strings.TrimSuffix(strings.ToLower(s), "/")
	host, pstr, err := net.SplitHostPort(s)
	if err != nil {
		// sin puerto: «1.2.3.4», «casa.example.org» o «::1» / «[::1]»
		host, pstr = strings.TrimSuffix(strings.TrimPrefix(s, "["), "]"), strconv.Itoa(PuertoFijoPorDefecto)
	}
	p, err := strconv.Atoi(pstr)
	if err != nil || p < 1 || p > 65535 {
		return nil, ErrDireccion
	}
	var proto, texto string
	if ip := net.ParseIP(host); ip != nil {
		if ip.IsUnspecified() || ip.IsMulticast() {
			return nil, ErrDireccion
		}
		if v4 := ip.To4(); v4 != nil {
			proto, host, texto = "ip4", v4.String(), v4.String()+":"+pstr
		} else {
			proto, host, texto = "ip6", ip.String(), "["+ip.String()+"]:"+pstr
		}
	} else if nombreDNSValido(host) {
		proto, texto = "dns", host+":"+pstr
	} else {
		return nil, ErrDireccion
	}
	pstr = strconv.Itoa(p)
	tcp, err := ma.NewMultiaddr("/" + proto + "/" + host + "/tcp/" + pstr)
	if err != nil {
		return nil, ErrDireccion
	}
	quic, err := ma.NewMultiaddr("/" + proto + "/" + host + "/udp/" + pstr + "/quic-v1")
	if err != nil {
		return nil, ErrDireccion
	}
	return &Direccion{Texto: texto, Info: peer.AddrInfo{Addrs: []ma.Multiaddr{tcp, quic}}, TCP: []ma.Multiaddr{tcp}}, nil
}

func parseMultiaddrDir(s string) (*Direccion, error) {
	a, err := ma.NewMultiaddr(s)
	if err != nil || strings.Contains(a.String(), "p2p-circuit") {
		return nil, ErrDireccion
	}
	d := &Direccion{Texto: a.String()}
	base, id := peer.SplitAddr(a)
	if base == nil {
		return nil, ErrDireccion
	}
	d.Info.ID = id
	red := false
	for _, p := range []int{ma.P_IP4, ma.P_IP6, ma.P_DNS, ma.P_DNS4, ma.P_DNS6} {
		if _, err := base.ValueForProtocol(p); err == nil {
			red = true
		}
	}
	_, errT := base.ValueForProtocol(ma.P_TCP)
	_, errQ := base.ValueForProtocol(ma.P_QUIC_V1)
	if !red || (errT != nil && errQ != nil) {
		return nil, ErrDireccion
	}
	d.Info.Addrs = []ma.Multiaddr{base}
	if errT == nil {
		d.TCP = []ma.Multiaddr{base}
	}
	return d, nil
}

// todas: las direcciones con las de TCP delante.
func (d *Direccion) todas() []ma.Multiaddr {
	r := append([]ma.Multiaddr{}, d.TCP...)
	for _, a := range d.Info.Addrs {
		if !contieneAddr(r, a) {
			r = append(r, a)
		}
	}
	return r
}

// DescubrirPeer: el peer ID de quien contesta en esa dirección (ver la cabecera). Solo marcado directo.
func (n *NodoP2P) DescubrirPeer(ctx context.Context, d *Direccion) (peer.ID, error) {
	if d.Info.ID != "" {
		return d.Info.ID, nil
	}
	_, pub, err := crypto.GenerateEd25519Key(rand.Reader)
	if err != nil {
		return "", err
	}
	falso, err := peer.IDFromPublicKey(pub)
	if err != nil {
		return "", err
	}
	defer n.H.Peerstore().ClearAddrs(falso)
	var ultimo error
	for _, a := range d.todas() {
		c2, cancel := context.WithTimeout(network.WithForceDirectDial(ctx, "direccion"), esperaDireccion)
		err := n.H.Connect(c2, peer.AddrInfo{ID: falso, Addrs: []ma.Multiaddr{a}})
		cancel()
		var mm sec.ErrPeerIDMismatch
		if errors.As(err, &mm) && mm.Actual != "" {
			return mm.Actual, nil
		}
		if err == nil { // no puede pasar (nadie tiene la clave inventada); por si acaso, no se sigue
			n.H.Network().ClosePeer(falso)
			return "", fmt.Errorf("%w: respuesta imposible de %s", ErrDirSinRespuesta, a)
		}
		ultimo = err
		if ctx.Err() != nil {
			break
		}
	}
	return "", fmt.Errorf("%w (%s): %v", ErrDirSinRespuesta, d.Texto, ultimo)
}

// ConectarDireccion: conexión DIRECTA (nunca por relay) con id en las direcciones de d.
func (n *NodoP2P) ConectarDireccion(ctx context.Context, id peer.ID, d *Direccion) error {
	if d.Info.ID != "" && d.Info.ID != id {
		return ErrDirOtro
	}
	c2, cancel := context.WithTimeout(network.WithForceDirectDial(ctx, "direccion"), esperaDireccion)
	defer cancel()
	err := n.H.Connect(c2, peer.AddrInfo{ID: id, Addrs: d.todas()})
	var mm sec.ErrPeerIDMismatch
	switch {
	case errors.As(err, &mm):
		return ErrDirOtro
	case err != nil:
		return fmt.Errorf("%w (%s): %v", ErrDirSinRespuesta, d.Texto, err)
	case !n.directa(id):
		return fmt.Errorf("%w (%s): solo hay conexión por relay", ErrDirSinRespuesta, d.Texto)
	}
	return nil
}

// ConectarDirecto: como ConectarCon pero solo a la dirección d (sin mDNS, DHT ni relays): un stream del túnel.
func (n *NodoP2P) ConectarDirecto(ctx context.Context, s *Sala, d *Direccion, logf func(string, ...any)) (network.Stream, error) {
	id := s.PeerID()
	t0 := time.Now()
	if err := n.ConectarDireccion(ctx, id, d); err != nil {
		logf("[p2p] dirección %s: %v", d.Texto, err)
		return nil, err
	}
	for _, c := range n.H.Network().ConnsToPeer(id) {
		logf("[p2p] conectado por dirección en %v: %s limitada=%v", time.Since(t0).Round(time.Millisecond), c.RemoteMultiaddr(), c.Stat().Limited)
	}
	return n.H.NewStream(ctx, id, ProtoTunel)
}

// ServidorEnDireccion: la ficha del servidor que contesta en d (Public o Password). ErrDirOculta si no da fichas (Hidden).
func (l *Lanzador) ServidorEnDireccion(ctx context.Context, d *Direccion) (*ServidorVisto, error) {
	n, err := l.nodoLista()
	if err != nil {
		return nil, err
	}
	id, err := n.DescubrirPeer(ctx, d)
	if err != nil {
		return nil, err
	}
	if _, err := PubDePeer(id); err != nil {
		return nil, fmt.Errorf("%w: el que contesta no es un servidor de YO2 (%v)", ErrDirSinRespuesta, err)
	}
	if err := n.ConectarDireccion(ctx, id, d); err != nil {
		return nil, err
	}
	_, _, espera := l.limites()
	ff, rtt, relay, err := PedirFicha(ctx, n.H, id, espera)
	if errors.Is(err, ErrNoPublico) {
		return nil, fmt.Errorf("%w: %v", ErrDirOculta, err)
	}
	if err != nil {
		return nil, fmt.Errorf("%w (%s): sin ficha: %v", ErrDirSinRespuesta, d.Texto, err)
	}
	s := l.visto(ff, d.todas(), rtt, relay, EstadoVivo, time.Now())
	return &s, nil
}
