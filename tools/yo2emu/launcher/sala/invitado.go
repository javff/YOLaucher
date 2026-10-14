package sala

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"io"
	"sync"
	"sync/atomic"
	"time"

	"github.com/hashicorp/yamux"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
)

// EsperaTor: tiempo máximo para conectar por Tor (incluye arrancar tor.exe: la primera vez, o con la red Tor lenta,
// solo eso puede llevar varios minutos; el 06-10 el Tor del anfitrión tardó 7).
const EsperaTor = 8 * time.Minute

// Invitado mantiene UNA sesión con el anfitrión: P2P primero y, si en EsperaP2P no hay conexión directa, Tor
// (que ya se ha ido conectando en paralelo desde el principio, para no sumar tiempos).
type Invitado struct {
	Sala      *Sala
	Clave     ed25519.PrivateKey // identidad de este invitado (CargarClaveInvitado): firma el saludo (T2)
	BinDir    string             // dónde se extrae tor.exe
	Dir       string             // datos de tor del invitado
	Modo      string             // "auto", "p2p" o "tor"
	EsperaP2P time.Duration
	SinMDNS   bool
	Logf      func(string, ...any)
	LogW      io.Writer
	Aviso     func(string)
	// AvisoK: la ventana. Si está, los avisos que tienen clave van aquí (y no a Aviso); la consola de JoinServer solo usa Aviso.
	AvisoK func(Msg)

	// Lanzador nuevo (lanzador.go); vacíos = lo de JoinServer:
	Nodo     *NodoP2P       // nodo P2P compartido (el de la lista): no se crea otro ni se cierra al acabar
	Pista    *peer.AddrInfo // direcciones del anfitrión ya conocidas (de la lista): se prueban sin esperar a la DHT
	Progreso func(string)   // "encontrado": hay un flujo con el anfitrión y empieza el saludo cifrado (nil = nada)
	// Direccion: «Join by address» (direccion.go): solo se llama a esa dirección, directo (sin mDNS, DHT ni relays).
	Direccion *Direccion

	mu      sync.Mutex
	ses     *yamux.Session
	Via     string
	ViaMsg  Msg      // lo mismo que Via, con clave para la ventana
	Informe []string // qué pasó con cada camino en la última conexión (para el diagnóstico)
	nodo    *NodoP2P
	tor     *Tor
	rech    *conRechazo
}

type resultado struct {
	ses  *yamux.Session
	rech *conRechazo // para saber si el anfitrión rechaza tras el saludo (lleno, sin sitio)
	via  string
	viaM Msg
	err  error
	nodo *NodoP2P
	tor  *Tor
	dur  time.Duration
}

func (i *Invitado) Conectar(ctx context.Context) error {
	i.mu.Lock()
	defer i.mu.Unlock()
	return i.conectar(ctx)
}

func (i *Invitado) intentoP2P(ctx context.Context) resultado {
	if i.Modo == "tor" {
		return resultado{err: errors.New("desactivado (-via tor)")}
	}
	t0 := time.Now()
	n := i.Nodo
	cerrarNodo := func() {}
	if n == nil {
		var err error
		if n, err = NuevoNodo(OpcP2P{Logf: i.Logf}); err != nil {
			return resultado{err: err}
		}
		cerrarNodo = n.Close
	}
	c2, c := context.WithTimeout(ctx, i.EsperaP2P)
	defer c()
	var st network.Stream
	var err error
	if i.Direccion != nil {
		st, err = n.ConectarDirecto(c2, i.Sala, i.Direccion, i.Logf)
	} else {
		st, err = n.ConectarCon(c2, i.Sala, i.SinMDNS, i.Logf, i.Pista)
	}
	if err != nil {
		cerrarNodo()
		return resultado{err: err}
	}
	i.progreso("encontrado")
	ses, rech, err := entrar(st, i.Sala, i.Clave, i.LogW)
	if err != nil {
		st.Close()
		cerrarNodo()
		return resultado{err: err}
	}
	via, viaM := "P2P directo ("+n.Describir(st.Conn())+")", n.DescribirMsg(st.Conn())
	i.Logf("[p2p] sesión lista en %v por %s %s", time.Since(t0).Round(time.Millisecond), via, st.Conn().RemoteMultiaddr())
	r := resultado{ses: ses, rech: rech, via: via, viaM: viaM, dur: time.Since(t0)}
	if i.Nodo == nil {
		r.nodo = n // solo se cierra al acabar si es nuestro
	}
	return r
}

func (i *Invitado) intentoTor(ctx context.Context, vivo *atomic.Pointer[Tor]) resultado {
	if i.Modo == "p2p" {
		return resultado{err: errors.New("desactivado (-via p2p)")}
	}
	t0 := time.Now()
	t, err := ClienteTor(i.BinDir, i.Dir, i.LogW)
	if err != nil {
		return resultado{err: err}
	}
	vivo.Store(t)
	c2, c := context.WithTimeout(ctx, EsperaTor)
	defer c()
	conn, err := t.DialOnion(c2, i.Sala, i.Logf)
	if err != nil {
		t.Cerrar()
		return resultado{err: err}
	}
	i.progreso("encontrado")
	ses, rech, err := entrar(conn, i.Sala, i.Clave, i.LogW)
	if err != nil {
		conn.Close()
		t.Cerrar()
		return resultado{err: err}
	}
	i.Logf("[tor] sesión lista en %v", time.Since(t0).Round(time.Millisecond))
	return resultado{ses: ses, rech: rech, via: "Tor (.onion)", viaM: M("msg.via.tor", "Tor (.onion)"), tor: t, dur: time.Since(t0)}
}

// avisar manda un aviso: con clave a la ventana (AvisoK) si la hay; si no, el texto en español a Aviso (consola).
func (i *Invitado) avisar(m Msg) {
	switch {
	case i.AvisoK != nil && m.K != "":
		i.AvisoK(m)
	case i.Aviso != nil:
		i.Aviso(m.Es)
	}
}

// ErrSinCamino: ni P2P ni Tor pudieron conectar. Error() es el texto de siempre (consola y log); la ventana lo reconoce
// con errors.As y enseña un mensaje traducido (el detalle de cada camino va en «detalles técnicos»).
type ErrSinCamino struct{ P2P, Tor error }

func (e *ErrSinCamino) Error() string { return fmt.Sprintf("- P2P: %v\n- Tor: %v", e.P2P, e.Tor) }

func (i *Invitado) progreso(m string) {
	if i.Progreso != nil {
		i.Progreso(m)
	}
}

func (r resultado) cerrar() {
	if r.ses != nil {
		r.ses.Close()
	}
	if r.nodo != nil {
		r.nodo.Close()
	}
	if r.tor != nil {
		r.tor.Cerrar()
	}
}

// esperarTor espera el resultado de Tor contando el progreso cada 20 s.
func (i *Invitado) esperarTor(torCh <-chan resultado, vivo *atomic.Pointer[Tor]) resultado {
	for {
		select {
		case r := <-torCh:
			return r
		case <-time.After(20 * time.Second):
			if t := vivo.Load(); t != nil {
				i.avisar(M("msg.aviso.tor_conectando", "Tor sigue conectando: "+t.Diagnostico(), "diag", t.DiagnosticoMsg()))
			}
		}
	}
}

func (i *Invitado) conectar(ctx context.Context) error {
	if i.ses != nil && !i.ses.IsClosed() {
		return nil
	}
	i.limpiar()
	p2pCh := make(chan resultado, 1)
	torCh := make(chan resultado, 1)
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	var torVivo atomic.Pointer[Tor] // para enseñar el progreso mientras se espera
	// Los dos a la vez (Tor tarda en arrancar): P2P manda, Tor solo se usa si P2P falla.
	go func() { p2pCh <- i.intentoP2P(ctx) }()
	go func() { torCh <- i.intentoTor(ctx, &torVivo) }()

	rp := <-p2pCh
	// Rechazo explícito del anfitrión (privado, lleno...): es definitivo, no tiene sentido probar Tor ni esperar 8 minutos
	var rz *ErrRechazado
	if errors.As(rp.err, &rz) {
		i.Logf("P2P: %v", rp.err)
		i.Informe = []string{"P2P: RECHAZADO, " + rp.err.Error(), "Tor: no se probó"}
		cancel()
		go func() { (<-torCh).cerrar() }()
		return rp.err
	}
	if rp.err == nil {
		i.ses, i.Via, i.ViaMsg, i.nodo, i.rech = rp.ses, rp.via, rp.viaM, rp.nodo, rp.rech
		i.Informe = []string{"P2P: OK, " + rp.via, "Tor: no hizo falta"}
		cancel() // Tor deja de intentarlo; si ya tenía sesión, se cierra
		go func() { (<-torCh).cerrar() }()
		return nil
	}
	i.Logf("P2P: %v", rp.err)
	if i.Direccion != nil { // por dirección no hay Tor: el error de la dirección tal cual (ErrDirSinRespuesta, ErrDirOtro...)
		i.Informe = []string{"P2P: FALLA, " + rp.err.Error(), "Tor: no se usa por dirección"}
		cancel()
		go func() { (<-torCh).cerrar() }()
		return rp.err
	}
	if i.Modo != "p2p" {
		i.avisar(M("msg.aviso.sin_p2p", "No hay conexión directa (P2P): "+rp.err.Error()))
		i.avisar(M("msg.aviso.probando_tor", "Probando por Tor: puede tardar unos minutos (la primera vez, más)..."))
	}
	rt := i.esperarTor(torCh, &torVivo)
	if rt.err == nil {
		i.ses, i.Via, i.ViaMsg, i.tor, i.rech = rt.ses, rt.via, rt.viaM, rt.tor, rt.rech
		i.Informe = []string{"P2P: FALLA, " + rp.err.Error(), "Tor: OK"}
		return nil
	}
	if errors.As(rt.err, &rz) { // por Tor sí llegó y el anfitrión dijo que no
		i.Informe = []string{"P2P: FALLA, " + rp.err.Error(), "Tor: RECHAZADO, " + rt.err.Error()}
		return rt.err
	}
	i.Logf("Tor: %v", rt.err)
	i.Informe = []string{"P2P: FALLA, " + rp.err.Error(), "Tor: FALLA, " + rt.err.Error()}
	return &ErrSinCamino{P2P: rp.err, Tor: rt.err}
}

// ProbarTodo (para -probar): intenta P2P y Tor HASTA EL FINAL los dos, aunque uno funcione, y devuelve un informe de
// cada camino. Se queda con la mejor sesión (P2P si va) para seguir comprobando el servidor.
func (i *Invitado) ProbarTodo(ctx context.Context) ([]string, error) {
	i.mu.Lock()
	defer i.mu.Unlock()
	i.limpiar()
	p2pCh := make(chan resultado, 1)
	torCh := make(chan resultado, 1)
	var torVivo atomic.Pointer[Tor]
	go func() { p2pCh <- i.intentoP2P(ctx) }()
	go func() { torCh <- i.intentoTor(ctx, &torVivo) }()
	rp := <-p2pCh
	if rp.err == nil {
		i.Aviso(fmt.Sprintf("P2P: OK en %v (%s). Esperando también a Tor para el informe...", rp.dur.Round(100*time.Millisecond), rp.via))
	} else {
		i.Aviso("P2P: FALLA: " + rp.err.Error())
	}
	rt := i.esperarTor(torCh, &torVivo)
	linea := func(nombre string, r resultado) string {
		if r.err != nil {
			return nombre + ": FALLA - " + r.err.Error()
		}
		l := fmt.Sprintf("%s: OK en %v - %s", nombre, r.dur.Round(100*time.Millisecond), r.via)
		if min, med, err := MedirEco(r.ses, 10); err == nil {
			l += fmt.Sprintf("; latencia mínima %v, media %v", min.Round(100*time.Microsecond), med.Round(100*time.Microsecond))
		}
		return l
	}
	inf := []string{linea("P2P", rp), linea("Tor", rt)}
	switch {
	case rp.err == nil:
		i.ses, i.Via, i.ViaMsg, i.nodo = rp.ses, rp.via, rp.viaM, rp.nodo
		rt.cerrar()
	case rt.err == nil:
		i.ses, i.Via, i.ViaMsg, i.tor = rt.ses, rt.via, rt.viaM, rt.tor
	default:
		return inf, errors.New("no funciona ningún camino")
	}
	i.Informe = inf
	return inf, nil
}

// Sesion devuelve la sesión viva o reconecta (si el anfitrión se reinició o la red cambió).
func (i *Invitado) Sesion(ctx context.Context) (*yamux.Session, error) {
	i.mu.Lock()
	defer i.mu.Unlock()
	if i.ses != nil && !i.ses.IsClosed() {
		return i.ses, nil
	}
	if i.rech != nil {
		if r := i.rech.Rechazo(); r != nil { // rechazado tras el saludo: reconectar no cambia nada
			return nil, r
		}
	}
	if i.ses != nil {
		i.avisar(M("msg.aviso.reconectando", "Se perdió la conexión con el anfitrión: reconectando..."))
	}
	if err := i.conectar(ctx); err != nil {
		return nil, err
	}
	i.avisar(M("msg.aviso.conectado_por", "Conectado por "+i.Via, "via", i.ViaMsg))
	return i.ses, nil
}

// Rechazo: si el anfitrión rechazó la entrada DESPUÉS del saludo (servidor lleno, sin sitio), lo dice; nil si no. La
// sesión ya está cerrada en ese caso: el que llama lo mira cuando el primer uso del túnel falla.
func (i *Invitado) Rechazo() *ErrRechazado {
	i.mu.Lock()
	r := i.rech
	i.mu.Unlock()
	if r == nil {
		return nil
	}
	return r.Rechazo()
}

// esperarRechazo: igual que Rechazo, pero esperando hasta medio segundo (el rechazo viaja un instante después de que el
// túnel se rompa). Solo se llama cuando algo ya ha fallado.
func (i *Invitado) esperarRechazo() *ErrRechazado {
	for n := 0; n < 10; n++ {
		if r := i.Rechazo(); r != nil {
			return r
		}
		time.Sleep(50 * time.Millisecond)
	}
	return nil
}

func (i *Invitado) limpiar() {
	i.rech = nil
	if i.ses != nil {
		i.ses.Close()
		i.ses = nil
	}
	if i.nodo != nil {
		i.nodo.Close()
		i.nodo = nil
	}
	if i.tor != nil {
		i.tor.Cerrar()
		i.tor = nil
	}
}

func (i *Invitado) Cerrar() {
	i.mu.Lock()
	defer i.mu.Unlock()
	i.limpiar()
}
