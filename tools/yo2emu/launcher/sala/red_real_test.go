package sala

// Prueba MANUAL por la red de verdad (DHT pública de IPFS): dos procesos en el mismo PC. No corre con go test normal.
//
//	go test -c -o sala.test.exe ./sala
//	set YO2_RED_REAL=1 & set YO2_RED_REAL_DIR=<carpeta temporal>
//	sala.test.exe -test.run TestRedRealAnfitrion -test.v -test.timeout 30m     (en una ventana; anuncia y espera)
//	sala.test.exe -test.run TestRedRealBuscar -test.v -test.timeout 30m        (en otra; mide cuánto tarda en verlo)
//
// El anfitrión se anuncia como «PRUEBA-NO-ENTRAR», solo P2P, con un run.py de mentira en 127.0.0.1:25730 (offset
// 20000: nunca los puertos del juego de verdad) y se cierra solo a los YO2_RED_REAL_MIN minutos (6 por defecto). Su
// anuncio en la DHT caduca solo (48 h) y, cerrado, no contesta fichas: ningún lanzador lo enseña.
// La lista no usa mDNS (solo la DHT); para ENTRAR en el mismo PC sí hace falta (o hairpin NAT en el router):
// YO2_RED_REAL_SIN_MDNS=1 lo quita.

import (
	"bufio"
	"context"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

func redReal(t *testing.T) (dir string, logf func(string, ...any)) {
	if os.Getenv("YO2_RED_REAL") != "1" {
		t.Skip("prueba manual por la red pública (YO2_RED_REAL=1)")
	}
	dir = os.Getenv("YO2_RED_REAL_DIR")
	if dir == "" {
		t.Fatal("falta YO2_RED_REAL_DIR")
	}
	os.MkdirAll(dir, 0o700)
	f, err := os.OpenFile(filepath.Join(dir, t.Name()+".log"), os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.Close() })
	lg := log.New(f, "", log.LstdFlags|log.Lmicroseconds)
	return dir, lg.Printf
}

func TestRedRealAnfitrion(t *testing.T) {
	dir, logf := redReal(t)
	min := 6
	if v, err := strconv.Atoi(os.Getenv("YO2_RED_REAL_MIN")); err == nil && v > 0 && v <= 30 {
		min = v
	}
	juegoFalso(t, 5730+offPrueba)
	l := NuevoLanzador(filepath.Join(dir, "anfitrion"), logf, nil)
	defer l.Cerrar()
	t0 := time.Now()
	if err := l.CrearServidor(context.Background(), OpcServidor{Nombre: "PRUEBA-NO-ENTRAR",
		Descripcion: "Prueba automática de la lista pública: no es un servidor de verdad", Max: 2, Publica: true,
		Reglas: ReglasVanilla, Idioma: "es", Offset: offPrueba, Modo: "p2p", SinMDNS: os.Getenv("YO2_RED_REAL_SIN_MDNS") == "1",
		Estado: EstadoFijo{D: DatosJuego{Jugadores: 0, Duelos: 0}}}); err != nil {
		t.Fatal(err)
	}
	id := l.idPropio()
	os.WriteFile(filepath.Join(dir, "id-anfitrion.txt"), []byte(id), 0o600)
	t.Logf("anfitrión %s (huella %s) arrancado; se cierra en %d min", id, l.EstadoServidor().Huella, min)
	listado := false
	fin := time.Now().Add(time.Duration(min) * time.Minute)
	for time.Now().Before(fin) {
		e := l.EstadoServidor()
		if e.ListadoOK && !listado {
			listado = true
			t.Logf("anunciado en la lista pública en %v (P2P: %s)", time.Since(t0).Round(time.Second), e.P2P)
		}
		time.Sleep(time.Second)
	}
	e := l.EstadoServidor()
	t.Logf("cerrando: invitados con túnel ahora %d; listado=%v", e.Invitados, listado)
	if !listado {
		t.Fatalf("no llegó a anunciarse en la lista: %+v", e)
	}
}

func TestRedRealBuscar(t *testing.T) {
	dir, logf := redReal(t)
	var id string
	for i := 0; i < 120 && id == ""; i++ { // el anfitrión escribe su id al arrancar
		b, _ := os.ReadFile(filepath.Join(dir, "id-anfitrion.txt"))
		id = strings.TrimSpace(string(b))
		time.Sleep(500 * time.Millisecond)
	}
	if id == "" {
		t.Fatal("no hay anfitrión de prueba")
	}
	l := NuevoLanzador(filepath.Join(dir, "invitado"), logf, nil)
	defer l.Cerrar()
	t0 := time.Now()
	var hallado *ServidorVisto
	var tHallado time.Duration
	total := 0
	for intento := 1; hallado == nil && time.Since(t0) < 8*time.Minute; intento++ {
		ctx, c := context.WithTimeout(context.Background(), 3*time.Minute)
		err := l.BuscarServidores(ctx, func(s ServidorVisto) {
			if s.Estado != EstadoVivo {
				return
			}
			total++
			t.Logf("  servidor visto a los %v: %q (%s, ping %d ms, relay=%v)", time.Since(t0).Round(100*time.Millisecond),
				s.Ficha.Nombre, s.Huella, s.PingMs, s.PorRelay)
			if s.ID == id && hallado == nil {
				x := s
				hallado, tHallado = &x, time.Since(t0)
				c() // basta: se corta la búsqueda
			}
		})
		c()
		t.Logf("búsqueda %d terminada a los %v (err=%v)", intento, time.Since(t0).Round(time.Second), err)
	}
	if hallado == nil {
		t.Fatalf("el anfitrión de prueba no aparece en la lista tras %v", time.Since(t0).Round(time.Second))
	}
	t.Logf("DESCUBIERTO en %v: %q ping %d ms relay=%v (servidores vivos vistos: %d)", tHallado.Round(100*time.Millisecond),
		hallado.Ficha.Nombre, hallado.PingMs, hallado.PorRelay, total)

	t1 := time.Now()
	con, err := l.Entrar(context.Background(), Destino{Servidor: hallado}, OpcEntrar{Modo: "p2p", SinMDNS: os.Getenv("YO2_RED_REAL_SIN_MDNS") == "1",
		SinTuneles: true}, func(e EventoEntrar) {
		t.Logf("  [%v] %s: %s %s", time.Since(t1).Round(100*time.Millisecond), e.Texto, e.Estado, e.Detalle)
	})
	if err != nil {
		t.Fatalf("Entrar: %v", err)
	}
	defer con.Cerrar()
	c, err := con.Dial(5730)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.SetReadDeadline(time.Now().Add(10 * time.Second))
	linea, err := bufio.NewReader(c).ReadString('\n')
	t.Logf("por el túnel (%s, latencia %v): %q %v", con.Via, con.Latencia.Round(100*time.Microsecond), strings.TrimSpace(linea), err)
	if !strings.HasPrefix(linea, "YO2MOCK ") {
		t.Fatal(fmt.Errorf("no llega al run.py de mentira"))
	}
	t.Logf("entrada completa en %v", time.Since(t1).Round(100*time.Millisecond))
}
