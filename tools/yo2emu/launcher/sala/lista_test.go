package sala

// Pruebas de la lista pública y del lanzador nuevo (ficha.go, lista.go, lanzador.go). Todo en este proceso: una DHT
// de 3 nodos en 127.0.0.1 (redLocal); nunca la DHT pública ni los puertos del juego en uso (el «run.py» de mentira
// escucha en 5730+20000 y los puertos locales del invitado van con +21000).

import (
	"bufio"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/libp2p/go-libp2p"
	dht "github.com/libp2p/go-libp2p-kad-dht"
	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/core/host"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"yo2online/internal/pruebas"
)

const (
	offPrueba      = 20000 // run.py de mentira en 127.0.0.1:25730
	offLocalPrueba = 21000 // puertos locales del invitado: 26730...
)

// ---------------------------------------------------------------- utilidades

// redLocal: n nodos DHT servidores en 127.0.0.1 conectados entre sí; devuelve sus direcciones (para OpcP2P.Arranque).
func redLocal(t *testing.T, n int) []peer.AddrInfo {
	t.Helper()
	var ais []peer.AddrInfo
	for i := 0; i < n; i++ {
		h, err := libp2p.New(libp2p.ListenAddrStrings("/ip4/127.0.0.1/tcp/0"))
		if err != nil {
			t.Fatal(err)
		}
		d, err := dht.New(h, dht.Mode(dht.ModeServer))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { d.Close(); h.Close() })
		for _, ai := range ais {
			if err := h.Connect(context.Background(), ai); err != nil {
				t.Fatal(err)
			}
		}
		ais = append(ais, peer.AddrInfo{ID: h.ID(), Addrs: h.Addrs()})
	}
	time.Sleep(300 * time.Millisecond) // identify: que se vean como servidores DHT
	return ais
}

func opcRed(arranque []peer.AddrInfo) OpcP2P {
	return OpcP2P{Arranque: arranque, SoloLoopback: true, MinPares: 1}
}

func lanzadorDePrueba(t *testing.T, red []peer.AddrInfo, r *registro) *Lanzador {
	t.Helper()
	l := NuevoLanzador(pruebas.TempDir(t), r.logf, io.Discard)
	l.Red = opcRed(red)
	l.EsperaFicha = 3 * time.Second
	l.EsperaBusqueda = 20 * time.Second
	t.Cleanup(l.Cerrar)
	return l
}

// esperar: hasta que f() sea cierto (o falla tras d).
func esperar(t *testing.T, d time.Duration, que string, f func() bool) {
	t.Helper()
	fin := time.Now().Add(d)
	for time.Now().Before(fin) {
		if f() {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("no pasa: %s", que)
}

// crearPublico: un servidor del lanzador, público, solo P2P, contra el run.py de mentira (offPrueba).
func crearPublico(t *testing.T, l *Lanzador, nombre string) {
	t.Helper()
	err := l.CrearServidor(context.Background(), OpcServidor{Nombre: nombre, Descripcion: "prueba", Max: 8, Publica: true,
		Reglas: ReglasVanilla, Idioma: "es", Offset: offPrueba, Modo: "p2p", SinMDNS: true,
		Estado: EstadoFijo{Err: errors.New("sin panel")}})
	if err != nil {
		t.Fatal(err)
	}
	esperar(t, 20*time.Second, "anuncio en la lista", func() bool { return l.EstadoServidor().ListadoOK })
}

// buscarTodo: una búsqueda entera; devuelve lo emitido.
func buscarTodo(t *testing.T, l *Lanzador) []ServidorVisto {
	t.Helper()
	var mu sync.Mutex
	var r []ServidorVisto
	if err := l.BuscarServidores(context.Background(), func(s ServidorVisto) {
		mu.Lock()
		r = append(r, s)
		mu.Unlock()
	}); err != nil {
		t.Fatal(err)
	}
	return r
}

func vivos(svs []ServidorVisto) map[string]ServidorVisto {
	m := map[string]ServidorVisto{}
	for _, s := range svs {
		if s.Estado == EstadoVivo {
			m[s.ID] = s
		}
	}
	return m
}

// juegoFalso: el «run.py» de mentira: a cada conexión le dice desde qué IP llega.
func juegoFalso(t *testing.T, puerto int) {
	t.Helper()
	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", puerto))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go func() {
				defer c.Close()
				ip, _, _ := net.SplitHostPort(c.RemoteAddr().String())
				fmt.Fprintf(c, "YO2MOCK %s\n", ip)
				io.Copy(io.Discard, c)
			}()
		}
	}()
}

// hostSuelto: un host libp2p en 127.0.0.1 con clave ed25519 (para hacer de servidor tramposo).
func hostSuelto(t *testing.T) host.Host {
	t.Helper()
	k, _, err := crypto.GenerateEd25519Key(nil)
	if err != nil {
		t.Fatal(err)
	}
	h, err := libp2p.New(libp2p.Identity(k), libp2p.ListenAddrStrings("/ip4/127.0.0.1/tcp/0"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { h.Close() })
	return h
}

func fichaEjemplo() Ficha {
	return Ficha{Nombre: "Duelos de los viernes", Descripcion: "Torneo semanal", Idioma: "es", Reglas: ReglasVanilla,
		Jugadores: 3, Max: 30, Duelos: 1, VersionJuego: VersionJuegoCliente, Salas: []string{"Mundial", "ES"}}
}

// ---------------------------------------------------------------- ficha: firma y límites

func TestFichaFirmaYVerificacion(t *testing.T) {
	s := anfitrionDePrueba(t)
	pub := s.Publica()
	nonce := []byte("0123456789abcdef")
	ff, err := FirmarFicha(pub, nonce, fichaEjemplo())
	if err != nil {
		t.Fatal(err)
	}
	ok, err := VerificarFicha(s.PeerID(), nonce, ff.Datos, ff.Firma)
	if err != nil {
		t.Fatal(err)
	}
	if ok.Ficha.Nombre != "Duelos de los viernes" || ok.Ficha.Jugadores != 3 {
		t.Fatalf("ficha %+v", ok.Ficha)
	}
	// de la ficha sale la MISMA sala pública que tiene el anfitrión (y no la privada)
	g, err := ok.Sala()
	if err != nil {
		t.Fatal(err)
	}
	if !g.EsPublica() || string(g.ClaveAuth()) != string(pub.ClaveAuth()) || g.Encuentro() != pub.Encuentro() {
		t.Fatal("la sala de la ficha no es la pública del anfitrión")
	}
	if string(g.ClaveAuth()) == string(s.ClaveAuth()) || g.Encuentro() == s.Encuentro() {
		t.Fatal("la sala pública coincide con la privada")
	}
	if strings.Contains(string(ff.Datos), s.Codigo()[4:20]) {
		t.Fatal("la ficha lleva el código privado")
	}
	// cualquier cambio rompe la firma
	mal := append([]byte{}, ff.Datos...)
	mal[len(mal)/2] ^= 1
	if _, err := VerificarFicha(s.PeerID(), nonce, mal, ff.Firma); err == nil {
		t.Fatal("ficha cambiada aceptada")
	}
	if _, err := VerificarFicha(s.PeerID(), []byte("otro-nonce-00000"), ff.Datos, ff.Firma); err == nil {
		t.Fatal("ficha con otro nonce aceptada (repetición)")
	}
	otro := anfitrionDePrueba(t)
	if _, err := VerificarFicha(otro.PeerID(), nonce, ff.Datos, ff.Firma); err == nil {
		t.Fatal("ficha de otro peer ID aceptada (suplantación)")
	}
	// una sala privada no firma fichas
	if _, err := FirmarFicha(s, nonce, fichaEjemplo()); err == nil {
		t.Fatal("se firmó una ficha con la sala privada")
	}
}

func TestFichaLimites(t *testing.T) {
	malas := map[string]func(*Ficha){
		"nombre largo":      func(f *Ficha) { f.Nombre = strings.Repeat("x", maxNombre+1) },
		"nombre vacío":      func(f *Ficha) { f.Nombre = "  " },
		"bidi":              func(f *Ficha) { f.Nombre = "abc‮dcba" },
		"control":           func(f *Ficha) { f.Descripcion = "a\x00b" },
		"salto de línea":    func(f *Ficha) { f.Descripcion = "a\nb" },
		"jugadores > max":   func(f *Ficha) { f.Jugadores = 31 },
		"max enorme":        func(f *Ficha) { f.Max = MaxJugadores + 1 },
		"reglas":            func(f *Ficha) { f.Reglas = "trampas" },
		"muchas salas":      func(f *Ficha) { f.Salas = make([]string, maxSalas+1) },
		"secreto":           func(f *Ficha) { f.Secreto = "AAAA" },
		"versión de ficha":  func(f *Ficha) { f.V = 2 },
		"descripción larga": func(f *Ficha) { f.Descripcion = strings.Repeat("ñ", maxDescripcion+1) },
	}
	s := anfitrionDePrueba(t).Publica()
	for nombre, cambio := range malas {
		f := fichaEjemplo()
		f.V, f.Secreto = 1, s.SecretoPublico()
		if err := f.Validar(); err != nil {
			t.Fatalf("la buena no valida: %v", err)
		}
		cambio(&f)
		if err := f.Validar(); err == nil {
			t.Errorf("%s: aceptada", nombre)
		}
	}
	// Normalizar deja dentro de los límites lo que pone el anfitrión
	f := fichaEjemplo()
	f.Nombre = "  Mi‮ servidor\n" + strings.Repeat("z", 100)
	f.Jugadores, f.Max, f.Reglas = 500, 20, "raro"
	ff, err := FirmarFicha(s, make([]byte, 16), f)
	if err != nil {
		t.Fatal(err)
	}
	if err := ff.Ficha.Validar(); err != nil {
		t.Fatalf("normalizada no valida: %v (%+v)", err, ff.Ficha)
	}
	if ff.Ficha.Jugadores != 20 || ff.Ficha.Reglas != ReglasVanilla || strings.ContainsRune(ff.Ficha.Nombre, 0x202e) {
		t.Fatalf("normalizar: %+v", ff.Ficha)
	}
}

// ---------------------------------------------------------------- suplantación por la red

func TestFichaSuplantadaRechazada(t *testing.T) {
	cli := hostSuelto(t)
	// A: servidor de verdad
	a := hostSuelto(t)
	kA, _ := a.Peerstore().PrivKey(a.ID()).Raw()
	sA := salaDeClave(t, kA)
	fs := &ServidorFichas{Sala: sA.Publica(), Ficha: fichaEjemplo}
	a.SetStreamHandler(ProtoFicha, fs.Atender)
	cli.Peerstore().AddAddrs(a.ID(), a.Addrs(), time.Hour)
	ffA, _, _, err := PedirFicha(context.Background(), cli, a.ID(), 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	// B: copia la ficha firmada de A tal cual (y otra con la firma de A de una petición anterior)
	b := hostSuelto(t)
	b.SetStreamHandler(ProtoFicha, func(st network.Stream) {
		defer st.Close()
		io.ReadFull(st, make([]byte, len(pedidoFicha)+lenNonceFicha))
		st.Write(binary.BigEndian.AppendUint16(nil, uint16(len(ffA.Datos))))
		st.Write(ffA.Datos)
		st.Write(ffA.Firma)
	})
	cli.Peerstore().AddAddrs(b.ID(), b.Addrs(), time.Hour)
	if _, _, _, err := PedirFicha(context.Background(), cli, b.ID(), 5*time.Second); err == nil ||
		!strings.Contains(err.Error(), "suplantación") {
		t.Fatalf("la ficha de A servida por B se aceptó: %v", err)
	}
	// B puede llamarse igual que A, pero es OTRO servidor (otro ID y otra huella): la lista los distingue
	kB, _ := b.Peerstore().PrivKey(b.ID()).Raw()
	sB := salaDeClave(t, kB)
	fsB := &ServidorFichas{Sala: sB.Publica(), Ficha: fichaEjemplo}
	b.SetStreamHandler(ProtoFicha, fsB.Atender)
	ffB, _, _, err := PedirFicha(context.Background(), cli, b.ID(), 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	gA, _ := ffA.Sala()
	gB, _ := ffB.Sala()
	if ffB.ID == ffA.ID || gA.Huella() == gB.Huella() {
		t.Fatal("dos servidores con el mismo nombre no se distinguen")
	}
}

// salaDeClave: la Sala de un anfitrión a partir de la clave libp2p (Raw de ed25519 = privada de 64 bytes).
func salaDeClave(t *testing.T, raw []byte) *Sala {
	t.Helper()
	if len(raw) != 64 {
		t.Fatalf("clave de %d bytes", len(raw))
	}
	priv := append([]byte{}, raw...)
	return &Sala{pub: append([]byte{}, priv[32:]...), priv: priv}
}

// ---------------------------------------------------------------- servidores tramposos en la lista

func TestFichaGrandeOLentaIgnorada(t *testing.T) {
	red := redLocal(t, 3)
	r := &registro{}
	// uno bueno
	bueno := lanzadorDePrueba(t, red, r)
	crearPublico(t, bueno, "El bueno")
	// tramposos que se anuncian en la lista
	tramposo := func(h func(network.Stream)) {
		n, err := NuevoNodo(OpcP2P{Arranque: red, SoloLoopback: true, MinPares: 1, Logf: r.logf})
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(n.Close)
		if h != nil {
			n.H.SetStreamHandler(ProtoFicha, h)
		}
		if err := n.EsperarDHT(context.Background()); err != nil {
			t.Fatal(err)
		}
		if err := n.DHT.Provide(context.Background(), CIDLista(), true); err != nil {
			t.Fatal(err)
		}
	}
	tramposo(func(st network.Stream) { // enorme
		io.ReadFull(st, make([]byte, 19))
		st.Write([]byte{0xff, 0xff})
		st.Write(make([]byte, 1<<16))
		st.Close()
	})
	tramposo(func(st network.Stream) { // lenta: no contesta nunca
		time.Sleep(30 * time.Second)
		st.Reset()
	})
	tramposo(func(st network.Stream) { // a medias: dice 100 bytes y manda 10 y se queda
		io.ReadFull(st, make([]byte, 19))
		st.Write([]byte{0, 100})
		st.Write(make([]byte, 10))
		time.Sleep(30 * time.Second)
		st.Reset()
	})
	tramposo(func(st network.Stream) { // basura con buena longitud
		io.ReadFull(st, make([]byte, 19))
		st.Write([]byte{0, 20})
		st.Write(make([]byte, 20+64))
		st.Close()
	})
	tramposo(nil) // se anuncia pero no habla el protocolo

	l := lanzadorDePrueba(t, red, &registro{})
	l.EsperaFicha = 1500 * time.Millisecond
	t0 := time.Now()
	vs := buscarTodo(t, l)
	dur := time.Since(t0)
	v := vivos(vs)
	if len(v) != 1 {
		t.Fatalf("esperaba solo el bueno, hay %d: %+v", len(v), vs)
	}
	for _, s := range v {
		if s.Ficha.Nombre != "El bueno" || s.PingMs < 1 {
			t.Fatalf("ficha %+v", s)
		}
	}
	if dur > 10*time.Second {
		t.Fatalf("la búsqueda tarda %v: los tramposos la frenan", dur)
	}
	t.Logf("búsqueda con 5 tramposos: %v", dur.Round(time.Millisecond))
}

// límite por par del anfitrión: el mismo par pidiendo fichas sin parar se queda sin ellas
func TestFichaLimitePorPar(t *testing.T) {
	cli := hostSuelto(t)
	a := hostSuelto(t)
	kA, _ := a.Peerstore().PrivKey(a.ID()).Raw()
	fs := &ServidorFichas{Sala: salaDeClave(t, kA).Publica(), Ficha: fichaEjemplo}
	a.SetStreamHandler(ProtoFicha, fs.Atender)
	cli.Peerstore().AddAddrs(a.ID(), a.Addrs(), time.Hour)
	ok, cortadas := 0, 0
	for i := 0; i < 10; i++ {
		if _, _, _, err := PedirFicha(context.Background(), cli, a.ID(), 2*time.Second); err == nil {
			ok++
		} else {
			cortadas++
		}
	}
	if ok != int(rafagaPar) || cortadas != 10-int(rafagaPar) {
		t.Fatalf("ok=%d cortadas=%d (ráfaga %v)", ok, cortadas, rafagaPar)
	}
}

// ---------------------------------------------------------------- privados: nunca en la lista

func TestPrivadoNuncaEnLaLista(t *testing.T) {
	red := redLocal(t, 3)
	r := &registro{}
	priv := lanzadorDePrueba(t, red, r)
	if err := priv.CrearServidor(context.Background(), OpcServidor{Nombre: "Secreto", Publica: false, Offset: offPrueba,
		Modo: "p2p", SinMDNS: true, Estado: EstadoFijo{Err: errors.New("x")}}); err != nil {
		t.Fatal(err)
	}
	esperar(t, 20*time.Second, "anuncio de la sala privada", func() bool { return priv.EstadoServidor().P2POK })
	priv.mu.Lock()
	sv := priv.srv
	priv.mu.Unlock()
	id := sv.sala.PeerID()

	l := lanzadorDePrueba(t, red, &registro{})
	if v := vivos(buscarTodo(t, l)); len(v) != 0 {
		t.Fatalf("un servidor privado sale en la lista: %+v", v)
	}
	n, _ := l.nodoLista()
	ctx, c := context.WithTimeout(context.Background(), 5*time.Second)
	defer c()
	for ai := range n.DHT.FindProvidersAsync(ctx, CIDLista(), 0) {
		if ai.ID == id {
			t.Fatal("el servidor privado anuncia el punto de encuentro de la lista")
		}
	}
	// su sala privada sí está (comprobación de que la prueba ve los anuncios)
	visto := false
	for ai := range n.DHT.FindProvidersAsync(ctx, sv.sala.Encuentro(), 1) {
		visto = visto || ai.ID == id
	}
	if !visto {
		t.Fatal("no se ve ni la sala privada: la prueba no vale")
	}
	// ni contesta fichas (aunque alguien conozca su peer ID)
	if _, _, _, err := PedirFicha(context.Background(), n.H, id, 3*time.Second); err == nil {
		t.Fatal("el servidor privado da su ficha")
	}
	// y aunque alguien supiera el secreto público (se deriva de su clave), un privado rechaza el saludo público
	g, _ := SalaPublica(sv.sala.pub, sv.sala.Publica().secreto[:])
	c1, c2 := net.Pipe()
	go sv.a.Servir(c1, "prueba")
	if ses, err := Entrar(c2, g, claveNueva(t), io.Discard); err == nil {
		if _, err := AbrirPuerto(ses, 0); err == nil {
			ses.Close()
		}
	}
	r.contiene(t, false, "entrada PÚBLICA (sin código) en una sala privada: rechazado")
}

// ---------------------------------------------------------------- saludo público (en memoria)

func TestSaludoPublico(t *testing.T) {
	a, gPriv, r := nuevoAnfitrion(t)
	a.Publica = a.Sala.Publica()
	gPub, err := SalaPublica(a.Sala.pub, a.Publica.secreto[:])
	if err != nil {
		t.Fatal(err)
	}
	k := claveNueva(t)
	eco(t, conectar(t, a, gPub, k))  // sin código, con el secreto de la ficha
	eco(t, conectar(t, a, gPriv, k)) // el código privado sigue valiendo en un servidor público
	// secreto público equivocado: fuera
	mal, _ := SalaPublica(a.Sala.pub, make([]byte, lenSecreto))
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	if _, err := Entrar(c2, mal, claveNueva(t), io.Discard); err == nil {
		t.Fatal("entró con un secreto público falso")
	}
	// un impostor con TODO lo público de la ficha (pública del anfitrión + secreto público) pero sin su clave privada:
	// pasa el HMAC, pero el invitado lo pilla por la firma
	imp := anfitrionDePrueba(t)
	falso := &Anfitrion{Sala: imp, Publica: &Sala{pub: a.Sala.pub, priv: imp.priv, secreto: a.Publica.secreto, publica: true},
		Logf: r.logf, LogW: io.Discard, Aviso: r.aviso}
	c3, c4 := net.Pipe()
	go falso.Servir(c3, "prueba")
	if _, err := Entrar(c4, gPub, claveNueva(t), io.Discard); !errors.Is(err, ErrImpostor) {
		t.Fatalf("impostor con el secreto público no detectado: %v", err)
	}
}

func TestMaxInvitados(t *testing.T) {
	a, g, r := nuevoAnfitrion(t)
	a.MaxInvitados = 1
	k1 := claveNueva(t)
	eco(t, conectar(t, a, g, k1))
	ipsDe(t, a, k1, 1)
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	ses, err := Entrar(c2, g, claveNueva(t), io.Discard)
	if err == nil {
		if st, err := AbrirPuerto(ses, 0); err == nil {
			st.SetReadDeadline(time.Now().Add(3 * time.Second))
			st.Write([]byte("hola"))
			if _, err := st.Read(make([]byte, 4)); err == nil {
				t.Fatal("entró un segundo invitado con el servidor lleno")
			}
		}
	}
	r.contiene(t, false, "servidor lleno")
	eco(t, conectar(t, a, g, k1)) // el que ya estaba puede abrir otro túnel (reconexión)
	if a.Invitados() != 1 {
		t.Fatalf("invitados = %d", a.Invitados())
	}
}

// ---------------------------------------------------------------- de punta a punta

func TestEntrarPublicoDePuntaAPunta(t *testing.T) {
	juegoFalso(t, 5730+offPrueba)
	red := redLocal(t, 3)
	rA := &registro{}
	anf := lanzadorDePrueba(t, red, rA)
	crearPublico(t, anf, "PRUEBA local")

	rB := &registro{}
	inv := lanzadorDePrueba(t, red, rB)
	t0 := time.Now()
	v := vivos(buscarTodo(t, inv))
	if len(v) != 1 {
		t.Fatalf("servidores vivos: %d", len(v))
	}
	var sv ServidorVisto
	for _, s := range v {
		sv = s
	}
	t.Logf("encontrado en %v (ping %d ms)", time.Since(t0).Round(time.Millisecond), sv.PingMs)
	if sv.Ficha.Nombre != "PRUEBA local" || sv.Ficha.Jugadores != 0 || sv.Ficha.Max != 8 || !sv.Compatible ||
		sv.Huella != anf.EstadoServidor().Huella {
		t.Fatalf("ficha %+v huella %s", sv, anf.EstadoServidor().Huella)
	}

	var pasos []string
	con, err := inv.Entrar(context.Background(), Destino{Servidor: &sv}, OpcEntrar{Modo: "p2p", SinMDNS: true,
		OffsetLocal: offLocalPrueba}, func(e EventoEntrar) { pasos = append(pasos, e.Paso.String()+":"+e.Estado) })
	if err != nil {
		t.Fatalf("Entrar: %v (pasos %v)", err, pasos)
	}
	defer con.Cerrar()
	want := []string{"Buscando al anfitrión:va", "Buscando al anfitrión:ya", "Abriendo túnel cifrado:va",
		"Abriendo túnel cifrado:ya", "Comprobando versión del juego:va", "Comprobando versión del juego:ya",
		"Abriendo Yu-Gi-Oh! Online 2:va", "Abriendo Yu-Gi-Oh! Online 2:ya"}
	if strings.Join(pasos, "|") != strings.Join(want, "|") {
		t.Fatalf("pasos %v", pasos)
	}
	if !con.Publica || !strings.HasPrefix(con.Via, "P2P directo") {
		t.Fatalf("conexión %+v", con)
	}
	// el juego habla con el puerto local y llega al run.py del anfitrión, que lo ve como 127.0.0.20 (T1)
	c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", 5730+offLocalPrueba), 3*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.SetReadDeadline(time.Now().Add(5 * time.Second))
	l, err := bufio.NewReader(c).ReadString('\n')
	if err != nil || strings.TrimSpace(l) != "YO2MOCK 127.0.0.20" {
		t.Fatalf("por el túnel llega %q (%v)", l, err)
	}
	// el anfitrión cuenta al invitado (sin estado de run.py: por túneles) y lo dice en su ficha
	e := anf.EstadoServidor()
	if e.Invitados != 1 || e.Jugadores != 1 || e.FuenteJug != "túneles" || !e.Publica || !e.P2POK {
		t.Fatalf("estado del anfitrión %+v", e)
	}
	v2 := vivos(buscarTodo(t, inv))
	if s := v2[sv.ID]; s.Ficha.Jugadores != 1 {
		t.Fatalf("la ficha no cuenta al invitado: %+v", s.Ficha)
	}

	// «Entrar con código» al mismo servidor (privado): también vale
	con2, err := inv.Entrar(context.Background(), Destino{Codigo: anf.CodigoPrivado()}, OpcEntrar{Modo: "p2p", SinMDNS: true,
		SinTuneles: true}, nil)
	if err != nil {
		t.Fatalf("con código: %v", err)
	}
	if con2.Publica {
		t.Fatal("con código debería ser privada")
	}
	con2.Cerrar()
	for _, x := range append(rA.log, rB.log...) {
		if strings.Contains(x, anf.CodigoPrivado()) {
			t.Fatal("el código privado sale en el log")
		}
	}

	// caché: otro lanzador con los mismos datos y SIN red ve la lista de antes al momento, marcada como caché
	sinRed := NuevoLanzador(inv.Datos, nil, nil)
	sinRed.Red = OpcP2P{Arranque: []peer.AddrInfo{}, SoloLoopback: true, MinPares: 1}
	defer sinRed.Cerrar()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	var cache []ServidorVisto
	t1 := time.Now()
	var primero time.Duration
	sinRed.BuscarServidores(ctx, func(s ServidorVisto) {
		if primero == 0 {
			primero = time.Since(t1)
		}
		cache = append(cache, s)
	})
	if len(cache) != 1 || cache[0].Estado != EstadoCache || cache[0].ID != sv.ID || cache[0].Ficha.Nombre != "PRUEBA local" {
		t.Fatalf("caché: %+v", cache)
	}
	if primero > 500*time.Millisecond {
		t.Fatalf("la caché tarda %v en salir", primero)
	}
	// favoritos por peer ID
	if err := inv.MarcarFavorito(sv.ID, true); err != nil {
		t.Fatal(err)
	}
	if f := inv.Favoritos(); len(f) != 1 || f[0] != sv.ID {
		t.Fatalf("favoritos %v", f)
	}
}

// ---------------------------------------------------------------- estado de run.py (propuesta /publico)

func TestEstadoPanel(t *testing.T) {
	resp := `{"jugadores":7,"duelos":2,"perfil":"vanilla","version_juego":"080912","salas":["Mundial","ES"]}`
	code := 200
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/publico" || r.Header.Get("X-Admin-Token") != "" {
			w.WriteHeader(400)
			return
		}
		w.WriteHeader(code)
		io.WriteString(w, resp)
	}))
	defer srv.Close()
	e := &EstadoPanel{URL: srv.URL + "/publico"}
	d, err := e.Leer(context.Background())
	if err != nil || d.Jugadores != 7 || d.Duelos != 2 || len(d.Salas) != 2 {
		t.Fatalf("%+v %v", d, err)
	}
	resp = `{"jugadores":-5}`
	if _, err := e.Leer(context.Background()); err == nil {
		t.Fatal("números fuera de rango aceptados")
	}
	resp = `{"jugadores":1,"x":"` + strings.Repeat("a", 20000) + `"}`
	if _, err := e.Leer(context.Background()); err == nil {
		t.Fatal("respuesta enorme aceptada")
	}
	code, resp = 404, `{"error":"no existe"}`
	if _, err := e.Leer(context.Background()); err == nil {
		t.Fatal("404 aceptado")
	}
	if p := NuevoEstadoPanel(3).URL; p != "http://127.0.0.1:8793/publico" {
		t.Fatalf("url %s", p)
	}
	if p := NuevoEstadoPanel(3).Antes; len(p) != 1 || p[0] != "http://127.0.0.1:8794/publico" {
		t.Fatalf("antes %v", p)
	}
}

// 11-10: se prueba primero la API de gestión (8791 + offset) y, si no contesta, el panel (8790 + offset).
func TestEstadoPanelPrefiereLaApiDeGestion(t *testing.T) {
	respuesta := func(n int) *httptest.Server {
		return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			io.WriteString(w, `{"jugadores":`+strconv.Itoa(n)+`,"duelos":0}`)
		}))
	}
	gest, panel := respuesta(3), respuesta(9)
	defer gest.Close()
	defer panel.Close()
	e := &EstadoPanel{URL: panel.URL + "/publico", Antes: []string{gest.URL + "/publico"}}
	if d, err := e.Leer(context.Background()); err != nil || d.Jugadores != 3 {
		t.Fatalf("con las dos, manda la de gestión: %+v %v", d, err)
	}
	gest.Close() // run.py sin la API de gestión (o parada): el panel
	if d, err := e.Leer(context.Background()); err != nil || d.Jugadores != 9 {
		t.Fatalf("sin la de gestión, el panel: %+v %v", d, err)
	}
	panel.Close()
	if _, err := e.Leer(context.Background()); err == nil {
		t.Fatal("sin ninguna de las dos debería fallar")
	}
}

// ---------------------------------------------------------------- caché: ping, favoritos, servidor propio (decisión 11-10)

// guardaFicha: escribe en el caché de l una ficha firmada de un anfitrión nuevo, vista hace `hace`, con ping `ping`.
func guardaFicha(t *testing.T, l *Lanzador, hace time.Duration, ping int) string {
	t.Helper()
	s := anfitrionDePrueba(t)
	nonce := []byte("0123456789abcdef")
	ff, err := FirmarFicha(s.Publica(), nonce, fichaEjemplo())
	if err != nil {
		t.Fatal(err)
	}
	sv := ServidorVisto{ID: s.PeerID().String(), Ficha: ff.Ficha, PingMs: ping, Visto: time.Now().Add(-hace), firmada: ff}
	l.guardarCache(append(l.leerCache(), sv))
	return sv.ID
}

func TestCacheNoTrasladaElPingDeOtraSesion(t *testing.T) {
	l := NuevoLanzador(pruebas.TempDir(t), nil, nil)
	id := guardaFicha(t, l, time.Minute, 42)
	c := l.leerCache()
	if len(c) != 1 || c[0].ID != id || c[0].Estado != EstadoCache {
		t.Fatalf("caché: %+v", c)
	}
	if c[0].PingMs != -1 {
		t.Fatalf("el ping guardado (42) se cargó como si fuera de ahora: %d", c[0].PingMs)
	}
}

func TestCacheCaducaPeroNoLosFavoritos(t *testing.T) {
	// una semana de caché: de hace 3 semanas, sin ser favorito caduca; siéndolo, no
	for _, esFav := range []bool{false, true} {
		l3 := NuevoLanzador(pruebas.TempDir(t), nil, nil)
		s := anfitrionDePrueba(t)
		ff, _ := FirmarFicha(s.Publica(), []byte("0123456789abcdef"), fichaEjemplo())
		id := s.PeerID().String()
		if esFav {
			if err := l3.MarcarFavorito(id, true); err != nil {
				t.Fatal(err)
			}
		}
		l3.guardarCache([]ServidorVisto{{ID: id, Ficha: ff.Ficha, PingMs: 5, Visto: time.Now().Add(-21 * 24 * time.Hour), firmada: ff}})
		got := l3.leerCache()
		if esFav && (len(got) != 1 || got[0].ID != id || !got[0].Favorito) {
			t.Fatalf("un favorito de hace 3 semanas debía seguir en el caché: %+v", got)
		}
		if !esFav && len(got) != 0 {
			t.Fatalf("uno que no es favorito de hace 3 semanas debía caducar: %+v", got)
		}
	}
}

func TestIDAnfitrionYPropio(t *testing.T) {
	datos := pruebas.TempDir(t)
	l := NuevoLanzador(datos, nil, nil)
	if id := l.IDAnfitrion(); id != "" {
		t.Fatalf("sin identidad debía ser vacío: %q", id)
	}
	if l.ServidorPublicoEnMarcha() {
		t.Fatal("no hay servidor en marcha")
	}
	// la identidad de anfitrión de este equipo: <datos>\anfitrion\identidad.key
	dir := filepath.Join(datos, "anfitrion")
	os.MkdirAll(dir, 0o700)
	s, _, err := CargarAnfitrion(dir, false, false)
	if err != nil {
		t.Fatal(err)
	}
	if got := l.IDAnfitrion(); got != s.PeerID().String() {
		t.Fatalf("IDAnfitrion %q != peer ID de la identidad %q", got, s.PeerID())
	}
	// una ficha de ese mismo anfitrión, vista con el servidor PARADO, sale marcada como propia
	ff, err := FirmarFicha(s.Publica(), []byte("0123456789abcdef"), fichaEjemplo())
	if err != nil {
		t.Fatal(err)
	}
	sv := l.visto(ff, nil, 0, false, EstadoCache, time.Now())
	if !sv.Propio {
		t.Fatal("la ficha de nuestra propia identidad debía marcarse Propio aunque el servidor esté parado")
	}
	// y la de otro anfitrión, no
	o := anfitrionDePrueba(t)
	ff2, _ := FirmarFicha(o.Publica(), []byte("0123456789abcdef"), fichaEjemplo())
	if l.visto(ff2, nil, 0, false, EstadoVivo, time.Now()).Propio {
		t.Fatal("la ficha de otro anfitrión no es nuestra")
	}
}

// ---------------------------------------------------------------- servidor que ya no es público

// Un anfitrión que contesta pero ya no atiende fichas (pasó a privado) se distingue de uno que no contesta.
func TestPedirFichaDeUnoQueYaNoEsPublico(t *testing.T) {
	mk := func() host.Host {
		h, err := libp2p.New(libp2p.ListenAddrStrings("/ip4/127.0.0.1/tcp/0"))
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { h.Close() })
		return h
	}
	anf, inv := mk(), mk() // anf no registra ProtoFicha: es lo que hace un servidor privado
	inv.Peerstore().AddAddrs(anf.ID(), anf.Addrs(), time.Minute)
	_, _, _, err := PedirFicha(context.Background(), inv, anf.ID(), 3*time.Second)
	if !errors.Is(err, ErrNoPublico) {
		t.Fatalf("debía ser ErrNoPublico: %v", err)
	}
}
