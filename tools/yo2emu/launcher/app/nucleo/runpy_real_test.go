package nucleo

// Prueba manual (YO2_RUNPY_REAL=1): crea un servidor con el run.py DE VERDAD de esta copia del repo, con los puertos
// desplazados +30000 (35730..., consola 35799, panel 38790; no toca los del servidor vivo), almacén json y estado/logs
// en una carpeta temporal, y lo para con orden por su consola: comprueba que «DEADLINE = 1» hace terminar el proceso
// solo y que el panel /publico da jugadores/duelos. Necesita Python con frida (el de run.py) y unos segundos.
//
//	$env:YO2_RUNPY_REAL = "1"; go test ./app/nucleo -run RunPyReal -v

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/libp2p/go-libp2p/core/peer"
	"yo2online/internal/pruebas"
	"yo2online/sala"
)

func TestRunPyRealParadaOrdenada(t *testing.T) {
	if os.Getenv("YO2_RUNPY_REAL") != "1" {
		t.Skip("manual: YO2_RUNPY_REAL=1")
	}
	runpy, _ := filepath.Abs(filepath.Join("..", "..", "..", "run.py"))
	if _, err := os.Stat(runpy); err != nil {
		t.Skip("no está run.py junto a launcher\\: " + err.Error())
	}
	local := pruebas.TempDir(t)
	t.Setenv("LOCALAPPDATA", local) // el token de la consola va aquí, no al %LOCALAPPDATA% real
	t.Setenv("YO2_RUNPY", runpy)
	datos := filepath.Join(local, "YO2Online")
	os.MkdirAll(datos, 0o700)
	tdir := pruebas.TempDir(t)

	l := sala.NuevoLanzador(datos, t.Logf, io.Discard)
	l.Red = sala.OpcP2P{Arranque: []peer.AddrInfo{}, SoloLoopback: true, MinPares: 0}
	n := Nuevo(Config{Datos: datos, ExeDir: filepath.Dir(runpy), Version: 1, Offset: offHost, Lanzador: l, Logf: t.Logf,
		AjustarServidor: func(o *sala.OpcServidor) {
			o.Modo, o.SinMDNS = "p2p", true
			o.Env = append(o.Env, "YO2_STORAGE=json", "YO2_STATE="+filepath.Join(tdir, "state.json"), "YO2_LOGDIR="+tdir,
				"YO2_BIND=127.0.0.1", "PYTHONIOENCODING=utf-8", "TEMP="+tdir, "TMP="+tdir)
		}})
	t.Cleanup(n.Cerrar)

	form := FormServidor{Nombre: "run.py real", Max: 8, Publica: false, Reglas: "mejorado", Cuentas: "cerradas"}
	if err := n.ArrancarServidor(form); err != nil {
		t.Fatal(err)
	}
	esperar(t, 90*time.Second, "run.py escuchando", func() bool { return n.EstadoServidor().JuegoOK })
	e := n.EstadoServidor()
	if !e.RunPyPropio || e.Aviso.K != "" {
		t.Fatalf("estado: %+v", e)
	}
	// el panel /publico de run.py da el perfil que se pidió y las cuentas cerradas
	esperar(t, 30*time.Second, "estado del panel", func() bool { return n.EstadoServidor().FuenteJug == "run.py" })
	d, err := sala.NuevoEstadoPanel(offHost).Leer(t.Context())
	if err != nil {
		t.Fatalf("/publico: %v", err)
	}
	t.Logf("/publico = %+v", d)
	if d.Perfil != "enhanced" || d.Cuentas != "cerradas" || d.VersionJuego != "080912" {
		t.Fatalf("run.py no recibió el formulario: %+v", d)
	}
	proc, _ := n.l.RunPyPropio()
	if proc == nil {
		t.Fatal("no hay proceso propio")
	}
	if err := n.CopiarCodigo(); err == nil { // sin portapapeles configurado
		t.Fatal("copió sin portapapeles")
	}

	t0 := time.Now()
	r := n.PararServidor()
	t.Logf("PararServidor: %+v (%v)", r, time.Since(t0).Round(100*time.Millisecond))
	if !r.OK || r.Forzada || !strings.Contains(r.Mensaje, "con orden") {
		t.Fatalf("no se cerró con orden: %+v", r)
	}
	if sala.Escucha(fmt.Sprintf("127.0.0.1:%d", 5730+offHost)) {
		t.Fatal("run.py sigue escuchando")
	}
}
