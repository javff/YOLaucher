package sala

// Puerto fijo del anfitrión y si es accesible desde Internet (NET, 12-10; NOTES-red.md §13).
//
// Por defecto libp2p escucha en un puerto aleatorio (TCP y UDP/QUIC) y se apaña con UPnP, relays y hole punching. Con
// «Fixed port» (Ajustes → Red) escucha SIEMPRE en el mismo puerto, TCP y UDP, para que el anfitrión pueda abrirlo en su
// router («a la antigua»): así se puede entrar con «Join by address» aunque un día no haya DHT pública ni relays.
// Si el puerto está ocupado se usa uno aleatorio y se dice (nunca se queda sin servidor por eso).

import (
	"errors"
	"fmt"
	"net"
	"strconv"
	"sync"
	"time"

	"github.com/libp2p/go-libp2p/core/host"
	ma "github.com/multiformats/go-multiaddr"
	manet "github.com/multiformats/go-multiaddr/net"
)

// PuertoFijoPorDefecto: el que propone Ajustes → Red (fuera de los del juego y de los rangos que usan las pruebas).
const PuertoFijoPorDefecto = 47300

var (
	ErrPuertoRango = errors.New("el puerto tiene que estar entre 1024 y 65535")
	ErrPuertoJuego = errors.New("ese puerto lo usa el juego en este PC")
)

// ValidarPuertoFijo: un puerto que se puede pedir como fijo (no los del juego ni los de su consola/panel/gestión).
func ValidarPuertoFijo(p int) error {
	if p < 1024 || p > 65535 {
		return ErrPuertoRango
	}
	if permitido(p) || p == 5730 || p == 5799 || p == 8730 || p == 8790 || p == 8791 || (p >= 5730 && p <= 5876) {
		return ErrPuertoJuego
	}
	return nil
}

// PuertoLibre: se puede escuchar en ese puerto en TCP y en UDP (en todas las interfaces, o solo en 127.0.0.1). Se
// comprueba ANTES de dárselo a libp2p porque su TCP usa SO_REUSEADDR y en Windows podría compartir el puerto con otro
// programa sin dar error.
func PuertoLibre(p int, loopback bool) error {
	ip := "0.0.0.0"
	if loopback {
		ip = "127.0.0.1"
	}
	a := net.JoinHostPort(ip, strconv.Itoa(p))
	ln, err := net.Listen("tcp4", a)
	if err != nil {
		return fmt.Errorf("TCP %d en uso: %w", p, err)
	}
	ln.Close()
	pc, err := net.ListenPacket("udp4", a)
	if err != nil {
		return fmt.Errorf("UDP %d en uso: %w", p, err)
	}
	pc.Close()
	return nil
}

// dirsEscucha: las direcciones de escucha de libp2p con ese puerto (0 = aleatorio).
func dirsEscucha(puerto int, loopback bool) []string {
	p := strconv.Itoa(puerto)
	if loopback {
		return []string{"/ip4/127.0.0.1/udp/" + p + "/quic-v1", "/ip4/127.0.0.1/tcp/" + p}
	}
	return []string{"/ip4/0.0.0.0/udp/" + p + "/quic-v1", "/ip4/0.0.0.0/tcp/" + p, "/ip6/::/udp/" + p + "/quic-v1", "/ip6/::/tcp/" + p}
}

// AccesoRed: lo que enseña «Your server» sobre cómo se llega a este PC desde Internet. Nada de esto es secreto (la IP
// pública ya la ve cualquiera que conecte; el código de sala no va aquí).
type AccesoRed struct {
	Puerto     int    // puerto TCP en que escucha ahora
	PuertoUDP  int    // puerto UDP (QUIC); con puerto fijo, el mismo
	Pedido     int    // el puerto fijo pedido (0 = aleatorio)
	Fijo       bool   // escucha en el puerto fijo pedido
	Ocupado    bool   // el fijo estaba ocupado: escucha en uno aleatorio
	Alcance    string // AutoNAT: "Public" | "Private" | "Unknown"
	DirPublica string // «IP:puerto» por la que se llega desde Internet, si se sabe
	Confirmada bool   // AutoNAT v2 la ha confirmado (si no, es la que ven los demás con «Public»)
	IPLocal    string // la IP de este PC en la red de casa (a la que hay que redirigir el puerto en el router)
}

// Acceso: el estado de ahora.
func (n *NodoP2P) Acceso() AccesoRed {
	a := AccesoRed{Pedido: n.o.Puerto, Ocupado: n.puertoErr != nil, Alcance: "Unknown"}
	a.Puerto, a.PuertoUDP = puertosEscucha(n.H)
	a.Fijo = n.o.Puerto > 0 && !a.Ocupado && a.Puerto == n.o.Puerto
	if v, _ := n.alcance.Load().(string); v != "" {
		a.Alcance = v
	}
	reach, _ := n.alcanzables.Load().([]ma.Multiaddr)
	if d := dirPublica(reach, a.Puerto); d != "" {
		a.DirPublica, a.Confirmada = d, true
	} else if a.Alcance == "Public" {
		a.DirPublica = dirPublica(n.H.Addrs(), a.Puerto)
	}
	a.IPLocal = ipLocal(n.H)
	return a
}

func puertosEscucha(h host.Host) (tcp, udp int) {
	for _, d := range h.Network().ListenAddresses() {
		if v, err := d.ValueForProtocol(ma.P_TCP); err == nil && tcp == 0 {
			tcp, _ = strconv.Atoi(v)
		}
		if v, err := d.ValueForProtocol(ma.P_UDP); err == nil && udp == 0 {
			udp, _ = strconv.Atoi(v)
		}
	}
	return
}

// dirPublica: la mejor «IP:puerto» pública de la lista: IPv4 TCP con nuestro puerto, luego IPv4 con cualquiera (UPnP
// puede dar otro puerto fuera), luego IPv6. "" = ninguna.
func dirPublica(addrs []ma.Multiaddr, puerto int) string {
	mejor, nota := "", 0
	for _, a := range addrs {
		if !manet.IsPublicAddr(a) {
			continue
		}
		var ip string
		v4 := true
		if v, err := a.ValueForProtocol(ma.P_IP4); err == nil {
			ip = v
		} else if v, err := a.ValueForProtocol(ma.P_IP6); err == nil {
			ip, v4 = "["+v+"]", false
		} else {
			continue
		}
		p, err := a.ValueForProtocol(ma.P_TCP)
		tcp := err == nil
		if !tcp {
			if p, err = a.ValueForProtocol(ma.P_UDP); err != nil {
				continue
			}
		}
		n := 1
		switch {
		case v4 && tcp && p == strconv.Itoa(puerto):
			n = 5
		case v4 && p == strconv.Itoa(puerto):
			n = 4
		case v4 && tcp:
			n = 3
		case v4:
			n = 2
		}
		if n > nota {
			mejor, nota = ip+":"+p, n
		}
	}
	return mejor
}

var cacheIPLocal struct {
	sync.Mutex
	t  time.Time
	ip string
}

// ipLocal: la IPv4 de este PC en su red (la de la ruta por defecto; si no hay, la primera privada en que escucha). La
// página pregunta cada segundo: se recuerda 30 s.
func ipLocal(h host.Host) string {
	cacheIPLocal.Lock()
	defer cacheIPLocal.Unlock()
	if time.Since(cacheIPLocal.t) < 30*time.Second {
		return cacheIPLocal.ip
	}
	cacheIPLocal.t, cacheIPLocal.ip = time.Now(), buscarIPLocal(h)
	return cacheIPLocal.ip
}

func buscarIPLocal(h host.Host) string {
	if c, err := net.Dial("udp4", "192.0.2.1:9"); err == nil { // UDP: no manda nada, solo elige la interfaz de salida
		ip := c.LocalAddr().(*net.UDPAddr).IP
		c.Close()
		if ip.IsPrivate() {
			return ip.String()
		}
	}
	addrs, _ := h.Network().InterfaceListenAddresses()
	for _, a := range addrs {
		if v, err := a.ValueForProtocol(ma.P_IP4); err == nil && manet.IsPrivateAddr(a) && !manet.IsIPLoopback(a) {
			return v
		}
	}
	return ""
}
