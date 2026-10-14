package juego

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"yo2online/internal/pruebas"
)

func TestColocarDLL(t *testing.T) {
	dir := pruebas.TempDir(t)
	dst := filepath.Join(dir, NombreDLL)
	nueva := []byte("MZ ... " + MarcaDLL + " 2 ...")

	if que, err := ColocarDLL(dir, nueva); err != nil || que != "copiada" {
		t.Fatalf("sin version.dll: %q %v", que, err)
	}
	if que, err := ColocarDLL(dir, nueva); err != nil || que != "ya estaba" {
		t.Fatalf("igual: %q %v", que, err)
	}
	os.WriteFile(dst, []byte("MZ ... "+MarcaDLL+" 1 ..."), 0o644) // nuestra, de otra versión
	if que, err := ColocarDLL(dir, nueva); err != nil || que != "actualizada" {
		t.Fatalf("nuestra vieja: %q %v", que, err)
	}
	if b, _ := os.ReadFile(dst); !bytes.Equal(b, nueva) {
		t.Fatal("no se cambió la nuestra vieja")
	}
	ajena := []byte("MZ version.dll de otro mod")
	os.WriteFile(dst, ajena, 0o644)
	if _, err := ColocarDLL(dir, nueva); !errors.Is(err, ErrDLLAjena) {
		t.Fatalf("ajena: %v", err)
	}
	if b, _ := os.ReadFile(dst); !bytes.Equal(b, ajena) {
		t.Fatal("se pisó una version.dll ajena")
	}
	if ents, _ := os.ReadDir(dir); len(ents) != 1 {
		t.Fatalf("quedan ficheros temporales: %v", ents)
	}
}

func TestElegirGancho(t *testing.T) {
	juego, datos := pruebas.TempDir(t), pruebas.TempDir(t)
	l := &Lanzador{Datos: datos}
	dll := []byte("MZ " + MarcaDLL + " prueba")
	viejo := DLLIncrustada
	defer func() { DLLIncrustada = viejo }()
	t.Setenv("YO2_GANCHO", "")

	DLLIncrustada = func() []byte { return nil }
	if m, _ := l.ElegirGancho(juego, false); m != GanchoFrida {
		t.Fatalf("sin DLL incrustada: %s", m)
	}
	DLLIncrustada = func() []byte { return dll }
	if m, _ := l.ElegirGancho(juego, false); m != GanchoDLL {
		t.Fatalf("por defecto: %s", m)
	}
	if m, _ := l.ElegirGancho(juego, true); m != GanchoFrida {
		t.Fatalf("diagnóstico: %s", m)
	}
	t.Setenv("YO2_GANCHO", "FRIDA")
	if m, _ := l.ElegirGancho(juego, false); m != GanchoFrida {
		t.Fatalf("YO2_GANCHO=frida: %s", m)
	}
	t.Setenv("YO2_GANCHO", "")
	os.WriteFile(filepath.Join(juego, "yo2-frida.txt"), nil, 0o644)
	if m, _ := l.ElegirGancho(juego, false); m != GanchoFrida {
		t.Fatalf("yo2-frida.txt: %s", m)
	}
	os.Remove(filepath.Join(juego, "yo2-frida.txt"))

	// falló esta DLL -> frida; otra DLL (lanzador nuevo) -> se vuelve a probar; YO2_GANCHO=dll la fuerza
	if err := marcarFalloDLL(datos, dll); err != nil {
		t.Fatal(err)
	}
	if m, _ := l.ElegirGancho(juego, false); m != GanchoFrida {
		t.Fatalf("tras un fallo: %s", m)
	}
	t.Setenv("YO2_GANCHO", "dll")
	if m, _ := l.ElegirGancho(juego, false); m != GanchoDLL {
		t.Fatalf("YO2_GANCHO=dll: %s", m)
	}
	t.Setenv("YO2_GANCHO", "")
	DLLIncrustada = func() []byte { return append(dll, '2') }
	if m, _ := l.ElegirGancho(juego, false); m != GanchoDLL {
		t.Fatalf("otra DLL tras el fallo de la anterior: %s", m)
	}
}

func TestVolcarLogDLL(t *testing.T) {
	var out bytes.Buffer
	l := &Lanzador{Log: &out}
	p := filepath.Join(pruebas.TempDir(t), FicheroLogDLL)
	if ok, hay := l.volcarLogDLL(p); ok || hay {
		t.Fatal("sin fichero")
	}
	os.WriteFile(p, []byte("10:00:00 [yo2net] YO2ONLINE-NETDLL 1 cargada\r\n10:00:00 [yo2net] ganchos: FALLO\r\n"), 0o644)
	if ok, hay := l.volcarLogDLL(p); ok || !hay {
		t.Fatal("con FALLO no vale")
	}
	os.WriteFile(p, []byte("10:00:00 [yo2net] gancho connect: WSOCK32.dll IAT 0x012d61f0\r\n10:00:00 [yo2net] ganchos OK\r\n"), 0o644)
	if ok, _ := l.volcarLogDLL(p); !ok {
		t.Fatal("con ganchos OK")
	}
	if !strings.Contains(out.String(), "IAT 0x012d61f0\n") {
		t.Fatalf("no se copió al log: %q", out.String())
	}
}

func TestExtraerOffset(t *testing.T) {
	l := &Lanzador{Datos: pruebas.TempDir(t)}
	_, hook, err := l.Extraer(false, 5900)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(hook)
	if !strings.HasPrefix(string(b), "const YO2_OFFSET = 5900;\n") || !strings.Contains(string(b), "redirect_hook cargado") {
		t.Fatalf("script: %.80q", b)
	}
}
