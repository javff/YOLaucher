package juego

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"yo2online/sala"
)

// El «exe nuevo» de estas pruebas es este mismo binario de test en modo ayudante (JUEGO_TEST_HIJO).
func TestMain(m *testing.M) {
	switch os.Getenv("JUEGO_TEST_HIJO") {
	case "duerme":
		time.Sleep(3 * time.Second)
		return
	case "sale1":
		os.Exit(1)
	case "sale0":
		os.Exit(0)
	}
	os.Exit(m.Run())
}

// exePrueba: el nombre del exe que se reemplaza (el de ahora; TestRelanzarDesdeNombreViejo pone el de antes del 12-10).
var exePrueba = sala.ExeLanzadorApp

func probarRelanzar(t *testing.T, modo string, o OpcActualiza) (relanzado bool, codigo int, deshecho string, a *Actualizador, f sala.Fichero, nuevo string) {
	t.Helper()
	// no es t.TempDir(): el «exe nuevo» sigue vivo unos segundos y Windows no deja borrar un exe en marcha
	datos, err := os.MkdirTemp("", "juego-relanzar")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		for i := 0; i < 50; i++ {
			if os.RemoveAll(datos) == nil {
				return
			}
			time.Sleep(200 * time.Millisecond)
		}
	})
	os.MkdirAll(filepath.Join(datos, "actualiza"), 0o700) // lo crea instalar() antes de relanzar
	a = &Actualizador{Datos: datos, Version: 1, Tipo: sala.TipoLanzadorApp, Nombre: sala.NombreLanzadorApp}
	exe := filepath.Join(datos, exePrueba)
	b, _ := os.ReadFile(os.Args[0])
	os.WriteFile(exe, b, 0o755)
	nuevo = filepath.Join(datos, "descarga.exe")
	os.WriteFile(nuevo, []byte("x"), 0o755)
	f = sala.Fichero{Nombre: sala.NombreLanzadorApp, SHA256: strings.Repeat("ab", 32),
		Firma: &sala.FirmaLanzador{Version: 2}}
	o.Args = []string{"-test.run=NoExiste"}
	o.Env = append(o.Env, "JUEGO_TEST_HIJO="+modo)
	relanzado, codigo = a.relanzar(&sala.Invitado{}, nil, f, exe, nuevo, o, func(sala.Msg) {}, func(m sala.Msg) { deshecho = m.Es })
	return
}

func TestRelanzarVentanaSigueVivo(t *testing.T) {
	t0 := time.Now()
	rel, _, deshecho, _, _, nuevo := probarRelanzar(t, "duerme", OpcActualiza{EsperaHijo: 700 * time.Millisecond})
	if !rel || deshecho != "" {
		t.Fatalf("relanzado=%v deshecho=%q", rel, deshecho)
	}
	if d := time.Since(t0); d < 600*time.Millisecond || d > 5*time.Second {
		t.Fatalf("esperó %v", d)
	}
	if _, err := os.Stat(nuevo); err == nil {
		t.Fatal("la descarga no se borró")
	}
}

func TestRelanzarVentanaSeCaeAlArrancar(t *testing.T) {
	rel, _, deshecho, a, f, nuevo := probarRelanzar(t, "sale1", OpcActualiza{EsperaHijo: 5 * time.Second})
	if rel {
		t.Fatal("dio por bueno un exe que se cae al arrancar")
	}
	if !strings.Contains(deshecho, "se cerró al arrancar") {
		t.Fatalf("no deshizo el reemplazo: %q", deshecho)
	}
	if _, err := os.Stat(a.marcaFallida(f.SHA256)); err != nil {
		t.Fatalf("no dejó la marca de versión fallida: %v", err)
	}
	if _, err := os.Stat(nuevo); err == nil {
		t.Fatal("la descarga mala sigue ahí")
	}
}

func TestRelanzarPasaElEntorno(t *testing.T) {
	// el hijo recibe EnvActualizado y lo que se le dé (el destino al que volver) y el PID del padre
	rel, _, deshecho, _, _, _ := probarRelanzar(t, "duerme", OpcActualiza{EsperaHijo: 300 * time.Millisecond, Env: []string{"YO2_RETOMAR=pub:x", "YO2_PADRE_PID=123"}})
	if !rel || deshecho != "" {
		t.Fatalf("relanzado=%v deshecho=%q", rel, deshecho)
	}
}

// Un lanzador de antes del 12-10 se llama «YO2Launcher.exe»: se actualiza igual (reemplaza SU exe, sin buscar un nombre
// fijo) y la identidad firmada sigue siendo sala.NombreLanzadorApp.
func TestRelanzarDesdeNombreViejo(t *testing.T) {
	exePrueba = "YO2Launcher.exe"
	defer func() { exePrueba = sala.ExeLanzadorApp }()
	rel, _, deshecho, a, _, _ := probarRelanzar(t, "duerme", OpcActualiza{EsperaHijo: 700 * time.Millisecond})
	if !rel || deshecho != "" {
		t.Fatalf("relanzado=%v deshecho=%q", rel, deshecho)
	}
	if a.Nombre != sala.NombreLanzadorApp {
		t.Fatalf("identidad %q", a.Nombre)
	}
}
