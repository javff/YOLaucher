package nucleo

// Prueba manual del PAQUETE del Discord (P5): «Host a server» con el núcleo de verdad sobre un paquete descomprimido
// (tools/yo2emu/paquete/empaquetar.py), PATH sin Python ni Node, carpeta de datos y %LOCALAPPDATA% temporales y puertos
// desplazados +5920 (banda de pruebas; nunca los del servidor vivo). Comprueba: con una carpeta de juego que no es
// Ver.080912 sale msg.paquete.datos_fallo; con la buena, primer_arranque.py + run.py del paquete con su Python, run.py
// escucha, el panel privado NO escucha pero la API de gestión (pestaña Manage) sí, con /publico (la ficha sale de
// run.py, no de los túneles), y «Parar» lo cierra con orden.
//
//	$env:YO2_PAQUETE_REAL = "<carpeta YO2OnlineLauncher descomprimida>"; $env:YO2_PAQUETE_JUEGO = "<cliente 2008>"
//	$env:YO2_PAQUETE_JUEGO_MALO = "<cliente 2006>" (opcional); go test ./app/nucleo -run PaqueteReal -v

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/libp2p/go-libp2p/core/peer"
	"yo2online/internal/pruebas"
	"yo2online/sala"
)

const offPaquete = 5920

func TestPaqueteRealHost(t *testing.T) {
	pkg, juegoBueno := os.Getenv("YO2_PAQUETE_REAL"), os.Getenv("YO2_PAQUETE_JUEGO")
	if pkg == "" || juegoBueno == "" {
		t.Skip("manual: YO2_PAQUETE_REAL y YO2_PAQUETE_JUEGO")
	}
	if sala.BuscarPaquete(pkg) == nil {
		t.Fatalf("%s no es un paquete completo", pkg)
	}
	local := pruebas.TempDir(t)
	t.Setenv("LOCALAPPDATA", local) // token de la consola de run.py aquí, no en el %LOCALAPPDATA% real
	t.Setenv("PATH", filepath.Join(os.Getenv("SystemRoot"), "System32"))
	t.Setenv("YO2_RUNPY", "")
	datos := filepath.Join(local, "YO2Online")
	os.MkdirAll(datos, 0o700)
	tdir := pruebas.TempDir(t)

	l := sala.NuevoLanzador(datos, t.Logf, io.Discard)
	l.Red = sala.OpcP2P{Arranque: []peer.AddrInfo{}, SoloLoopback: true, MinPares: 0}
	n := Nuevo(Config{Datos: datos, ExeDir: pkg, Version: 1, Offset: offPaquete, Lanzador: l, Logf: t.Logf,
		AjustarServidor: func(o *sala.OpcServidor) {
			o.Modo, o.SinMDNS = "p2p", true
			o.Env = append(o.Env, "YO2_BIND=127.0.0.1", "TEMP="+tdir, "TMP="+tdir)
		}})
	t.Cleanup(n.Cerrar)

	var nPy, nRun bool
	for _, c := range n.Comprobar() {
		if c.Nombre.K == "msg.chk.python" {
			nPy = c.Estado == "ok"
		}
		if c.Nombre.K == "msg.chk.runpy" {
			nRun = c.Estado == "ok"
		}
	}
	if !nPy || !nRun {
		t.Fatalf("Comprobar no ve el Python/run.py del paquete: %+v", n.Comprobar())
	}

	form := FormServidor{Nombre: "paquete", Max: 8, Publica: false, Reglas: "vanilla", Cuentas: "libre"}
	if malo := os.Getenv("YO2_PAQUETE_JUEGO_MALO"); malo != "" {
		if r := n.ElegirCarpetaJuego(malo); r.Carpeta == "" {
			t.Logf("carpeta mala no aceptada en Ajustes: %+v", r)
		}
		err := n.ArrancarServidor(form)
		if err == nil || !strings.Contains(err.Error(), "msg.paquete.datos_fallo") {
			t.Fatalf("con el cliente malo debería fallar con msg.paquete.datos_fallo: %v", err)
		}
		t.Logf("cliente malo -> %v", err)
	}
	if r := n.ElegirCarpetaJuego(juegoBueno); r.Carpeta == "" {
		t.Fatalf("Ajustes no acepta %s: %+v", juegoBueno, r)
	}
	t0 := time.Now()
	if err := n.ArrancarServidor(form); err != nil {
		t.Fatal(err)
	}
	esperar(t, 90*time.Second, "run.py escuchando", func() bool { return n.EstadoServidor().JuegoOK })
	t.Logf("run.py del paquete escuchando en %v", time.Since(t0).Round(100*time.Millisecond))
	e := n.EstadoServidor()
	if !e.RunPyPropio {
		t.Fatalf("estado: %+v", e)
	}
	time.Sleep(3 * time.Second)
	e = n.EstadoServidor()
	t.Logf("ficha: jugadores %d, en duelo %d, fuente %q", e.Jugadores, e.EnDuelo, e.FuenteJug)
	// INT 12-10: en modo paquete el panel va apagado, pero la API de gestión (8791 + offset) y su /publico funcionan
	if sala.Escucha("127.0.0.1:" + strconv.Itoa(8790+offPaquete)) {
		t.Fatal("el panel privado escucha en el paquete")
	}
	pub := sala.NuevoEstadoPanel(offPaquete)
	pub.URL = "" // solo la API de gestión
	d, err := pub.Leer(context.Background())
	if err != nil {
		t.Fatalf("/publico de la API de gestión: %v", err)
	}
	t.Logf("/publico: %+v", d)
	if d.VersionJuego == "" || d.Perfil == "" || d.Cuentas == "" {
		t.Fatalf("/publico sin datos: %+v", d)
	}
	if e.FuenteJug != "run.py" {
		t.Fatalf("la ficha no sale de run.py: %q", e.FuenteJug)
	}
	if _, err := n.GestionJugadores(); err != nil {
		t.Fatalf("Manage > Players: %v", err)
	}
	if c, err := n.GestionCuentas(); err != nil {
		t.Fatalf("Manage > Accounts: %v", err)
	} else {
		t.Logf("Manage > Accounts: %+v", c)
	}
	if _, err := n.GestionVetados(); err != nil {
		t.Fatalf("Manage > Bans: %v", err)
	}
	if f, err := n.GestionCopia(); err != nil {
		t.Fatalf("Manage > Backup: %v", err)
	} else {
		t.Logf("Manage > Backup: %s", f)
	}
	// estado y logs en <datos>\servidor\<puerto>, datos del juego en <datos>\juego_datos
	srv := filepath.Join(datos, "servidor", "11650")
	logs, _ := filepath.Glob(filepath.Join(srv, "logs", "*.log"))
	if len(logs) == 0 {
		t.Fatalf("no hay log de run.py en %s", srv)
	}
	b, _ := os.ReadFile(logs[len(logs)-1])
	if !strings.Contains(string(b), "[paquete] modo paquete") || !strings.Contains(string(b), "reglas listas") {
		t.Fatalf("run.py no arrancó en modo paquete con reglas:\n%s", b[:min(len(b), 3000)])
	}
	if _, err := os.Stat(filepath.Join(datos, "juego_datos", ".yo2-game-data.json")); err != nil {
		t.Fatal(err)
	}

	r := n.PararServidor()
	t.Logf("PararServidor: %+v", r)
	if !r.OK || r.Forzada {
		t.Fatalf("no se cerró con orden: %+v", r)
	}
	if sala.Escucha("127.0.0.1:11650") {
		t.Fatal("run.py sigue escuchando")
	}
}
