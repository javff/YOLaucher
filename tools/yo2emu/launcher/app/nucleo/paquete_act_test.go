package nucleo

// De punta a punta con dos (tres) lanzadores en este proceso, cada uno con su carpeta de datos (como
// YO2_LANZADOR_DATOS) y la red P2P de 127.0.0.1 de integracion_test.go: un anfitrión reparte la versión N+1 del paquete
// (desde su semilla) y un invitado con la N la baja (a) al hacer Join a un servidor privado (túnel, puerto 3) y (b)
// al verlo en la lista pública (libp2p), y la aplica al «arrancar». La clave del editor es una de prueba.

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/libp2p/go-libp2p/core/peer"
	"yo2online/internal/pruebas"
	"yo2online/sala"
)

func arbolPaquete(t *testing.T, priv ed25519.PrivateKey, dir string, version int64, ficheros map[string]string) {
	t.Helper()
	exe := []byte("exe de prueba (igual en las dos versiones)")
	for r, c := range ficheros {
		p := filepath.Join(dir, filepath.FromSlash(r))
		os.MkdirAll(filepath.Dir(p), 0o755)
		os.WriteFile(p, []byte(c), 0o644)
	}
	os.WriteFile(filepath.Join(dir, sala.ExeLanzadorApp), exe, 0o755)
	h := sha256.Sum256(exe)
	f := sala.FirmaLanzador{V: sala.FirmaVersionV, Nombre: sala.NombreLanzadorApp, Version: 20261012000000,
		SHA256: hex.EncodeToString(h[:]), Tam: int64(len(exe))}
	f.Firma = base64.StdEncoding.EncodeToString(ed25519.Sign(priv, f.Mensaje()))
	b, _ := json.Marshal(f)
	os.WriteFile(filepath.Join(dir, sala.ExeLanzadorApp+sala.ExtFirma), b, 0o644)
	raw, err := sala.ManifiestoDeArbol(dir, version, f.Version)
	if err != nil {
		t.Fatal(err)
	}
	out, err := sala.FirmarManifiesto(priv, raw)
	if err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(dir, sala.NombrePaquete), out, 0o644)
}

func ficherosPaquete(v int) map[string]string {
	return map[string]string{
		"README.txt":           fmt.Sprintf("readme %d", v),
		"server/yo2emu/run.py": fmt.Sprintf("print(%d)", v),
		"server/yo2emu/paquete/primer_arranque.py": "x",
		"python/python.exe":                        "py",
		"node/node.exe":                            "node",
		"engine/duelcore.mjs":                      fmt.Sprintf("motor %d", v),
		"vendor/cards.cdb":                         "cdb",
	}
}

// nucleoConExe: como nucleoDeRed, pero con el exe del lanzador en exe (dentro de un paquete) y la semilla que se diga.
func nucleoConExe(t *testing.T, red []peer.AddrInfo, exe string) (*Nucleo, *grabador, string) {
	t.Helper()
	datos := pruebas.TempDir(t)
	juegoDir := filepath.Join(datos, "juego")
	os.MkdirAll(juegoDir, 0o755)
	os.WriteFile(filepath.Join(juegoDir, "yo2.exe"), []byte("MZ Ver.080912.00"), 0o644)
	l := sala.NuevoLanzador(datos, func(string, ...any) {}, io.Discard)
	l.Red = sala.OpcP2P{Arranque: red, SoloLoopback: true, MinPares: 1}
	l.EsperaFicha = 3 * time.Second
	l.EsperaBusqueda = 20 * time.Second
	g := &grabador{}
	n := Nuevo(Config{Datos: datos, ExeDir: datos, Exe: exe, Version: 20261012000000, Offset: offHost, Lanzador: l, Emitir: g.emitir, RunPyAjeno: true,
		Logf: t.Logf,
		AjustarEntrada: func(o *sala.OpcEntrar) {
			o.Modo, o.SinMDNS, o.OffsetLocal, o.AbrirJuego = "p2p", true, offLocal, nil
		},
		AjustarServidor: func(o *sala.OpcServidor) {
			o.Modo, o.SinMDNS = "p2p", true
			o.Estado = sala.EstadoFijo{Err: fmt.Errorf("sin panel")}
		}})
	t.Cleanup(n.Cerrar)
	if r := n.ElegirCarpetaJuego(juegoDir); !r.OK {
		t.Fatal(r.Msg.Es)
	}
	return n, g, datos
}

func (g *grabador) cuantos(ev string) int {
	g.mu.Lock()
	defer g.mu.Unlock()
	k := 0
	for _, e := range g.evs {
		if e.ev == ev {
			k++
		}
	}
	return k
}

func TestPaqueteEntreDosLanzadores(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	t.Cleanup(sala.UsarClaveEditorDePrueba(pub))
	juegoFalso(t, 5730+offHost)
	red := redLocal(t, 3)

	// anfitrión: lanzador de desarrollo (no es un paquete) con la versión 2 en su semilla, servidor PRIVADO
	h, _, hDatos := nucleoConExe(t, red, filepath.Join(pruebas.TempDir(t), "YO2OnlineLauncher.exe"))
	arbolPaquete(t, priv, filepath.Join(hDatos, "semilla"), 2, ficherosPaquete(2))
	if e := h.EstadoActualizacion(); e.Actualizable || e.Reparte != 2 {
		t.Fatalf("anfitrión: %+v", e)
	}
	if err := h.ArrancarServidor(FormServidor{Nombre: "Privado", Max: 8, Publica: false, Reglas: "vanilla", Cuentas: "libre"}); err != nil {
		t.Fatal(err)
	}
	esperar(t, 20*time.Second, "anuncio", func() bool { return h.EstadoServidor().P2POK })

	// (a) Join con código: el invitado (paquete versión 1) la baja por el túnel en segundo plano
	pkg1 := pruebas.TempDir(t)
	arbolPaquete(t, priv, pkg1, 1, ficherosPaquete(1))
	exe1 := filepath.Join(pkg1, sala.ExeLanzadorApp)
	inv, g, _ := nucleoConExe(t, red, exe1)
	if e := inv.EstadoActualizacion(); !e.Actualizable || e.Instalada != 1 || e.Fase != "" {
		t.Fatalf("invitado: %+v", e)
	}
	if err := inv.EntrarConCodigo(h.l.CodigoPrivado()); err != nil {
		t.Fatal(err)
	}
	esperar(t, 60*time.Second, "versión 2 lista (Join)", func() bool {
		e := inv.EstadoActualizacion()
		return e.Fase == "lista" && e.Version == 2
	})
	if g.cuantos("paquete") == 0 {
		t.Fatal("la página no se enteró")
	}
	inv.Desconectar()
	// el botón: aquí el «exe» no es un programa de verdad, así que no se puede relanzar (y la ventana no se cierra)
	if r := inv.ReiniciarParaActualizar(); r.OK || r.Motivo != "error" {
		t.Fatalf("reiniciar: %+v", r)
	}
	// el «arranque» siguiente la aplica
	if AplicarAlArrancar(exe1, 5900, t.Logf) {
		t.Fatal("el exe no cambiaba: no hay que relanzar")
	}
	if b, _ := os.ReadFile(filepath.Join(pkg1, "server", "yo2emu", "run.py")); string(b) != "print(2)" {
		t.Fatalf("run.py tras aplicar: %q", b)
	}
	if b, _ := os.ReadFile(filepath.Join(pkg1, "engine", "duelcore.mjs")); string(b) != "motor 2" {
		t.Fatalf("motor tras aplicar: %q", b)
	}

	// (b) lista pública: otro invitado con la versión 1 la ve en la ficha y la baja por libp2p (sin código)
	h.PararServidor()
	if err := h.ArrancarServidor(FormServidor{Nombre: "Publico", Max: 8, Publica: true, Reglas: "vanilla", Cuentas: "libre"}); err != nil {
		t.Fatal(err)
	}
	esperar(t, 20*time.Second, "anuncio en la lista", func() bool { return h.EstadoServidor().ListadoOK })
	pkg2 := pruebas.TempDir(t)
	arbolPaquete(t, priv, pkg2, 1, ficherosPaquete(1))
	inv2, _, _ := nucleoConExe(t, red, filepath.Join(pkg2, sala.ExeLanzadorApp))
	inv2.Buscar()
	esperar(t, 60*time.Second, "versión 2 lista (lista pública)", func() bool {
		e := inv2.EstadoActualizacion()
		return e.Fase == "lista" && e.Version == 2
	})
	// y ahora el invitado actualizado también la reparte (si hospeda)
	inv3, _, _ := nucleoConExe(t, red, exe1)
	if e := inv3.EstadoActualizacion(); e.Instalada != 2 || e.Reparte != 2 {
		t.Fatalf("tras aplicar: %+v", e)
	}
}
