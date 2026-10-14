package sala

// Pruebas de NET (12-10): puerto fijo, «Join by address» sin DHT ni relays, lista de nodos firmada y mDNS en la red local.
// Todo en 127.0.0.1 (salvo TestLANmDNSSinInternet, manual). Puertos: fijos 5941-5960, run.py de mentira en 5730+22000.

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/core/peer"
	"yo2online/internal/pruebas"
)

const offDir = 22000 // run.py de mentira en 127.0.0.1:27730

// sinDHT: red sin ningún nodo de arranque (la DHT no existe) en 127.0.0.1.
func sinDHT() OpcP2P { return OpcP2P{Arranque: []peer.AddrInfo{}, SoloLoopback: true, MinPares: 1} }

func lanzadorSinDHT(t *testing.T, r *registro) *Lanzador {
	t.Helper()
	l := NuevoLanzador(pruebas.TempDir(t), r.logf, io.Discard)
	l.Red = sinDHT()
	l.EsperaFicha = 3 * time.Second
	t.Cleanup(l.Cerrar)
	return l
}

// servidorEnPuerto: el servidor propio de l con puerto fijo; clave = contraseña (modo Password) o "" (publica decide).
func servidorEnPuerto(t *testing.T, l *Lanzador, puerto int, publica bool, clave string) {
	t.Helper()
	l.PonerPuertoFijo(puerto)
	o := OpcServidor{Nombre: "Por direccion", Max: 8, Publica: publica, Offset: offDir, Modo: "p2p", SinMDNS: true,
		Estado: EstadoFijo{Err: errors.New("sin panel")}}
	if clave != "" {
		os.MkdirAll(filepath.Join(l.Datos, "anfitrion"), 0o700)
		s, _, err := CargarAnfitrion(filepath.Join(l.Datos, "anfitrion"), false, false)
		if err != nil {
			t.Fatal(err)
		}
		if o.Clave, err = NuevaClaveServidor(clave, s.Pub()); err != nil {
			t.Fatal(err)
		}
	}
	if err := l.CrearServidor(context.Background(), o); err != nil {
		t.Fatal(err)
	}
}

func entrarDir(l *Lanzador, d Destino) (*Conexion, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	return l.Entrar(ctx, d, OpcEntrar{SinMDNS: true, SinTuneles: true, EsperaP2P: 10 * time.Second}, nil)
}

func TestPuertoFijoYOcupado(t *testing.T) {
	// libre: TCP y UDP en ese puerto
	r := &registro{}
	n, err := NuevoNodo(OpcP2P{Arranque: []peer.AddrInfo{}, SoloLoopback: true, Puerto: 5941, Anfitr: true, Logf: r.logf})
	if err != nil {
		t.Fatal(err)
	}
	a := n.Acceso()
	n.Close()
	if !a.Fijo || a.Ocupado || a.Puerto != 5941 || a.PuertoUDP != 5941 || a.Pedido != 5941 {
		t.Fatalf("puerto fijo libre: %+v", a)
	}
	// ocupado (TCP): mensaje claro y vuelta a uno aleatorio
	ln, err := net.Listen("tcp4", "127.0.0.1:5942")
	if err != nil {
		t.Skipf("el puerto 5942 de la prueba está ocupado: %v", err)
	}
	defer ln.Close()
	r = &registro{}
	n, err = NuevoNodo(OpcP2P{Arranque: []peer.AddrInfo{}, SoloLoopback: true, Puerto: 5942, Anfitr: true, Logf: r.logf})
	if err != nil {
		t.Fatalf("con el puerto fijo ocupado no arranca: %v", err)
	}
	a = n.Acceso()
	n.Close()
	if a.Fijo || !a.Ocupado || a.Puerto == 5942 || a.Puerto == 0 || a.Pedido != 5942 {
		t.Fatalf("puerto fijo ocupado: %+v", a)
	}
	r.contiene(t, false, "El puerto fijo 5942 está ocupado")
	// ocupado solo en UDP: también
	pc, err := net.ListenPacket("udp4", "127.0.0.1:5943")
	if err != nil {
		t.Skipf("el puerto UDP 5943 de la prueba está ocupado: %v", err)
	}
	defer pc.Close()
	if err := PuertoLibre(5943, true); err == nil || !strings.Contains(err.Error(), "UDP") {
		t.Fatalf("UDP ocupado no se detecta: %v", err)
	}
	for _, p := range []int{80, 5730, 5745, 5799, 5850, 8730, 8790, 8791, 70000} {
		if ValidarPuertoFijo(p) == nil {
			t.Fatalf("el puerto %d no debería valer como fijo", p)
		}
	}
	if err := ValidarPuertoFijo(PuertoFijoPorDefecto); err != nil {
		t.Fatal(err)
	}
}

func TestParseDireccion(t *testing.T) {
	bien := map[string]string{
		"1.2.3.4:47300":            "1.2.3.4:47300",
		" 1.2.3.4 ":                "1.2.3.4:47300",
		"Casa.Example.org:5000":    "casa.example.org:5000",
		"yo2://casa.example.org":   "casa.example.org:47300",
		"[::1]:6000":               "[::1]:6000",
		"::1":                      "[::1]:47300",
		"host-pc:47300":            "host-pc:47300",
		"/ip4/1.2.3.4/tcp/47300":   "/ip4/1.2.3.4/tcp/47300",
		"/dns/x.org/udp/9/quic-v1": "/dns/x.org/udp/9/quic-v1",
	}
	for in, want := range bien {
		d, err := ParseDireccion(in)
		if err != nil || d.Texto != want {
			t.Fatalf("%q: %v %v", in, d, err)
		}
	}
	d, _ := ParseDireccion("1.2.3.4:47300")
	if len(d.Info.Addrs) != 2 || d.Info.Addrs[0].String() != "/ip4/1.2.3.4/tcp/47300" || d.Info.Addrs[1].String() != "/ip4/1.2.3.4/udp/47300/quic-v1" {
		t.Fatalf("multiaddrs: %v", d.Info.Addrs)
	}
	d, _ = ParseDireccion("casa.example.org:5000")
	if d.Info.Addrs[0].String() != "/dns/casa.example.org/tcp/5000" {
		t.Fatalf("dns: %v", d.Info.Addrs)
	}
	for _, mal := range []string{"", "1.2.3.4:0", "1.2.3.4:70000", "a b:1", "ex_ample.org:1", "0.0.0.0:5", "224.0.0.1:5",
		"/ip4/1.2.3.4/tcp/1/p2p/12D3KooWDpJ7As7BWAwRMfu1VU2WCqNjvq387JEYKDBj4kx6nXTN/p2p-circuit", "/ip4/1.2.3.4", "x:y:z",
		"-mal.org:5", strings.Repeat("a", 301)} {
		if _, err := ParseDireccion(mal); err == nil {
			t.Fatalf("%q debería fallar", mal)
		}
	}
}

// TestEntrarPorDireccionSinDHT: dos núcleos en 127.0.0.1 SIN ningún nodo de arranque: se entra por IP:puerto fijo, con
// contraseña (mala, buena, sin ella), con el código (que salta la contraseña), con un código ajeno y donde no hay nadie.
func TestEntrarPorDireccionSinDHT(t *testing.T) {
	kdfRapido(t)
	juegoFalso(t, 5730+offDir)
	hr, ir := &registro{}, &registro{}
	h := lanzadorSinDHT(t, hr)
	servidorEnPuerto(t, h, 5944, true, "clave-buena")
	acc := h.EstadoServidor().Acceso
	if acc == nil || !acc.Fijo || acc.Puerto != 5944 || acc.PuertoUDP != 5944 {
		t.Fatalf("acceso del anfitrión: %+v", acc)
	}
	codigo := h.CodigoPrivado()
	inv := lanzadorSinDHT(t, ir)
	dir := "127.0.0.1:5944"

	if _, err := entrarDir(inv, Destino{Direccion: dir}); !errors.Is(err, ErrPideContrasena) {
		t.Fatalf("sin contraseña: %v", err)
	}
	if _, err := entrarDir(inv, Destino{Direccion: dir, Credencial: NuevaCredencial("mala")}); !errors.Is(err, ErrContrasena) {
		t.Fatalf("contraseña mala: %v", err)
	}
	t0 := time.Now()
	c, err := entrarDir(inv, Destino{Direccion: dir, Credencial: NuevaCredencial("clave-buena")})
	if err != nil {
		t.Fatalf("contraseña buena: %v", err)
	}
	if !c.Publica || !strings.Contains(c.Via, "red local") || c.Huella != h.EstadoServidor().Huella {
		t.Fatalf("conexión: %+v", c)
	}
	t.Logf("por dirección con contraseña en %v (%s)", time.Since(t0).Round(time.Millisecond), c.Via)
	c.Cerrar()

	// el código entra sin contraseña (como siempre), también con la multiaddr completa
	c, err = entrarDir(inv, Destino{Direccion: dir, Codigo: codigo})
	if err != nil || c.Publica {
		t.Fatalf("con código: %v %+v", err, c)
	}
	c.Cerrar()
	peerID := h.EstadoServidor().PeerID
	c, err = entrarDir(inv, Destino{Direccion: "/ip4/127.0.0.1/tcp/5944/p2p/" + peerID, Codigo: codigo})
	if err != nil {
		t.Fatalf("multiaddr con /p2p/: %v", err)
	}
	c.Cerrar()

	// el código sigue exigiéndose: el mismo anfitrión con otro secreto no entra
	s, _ := Parse(codigo)
	otro := *s
	otro.secreto[0] ^= 1
	if _, err := entrarDir(inv, Destino{Direccion: dir, Codigo: otro.Codigo()}); err == nil {
		t.Fatal("entró con un código de secreto falso")
	}
	// código de OTRO servidor en esta dirección: ni se intenta el saludo
	ajena, _, _ := CargarAnfitrion(pruebas.TempDir(t), false, false)
	if _, err := entrarDir(inv, Destino{Direccion: dir, Codigo: ajena.Codigo()}); !errors.Is(err, ErrDirOtro) {
		t.Fatalf("código ajeno: %v", err)
	}
	// /p2p/ que no es el del código
	if _, err := entrarDir(inv, Destino{Direccion: "/ip4/127.0.0.1/tcp/5944/p2p/" + ajena.PeerID().String(), Codigo: codigo}); !errors.Is(err, ErrDirOtro) {
		t.Fatalf("/p2p/ ajeno: %v", err)
	}
	// nadie en esa dirección: fallo claro y rápido (nada de 30 s de DHT ni 8 min de Tor). Con otro lanzador: este ya
	// tiene conexión directa con el anfitrión y libp2p la reutilizaría (es el mismo servidor, comprobado por su clave)
	inv2 := lanzadorSinDHT(t, &registro{})
	t0 = time.Now()
	if _, err := entrarDir(inv2, Destino{Direccion: "127.0.0.1:5945", Codigo: codigo}); !errors.Is(err, ErrDirSinRespuesta) {
		t.Fatalf("nadie en la dirección: %v", err)
	} else if time.Since(t0) > 15*time.Second {
		t.Fatalf("tardó %v en fallar", time.Since(t0))
	}
	if _, err := entrarDir(inv2, Destino{Direccion: "127.0.0.1:5945"}); !errors.Is(err, ErrDirSinRespuesta) {
		t.Fatalf("nadie en la dirección (sin código): %v", err)
	}
	// ni DHT ni relays: las tablas siguen vacías
	if n := inv.nodo.DHT.RoutingTable().Size() + inv2.nodo.DHT.RoutingTable().Size(); n != 0 {
		t.Fatalf("el invitado tiene %d pares de DHT", n)
	}
}

func TestEntrarPorDireccionOcultoYPublico(t *testing.T) {
	juegoFalso(t, 5730+offDir)
	oculto := lanzadorSinDHT(t, &registro{})
	servidorEnPuerto(t, oculto, 5946, false, "")
	publico := lanzadorSinDHT(t, &registro{})
	servidorEnPuerto(t, publico, 5947, true, "")
	inv := lanzadorSinDHT(t, &registro{})
	if _, err := entrarDir(inv, Destino{Direccion: "127.0.0.1:5946"}); !errors.Is(err, ErrDirOculta) {
		t.Fatalf("Hidden sin código: %v", err)
	}
	c, err := entrarDir(inv, Destino{Direccion: "127.0.0.1:5946", Codigo: oculto.CodigoPrivado()})
	if err != nil {
		t.Fatalf("Hidden con código: %v", err)
	}
	c.Cerrar()
	c, err = entrarDir(inv, Destino{Direccion: "localhost:5947"}) // nombre DNS (se resuelve por libp2p)
	if err != nil || !c.Publica {
		t.Fatalf("Public sin nada: %v", err)
	}
	c.Cerrar()
}

// TestPuertoFijoOcupadoServidor: el puerto fijo ocupado no deja sin servidor: escucha en otro y se entra por ese.
func TestPuertoFijoOcupadoServidor(t *testing.T) {
	juegoFalso(t, 5730+offDir)
	ln, err := net.Listen("tcp4", "127.0.0.1:5948")
	if err != nil {
		t.Skipf("el puerto 5948 de la prueba está ocupado: %v", err)
	}
	defer ln.Close()
	r := &registro{}
	h := lanzadorSinDHT(t, r)
	servidorEnPuerto(t, h, 5948, false, "")
	a := h.EstadoServidor().Acceso
	if a == nil || a.Fijo || !a.Ocupado || a.Puerto == 5948 || a.Puerto == 0 {
		t.Fatalf("acceso: %+v", a)
	}
	r.contiene(t, false, "El puerto fijo 5948 está ocupado")
	c, err := entrarDir(lanzadorSinDHT(t, &registro{}), Destino{Direccion: "127.0.0.1:" + strconv.Itoa(a.Puerto), Codigo: h.CodigoPrivado()})
	if err != nil {
		t.Fatalf("por el puerto aleatorio: %v", err)
	}
	c.Cerrar()
}

// ---------------------------------------------------------------- lista de nodos firmada

func nodoDePrueba(t *testing.T, ip string) string {
	t.Helper()
	_, pub, err := crypto.GenerateEd25519Key(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	id, _ := peer.IDFromPublicKey(pub)
	return "/ip4/" + ip + "/tcp/4001/p2p/" + id.String()
}

func TestListaRedFirmada(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	t.Cleanup(UsarClaveEditorDePrueba(pub)) // nunca la clave del editor de verdad
	a1, a2, rl := nodoDePrueba(t, "198.51.100.1"), nodoDePrueba(t, "198.51.100.2"), nodoDePrueba(t, "198.51.100.3")

	l, err := FirmarListaRed(priv, ListaRed{Version: 20261012100000, Arranque: []string{a1, a2}, Relays: []string{rl}})
	if err != nil {
		t.Fatal(err)
	}
	if err := l.Verificar(); err != nil {
		t.Fatalf("válida: %v", err)
	}
	// retocada (un nodo más, otro orden, otra versión): la firma ya no vale
	for _, mal := range []func(x *ListaRed){
		func(x *ListaRed) { x.Arranque = append(x.Arranque, nodoDePrueba(t, "203.0.113.9")) },
		func(x *ListaRed) { x.Arranque[0], x.Arranque[1] = x.Arranque[1], x.Arranque[0] },
		func(x *ListaRed) { x.Version++ },
		func(x *ListaRed) { x.Relays = nil },
	} {
		x := *l
		x.Arranque = append([]string{}, l.Arranque...)
		mal(&x)
		if x.Verificar() == nil {
			t.Fatalf("lista retocada aceptada: %+v", x)
		}
	}
	// firmada con otra clave
	_, otra, _ := ed25519.GenerateKey(rand.Reader)
	if x, _ := FirmarListaRed(otra, ListaRed{Version: 1, Arranque: []string{a1}}); x.Verificar() == nil {
		t.Fatal("lista de otra clave aceptada")
	}
	// mal formadas: no se firman
	for _, x := range []ListaRed{{Version: 1}, {Version: 1, Arranque: []string{"/ip4/1.2.3.4/tcp/1"}},
		{Version: 1, Arranque: []string{a1 + "/p2p-circuit"}}, {Version: 1, Arranque: []string{"basura"}}} {
		if _, err := FirmarListaRed(priv, x); err == nil {
			t.Fatalf("firmó una lista mala: %+v", x)
		}
	}

	// guardada: solo si es más nueva (anti-rollback)
	datos := pruebas.TempDir(t)
	if ok, err := GuardarRedFirmada(datos, l); !ok || err != nil {
		t.Fatalf("guardar: %v %v", ok, err)
	}
	vieja, _ := FirmarListaRed(priv, ListaRed{Version: l.Version - 1, Arranque: []string{a2}})
	if ok, _ := GuardarRedFirmada(datos, vieja); ok {
		t.Fatal("una lista más vieja pisó a la guardada")
	}
	if g := LeerRedFirmada(datos); g == nil || g.Version != l.Version {
		t.Fatalf("leída: %+v", g)
	}
	// un red-firmada.json retocado a mano no se usa
	b, _ := os.ReadFile(filepath.Join(datos, NombreRedFirmada))
	os.WriteFile(filepath.Join(datos, NombreRedFirmada), []byte(strings.Replace(string(b), "198.51.100.1", "198.51.100.66", 1)), 0o600)
	if LeerRedFirmada(datos) != nil {
		t.Fatal("red-firmada.json retocado aceptado")
	}
	GuardarRedFirmada(pruebas.TempDir(t), l)

	// la unión: firmada más nueva primero, luego la del usuario (con líneas malas que no paran nada), luego la de fábrica
	datos = pruebas.TempDir(t)
	usuario := nodoDePrueba(t, "192.0.2.10")
	os.WriteFile(filepath.Join(datos, NombreRedUsuario), []byte("# mis nodos\n"+usuario+"\nrelay "+rl+"\nesto no vale\narranque /ip4/1.2.3.4/tcp/1\n"+a1+"\n"), 0o600)
	nueva, _ := FirmarListaRed(priv, ListaRed{Version: l.Version + 5, Arranque: []string{a2}})
	var logs []string
	r := CalcularRed(datos, []FuenteRed{{Lista: l, Origen: OrigenFirmadaPaq}, {Lista: nueva, Origen: OrigenFirmadaPaq},
		{Lista: &ListaRed{V: 1, Version: 99999999999999, Arranque: []string{a1}, Firma: "AAAA"}, Origen: OrigenFirmadaPaq}},
		func(f string, a ...any) { logs = append(logs, f) })
	if r.VersionFirmada != nueva.Version || r.Arranque[0].ID.String() != strings.Split(a2, "/p2p/")[1] {
		t.Fatalf("no manda la firmada más nueva: v%d %v", r.VersionFirmada, r.Arranque[0])
	}
	if len(r.Arranque) != 1+2+r.NFabrica || r.NUsuario != 3 || len(r.ErroresUsuario) != 2 || len(r.Relays) != 1 {
		t.Fatalf("unión: %d arranque (fábrica %d), usuario %d, malas %v, relays %d", len(r.Arranque), r.NFabrica, r.NUsuario, r.ErroresUsuario, len(r.Relays))
	}
	if g := LeerRedFirmada(datos); g == nil || g.Version != nueva.Version {
		t.Fatal("la firmada más nueva no se guardó")
	}
	// sin candidatas: la guardada sigue mandando (sobrevive aunque el paquete cambie)
	if r2 := CalcularRed(datos, nil, nil); r2.VersionFirmada != nueva.Version || r2.OrigenFirmada != OrigenFirmadaCach {
		t.Fatalf("sin candidatas: %+v", r2)
	}
	// sin nada: solo la de fábrica
	if r3 := CalcularRed(pruebas.TempDir(t), nil, nil); r3.VersionFirmada != 0 || len(r3.Arranque) != r3.NFabrica || r3.NFabrica == 0 {
		t.Fatalf("de fábrica: %+v", r3)
	}
}

// TestListaRedEnElPaquete: la lista viaja dentro del manifiesto firmado; una lista mala invalida el paquete.
func TestListaRedEnElPaquete(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	t.Cleanup(UsarClaveEditorDePrueba(pub))
	ceros := strings.Repeat("0", 64)
	man := ManifiestoPaquete{V: 1, Producto: ProductoPaquete, Version: 20261012120000, VersionLanzador: 1,
		Ficheros: []ArchivoPaquete{{Ruta: ExeLanzadorApp, Parte: ParteLanzador, SHA256: ceros, Tam: 1},
			{Ruta: ExeLanzadorApp + ExtFirma, Parte: ParteLanzador, SHA256: ceros, Tam: 1}}}
	l, _ := FirmarListaRed(priv, ListaRed{Version: 20261012110000, Arranque: []string{nodoDePrueba(t, "198.51.100.1")}})
	man.Red = l
	raw, _ := json.Marshal(man)
	firmado, err := FirmarManifiesto(priv, raw)
	if err != nil {
		t.Fatal(err)
	}
	pv, err := VerificarPaquete(firmado)
	if err != nil || pv.Man.Red == nil || pv.Man.Red.Version != l.Version {
		t.Fatalf("paquete con lista: %v %+v", err, pv)
	}
	// lista retocada dentro del manifiesto (aunque el editor firmara el manifiesto entero): rechazado
	malo := *l
	malo.Version++
	man.Red = &malo
	raw, _ = json.Marshal(man)
	if _, err := FirmarManifiesto(priv, raw); err == nil {
		t.Fatal("se firmó un manifiesto con una lista de red mala")
	}
	// un manifiesto sin «red» (los de antes) sigue valiendo
	man.Red = nil
	raw, _ = json.Marshal(man)
	if firmado, err = FirmarManifiesto(priv, raw); err != nil {
		t.Fatal(err)
	}
	if pv, err := VerificarPaquete(firmado); err != nil || pv.Man.Red != nil {
		t.Fatalf("sin lista: %v", err)
	}
}

// TestLANmDNSSinInternet (manual: YO2_LAN_REAL=1): en la red local DE VERDAD (no solo 127.0.0.1), sin nodos de arranque
// ni UPnP, el invitado encuentra al anfitrión por mDNS con el código y entra directo. Escucha en todas las interfaces:
// compilar el binario de pruebas a una ruta con permiso en el cortafuegos de Windows (si no, sale su aviso).
func TestLANmDNSSinInternet(t *testing.T) {
	if os.Getenv("YO2_LAN_REAL") != "1" {
		t.Skip("manual: YO2_LAN_REAL=1")
	}
	juegoFalso(t, 5730+offDir)
	red := OpcP2P{Arranque: []peer.AddrInfo{}, SinNAT: true, MinPares: 1}
	r := &registro{}
	h := NuevoLanzador(pruebas.TempDir(t), r.logf, io.Discard)
	h.Red = red
	h.PonerPuertoFijo(5949)
	t.Cleanup(h.Cerrar)
	if err := h.CrearServidor(context.Background(), OpcServidor{Nombre: "LAN", Max: 8, Offset: offDir, Modo: "p2p",
		Estado: EstadoFijo{Err: errors.New("sin panel")}}); err != nil {
		t.Fatal(err)
	}
	a := h.EstadoServidor().Acceso
	t.Logf("anfitrión: %+v", a)
	if a == nil || !a.Fijo {
		t.Fatalf("puerto fijo: %+v", a)
	}
	ri := &registro{}
	inv := NuevoLanzador(pruebas.TempDir(t), ri.logf, io.Discard)
	inv.Red = red
	t.Cleanup(inv.Cerrar)
	ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
	defer cancel()
	t0 := time.Now()
	c, err := inv.Entrar(ctx, Destino{Codigo: h.CodigoPrivado()}, OpcEntrar{Modo: "p2p", SinTuneles: true, EsperaP2P: 30 * time.Second}, nil)
	if err != nil {
		for _, l := range ri.log {
			t.Log(l)
		}
		t.Fatalf("por mDNS: %v", err)
	}
	defer c.Cerrar()
	mdns := false
	for _, l := range ri.log {
		mdns = mdns || strings.Contains(l, "mDNS: anfitrión en la red local")
	}
	t.Logf("entrada por mDNS en %v: %s (DHT: %d pares)", time.Since(t0).Round(time.Millisecond), c.Via, inv.nodo.DHT.RoutingTable().Size())
	if !mdns || !strings.Contains(c.Via, "red local") || inv.nodo.DHT.RoutingTable().Size() != 0 {
		t.Fatalf("no fue por mDNS en la red local: %s mdns=%v", c.Via, mdns)
	}
}
