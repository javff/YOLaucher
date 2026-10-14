package sala

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"yo2online/internal/pruebas"
)

// Un paquete de mentira: solo existe si están TODAS las piezas; el del repo de desarrollo (sin server\) da nil.
func TestBuscarPaquete(t *testing.T) {
	d := pruebas.TempDir(t)
	piezas := []string{`server\yo2emu\run.py`, `python\python.exe`, `node\node.exe`, `engine\duelcore.mjs`,
		`server\yo2emu\paquete\primer_arranque.py`, `vendor\cards.cdb`}
	for i, p := range piezas {
		if BuscarPaquete(d) != nil {
			t.Fatalf("con %d de %d piezas ya da paquete", i, len(piezas))
		}
		f := filepath.Join(d, p)
		os.MkdirAll(filepath.Dir(f), 0o700)
		os.WriteFile(f, []byte("x"), 0o600)
	}
	p := BuscarPaquete(d)
	if p == nil {
		t.Fatal("con todas las piezas no encuentra el paquete")
	}
	if RunPyPaquete(d) != filepath.Join(d, "server", "yo2emu", "run.py") {
		t.Fatalf("RunPyPaquete = %q", RunPyPaquete(d))
	}
	if BuscarPaquete("") != nil || RunPyPaquete(filepath.Join(d, "no")) != "" {
		t.Fatal("sin carpeta o carpeta sin paquete debería dar nil / \"\"")
	}

	o := OpcServidor{Offset: 5910, Env: []string{"YO2_PERFIL=vanilla"}}
	datos := filepath.Join(d, "datos")
	p.Ajustar(&o, datos)
	if o.RunPy != p.RunPy || o.Python != p.Python || pythonDe(o) != p.Python {
		t.Fatalf("Ajustar no pone run.py/python del paquete: %+v", o)
	}
	env := strings.Join(o.Env, "\n")
	for _, want := range []string{"YO2_PERFIL=vanilla", "YO2_PAQUETE=1", "YO2_ADMIN_PANEL=0",
		"YO2_JUEGO_DATOS=" + filepath.Join(datos, "juego_datos"),
		"YO2_STATE=" + filepath.Join(datos, "servidor", "11640", "state.json"),
		"YO2_LOGDIR=" + filepath.Join(datos, "servidor", "11640", "logs"),
		"YO2_NODE=" + p.Node, "YO2_DUELCORE=" + p.Duelcore, "YO2_VENDOR=" + p.Vendor} {
		if !strings.Contains(env, want) {
			t.Errorf("falta %q en el entorno:\n%s", want, env)
		}
	}
	// «My servers» (12-10): un servidor con su propia carpeta lleva ahí el estado y los logs, nunca a servidor\<puerto>
	propio := filepath.Join(datos, "servidores", "0123456789abcdef", "servidor")
	o2 := OpcServidor{Offset: 5910, DirDatos: propio}
	p.Ajustar(&o2, datos)
	env2 := strings.Join(o2.Env, "\n")
	if !strings.Contains(env2, "YO2_STATE="+filepath.Join(propio, "state.json")) || !strings.Contains(env2, "YO2_LOGDIR="+filepath.Join(propio, "logs")) ||
		strings.Contains(env2, filepath.Join(datos, "servidor", "11640")) {
		t.Fatalf("con DirDatos el estado no va a su carpeta:\n%s", env2)
	}
	var nada *Paquete
	nada.Ajustar(&o, datos) // nil: no hace nada ni revienta
	if pythonDe(OpcServidor{}) != PythonExe() {
		t.Fatal("sin Python en las opciones debería usar el del sistema")
	}
}

func TestLineasError(t *testing.T) {
	s := lineasError("Game data: 0 files\r\nERROR: the game in X is Ver.060419, but ...\nmore")
	if !strings.HasPrefix(s, "ERROR: the game in X is Ver.060419") || strings.Contains(s, "more") {
		t.Fatalf("lineasError = %q", s)
	}
	if lineasError("a\nb\nc\nd") != "b c d" {
		t.Fatalf("sin ERROR deberían salir las 3 últimas: %q", lineasError("a\nb\nc\nd"))
	}
}
