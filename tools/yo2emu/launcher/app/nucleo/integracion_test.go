package nucleo

// Pruebas de punta a punta de la ventana contra una red P2P que vive dentro del proceso (DHT de 3 nodos en 127.0.0.1),
// con el «run.py» de mentira y los puertos desplazados (host +30000, invitado +31000): nada toca los puertos reales
// del juego ni la DHT pública.

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/libp2p/go-libp2p"
	dht "github.com/libp2p/go-libp2p-kad-dht"
	"github.com/libp2p/go-libp2p/core/peer"
	"yo2online/internal/pruebas"
	"yo2online/sala"
)

const (
	offHost  = 30000 // el «run.py» de mentira escucha en 127.0.0.1:35730
	offLocal = 31000 // los puertos locales del invitado: 36730...
)

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
	time.Sleep(300 * time.Millisecond)
	return ais
}

func juegoFalso(t *testing.T, puerto int) {
	t.Helper()
	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", puerto))
	if err != nil {
		t.Skipf("el puerto %d de la prueba está ocupado: %v", puerto, err)
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

// grabador: guarda los eventos que la ventana mandaría a la página.
type grabador struct {
	mu  sync.Mutex
	evs []struct {
		ev string
		d  any
	}
}

func (g *grabador) emitir(ev string, d any) {
	g.mu.Lock()
	g.evs = append(g.evs, struct {
		ev string
		d  any
	}{ev, d})
	g.mu.Unlock()
}

// entradas: los eventos «entrar» en orden.
func (g *grabador) entradas() []EventoEntrada {
	g.mu.Lock()
	defer g.mu.Unlock()
	var r []EventoEntrada
	for _, e := range g.evs {
		if e.ev == "entrar" {
			r = append(r, e.d.(EventoEntrada))
		}
	}
	return r
}

func (g *grabador) ultimaLista() Lista {
	g.mu.Lock()
	defer g.mu.Unlock()
	var l Lista
	for _, e := range g.evs {
		if e.ev == "lista" {
			l = e.d.(Lista)
		}
	}
	return l
}

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

func nucleoDeRed(t *testing.T, red []peer.AddrInfo, retomar ...string) (*Nucleo, *grabador) {
	t.Helper()
	datos := pruebas.TempDir(t)
	// la carpeta del juego de mentira (para que «Entrar» no se queje); AbrirJuego se quita: no se lanza nada
	juegoDir := filepath.Join(datos, "juego")
	os.MkdirAll(juegoDir, 0o755)
	os.WriteFile(filepath.Join(juegoDir, "yo2.exe"), []byte("MZ Ver.080912.00"), 0o644)
	l := sala.NuevoLanzador(datos, func(string, ...any) {}, io.Discard)
	l.Red = sala.OpcP2P{Arranque: red, SoloLoopback: true, MinPares: 1}
	l.EsperaFicha = 3 * time.Second
	l.EsperaBusqueda = 20 * time.Second
	g := &grabador{}
	n := Nuevo(Config{Datos: datos, ExeDir: datos, Version: 20261011120000, Offset: offHost, Lanzador: l, Emitir: g.emitir, RunPyAjeno: true,
		AjustarEntrada: func(o *sala.OpcEntrar) {
			o.Modo, o.SinMDNS, o.OffsetLocal, o.AbrirJuego = "p2p", true, offLocal, nil
		},
		AjustarServidor: func(o *sala.OpcServidor) {
			o.Modo, o.SinMDNS = "p2p", true
			o.Estado = sala.EstadoFijo{Err: fmt.Errorf("sin panel")} // jugadores por túneles, duelos «no se sabe»
		}})
	t.Cleanup(n.Cerrar)
	if r := n.ElegirCarpetaJuego(juegoDir); !r.OK {
		t.Fatal(r.Msg.Es)
	}
	return n, g
}

func TestVentanaDePuntaAPunta(t *testing.T) {
	juegoFalso(t, 5730+offHost)
	red := redLocal(t, 3)

	// ---- anfitrión: crear servidor con el formulario (el run.py «ya estaba» en marcha: lo arrancó otro)
	h, _ := nucleoDeRed(t, red)
	form := FormServidor{Nombre: "Servidor de prueba", Descripcion: "hola", Max: 8, Publica: true, Reglas: "mejorado", Cuentas: "cerradas"}
	if err := h.ArrancarServidor(form); err != nil {
		t.Fatal(err)
	}
	esperar(t, 20*time.Second, "anuncio en la lista pública", func() bool { return h.EstadoServidor().ListadoOK })
	if err := h.ArrancarServidor(form); err == nil {
		t.Fatal("dejó crear dos servidores")
	}
	if got := h.FormServidor(); got != form.Normalizado() {
		t.Fatalf("el formulario no se recuerda: %+v", got)
	}
	es := h.EstadoServidor()
	if !es.EnMarcha || es.RunPyPropio || es.Aviso.K == "" || es.Max != 8 {
		t.Fatalf("estado del servidor: %+v", es)
	}
	if err := h.CopiarCodigo(); err == nil {
		t.Fatal("un servidor público no copia código")
	}

	// ---- Manage: con el servidor en marcha el núcleo habla con la API de gestión (aquí, la de mentira de gestion_test.go)
	api := nuevaApiFalsa(t, "tok-de-integracion-123456")
	api.respuestas["/gestion/jugadores"] = map[string]any{"jugadores": []map[string]any{
		{"corto": "abc123", "nombre": "Zeta", "dueno": false, "estado": "sala", "mundo": nil, "zona": nil, "desde_s": 7, "vetado": false}}}
	loc := pruebas.TempDir(t)
	os.WriteFile(filepath.Join(loc, "gestion_"+strconv.Itoa(api.puerto())+".txt"), []byte("tok-de-integracion-123456"), 0o600)
	h.cfg.Gestion = &Gestion{Base: api.srv.URL, Local: loc}
	if js, err := h.GestionJugadores(); err != nil || len(js) != 1 || js[0].Nombre != "Zeta" {
		t.Fatalf("Manage con el servidor en marcha: %v %v", js, err)
	}
	if err := h.GestionVetar("abc123", "spam"); err != nil {
		t.Fatalf("vetar: %v", err)
	}

	// ---- invitado: la lista, entrar, desconectar
	inv, g := nucleoDeRed(t, red)
	inv.Buscar()
	esperar(t, 30*time.Second, "el servidor en la lista", func() bool {
		for _, f := range g.ultimaLista().Filas {
			if f.Estado == sala.EstadoVivo {
				return true
			}
		}
		return false
	})
	esperar(t, 30*time.Second, "búsqueda terminada", func() bool { return !g.ultimaLista().Buscando })
	l := g.ultimaLista()
	if len(l.Filas) != 1 {
		t.Fatalf("filas: %+v", l.Filas)
	}
	f := l.Filas[0]
	if f.Nombre != "Servidor de prueba" || f.Max != 8 || f.Reglas != "mejorado" || f.Cuentas != "cerradas" ||
		f.EnDuelo != -1 || !f.Compatible || f.Estado != "vivo" || f.Huella != h.EstadoServidor().Huella || f.IDCorto == "" || f.Lleno {
		t.Fatalf("fila: %+v", f)
	}
	if err := inv.Entrar("peer-que-no-esta"); err == nil {
		t.Fatal("entró en un servidor que no está en la lista")
	}
	if err := inv.Entrar(f.ID); err != nil {
		t.Fatal(err)
	}
	esperar(t, 30*time.Second, "entrar", func() bool {
		ev := g.entradas()
		return len(ev) > 0 && (ev[len(ev)-1].Fase == "listo" || ev[len(ev)-1].Fase == "error")
	})
	var resumen []string
	for _, e := range g.entradas() {
		switch e.Fase {
		case "paso":
			resumen = append(resumen, fmt.Sprintf("%d:%s", e.Paso, e.Estado))
		default:
			resumen = append(resumen, e.Fase)
		}
		if e.Fase == "error" {
			t.Fatalf("falló: %s / %s", e.Error.Es, e.Tecnico)
		}
	}
	want := "inicio 0:va 0:ya 1:va 1:ya 2:va 2:ya 3:va 3:ya listo"
	if got := strings.Join(resumen, " "); got != want {
		t.Fatalf("pasos: %s", got)
	}
	e := inv.Estado()
	if e.Conexion == nil || e.Conexion.Nombre != "Servidor de prueba" || !e.Conexion.Publica || !strings.HasPrefix(e.Conexion.Via.K, "msg.via.p2p") {
		t.Fatalf("estado del invitado: %+v", e.Conexion)
	}
	// un puerto local lleva al «run.py» del anfitrión (el túnel de verdad funciona)
	c, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", 5730+offLocal), 3*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	c.SetReadDeadline(time.Now().Add(5 * time.Second))
	linea, err := bufio.NewReader(c).ReadString('\n')
	c.Close()
	if err != nil || !strings.HasPrefix(linea, "YO2MOCK") {
		t.Fatalf("por el túnel llega %q (%v)", linea, err)
	}
	// conectado: no se puede entrar en otro a la vez; y cerrar pide confirmación
	if err := inv.Entrar(f.ID); codigoDe(err) != "msg.err.ya_conectado" {
		t.Fatalf("entrar estando conectado: %v", err)
	}
	if !inv.AntesDeCerrar() {
		t.Fatal("conectado: el cierre debe pedir confirmación")
	}
	esperar(t, 10*time.Second, "el anfitrión cuenta al invitado", func() bool { return h.EstadoServidor().Invitados == 1 })
	inv.Desconectar()
	if inv.Estado().Conexion != nil {
		t.Fatal("sigue conectado")
	}
	if inv.AntesDeCerrar() {
		t.Fatal("desconectado: puede cerrar")
	}

	// ---- «Parar»: el run.py no es nuestro, así que se deja en marcha y se dice
	r := h.PararServidor()
	if !r.OK || r.Forzada || !strings.Contains(r.Mensaje, "no lo arrancó este lanzador") {
		t.Fatalf("parar: %+v", r)
	}
	if h.EstadoServidor().EnMarcha {
		t.Fatal("el servidor sigue en marcha")
	}
	if !sala.Escucha(fmt.Sprintf("127.0.0.1:%d", 5730+offHost)) {
		t.Fatal("se cerró un run.py que no era nuestro")
	}
}

// Tras reiniciarse por una actualización, la ventana vuelve a entrar sola en el servidor de antes (por peer ID).
func TestVentanaRetomaTrasActualizar(t *testing.T) {
	juegoFalso(t, 5730+offHost)
	red := redLocal(t, 3)
	h, _ := nucleoDeRed(t, red)
	if err := h.ArrancarServidor(FormServidor{Nombre: "Retomar", Max: 8, Publica: true, Reglas: "vanilla", Cuentas: "libre"}); err != nil {
		t.Fatal(err)
	}
	esperar(t, 20*time.Second, "anuncio", func() bool { return h.EstadoServidor().ListadoOK })
	peerID := h.l.EstadoServidor().PeerID

	t.Setenv(EnvRetomar, "pub:"+peerID) // lo que dejaría el proceso anterior
	inv, g := nucleoDeRed(t, red)
	if !inv.Inicio().Reanudando {
		t.Fatal("no sabe que viene de una actualización")
	}
	if os.Getenv(EnvRetomar) != "" {
		t.Fatal("el entorno de retomar no se borró")
	}
	inv.Listo()
	esperar(t, 60*time.Second, "volver a entrar sin pedir nada", func() bool {
		ev := g.entradas()
		return len(ev) > 0 && (ev[len(ev)-1].Fase == "listo" || ev[len(ev)-1].Fase == "error")
	})
	ev := g.entradas()
	if last := ev[len(ev)-1]; last.Fase != "listo" || last.Servidor != "Retomar" {
		t.Fatalf("último evento: %+v", last)
	}
	if inv.Estado().Conexion == nil {
		t.Fatal("no quedó conectado")
	}
	inv.Desconectar()
	if inv.Inicio().Reanudando {
		t.Fatal("sigue marcado como reanudando")
	}
}
