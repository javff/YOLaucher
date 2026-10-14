package juego

import (
	"os"
	"path/filepath"
	"testing"
	"yo2online/internal/pruebas"
)

func TestNormalizarYBuscarCarpeta(t *testing.T) {
	a, b, datos := pruebas.TempDir(t), pruebas.TempDir(t), pruebas.TempDir(t)
	os.WriteFile(filepath.Join(a, "yo2.exe"), []byte("MZ Ver.080912.00"), 0o644)
	os.WriteFile(filepath.Join(b, "yo2.exe"), []byte("MZ Ver.060419.00"), 0o644)

	if got := NormalizarCarpeta(`  "` + filepath.Join(a, "YO2.EXE") + `" `); got != a {
		t.Fatalf("NormalizarCarpeta con comillas y ruta del exe: %q", got)
	}
	if got := NormalizarCarpeta(a); got != a {
		t.Fatalf("NormalizarCarpeta: %q", got)
	}
	viejo := RutaPorDefecto
	RutaPorDefecto = ""
	defer func() { RutaPorDefecto = viejo }()

	if got := BuscarCarpeta("", pruebas.TempDir(t), datos); got != "" {
		t.Fatalf("encontró un juego que no hay: %q", got)
	}
	// orden: la pedida, junto al exe, la guardada
	GuardarCarpeta(datos, b)
	if got := BuscarCarpeta("", pruebas.TempDir(t), datos); got != b {
		t.Fatalf("guardada: %q", got)
	}
	if got := BuscarCarpeta("", a, datos); got != a {
		t.Fatalf("junto al exe debe ganar a la guardada: %q", got)
	}
	if got := BuscarCarpeta(b, a, datos); got != b {
		t.Fatalf("la pedida debe ganar a todo: %q", got)
	}
	if got := BuscarCarpeta(filepath.Join(a, "no-existe"), pruebas.TempDir(t), datos); got != b {
		t.Fatalf("una pedida que no vale se salta: %q", got)
	}
	// un yo2.exe de otra versión no tapa al bueno: gana el de la versión que usan los servidores (el cliente de 2006,
	// Ver.060419, es el que deja la instalación de Konami en %ProgramFiles%; el de 2008, Ver.080912, está en otra carpeta)
	GuardarCarpeta(datos, a)
	if got := BuscarCarpeta("", b, datos); got != a {
		t.Fatalf("el Ver.080912 guardado debe ganar al Ver.060419 junto al exe: %q", got)
	}
	RutaPorDefecto = b
	if got := BuscarCarpeta("", pruebas.TempDir(t), datos); got != a {
		t.Fatalf("la ruta de Konami con otra versión tapó a la guardada: %q", got)
	}
	GuardarCarpeta(datos, filepath.Join(a, "no-existe"))
	if got := BuscarCarpeta("", pruebas.TempDir(t), datos); got != b {
		t.Fatalf("sin ninguna de la versión buena, vale la primera que haya: %q", got)
	}
	if got := BuscarCarpeta(b, a, datos); got != b {
		t.Fatalf("la pedida es una elección explícita y manda aunque sea de otra versión: %q", got)
	}
	if !CarpetaValida(a) || CarpetaValida("") || CarpetaValida(pruebas.TempDir(t)) {
		t.Fatal("CarpetaValida")
	}
}

func TestVersionJuego(t *testing.T) {
	d := pruebas.TempDir(t)
	if _, err := VersionJuego(d); err == nil {
		t.Fatal("sin yo2.exe no hay versión")
	}
	os.WriteFile(filepath.Join(d, "yo2.exe"), []byte("MZxxxx"), 0o644)
	if _, err := VersionJuego(d); err == nil {
		t.Fatal("un exe sin «Ver.» no tiene versión")
	}
	os.WriteFile(filepath.Join(d, "yo2.exe"), []byte("MZ\x00Ver.080912.00\x00 y mas"), 0o644)
	if v, err := VersionJuego(d); err != nil || v != VersionEsperada {
		t.Fatalf("%q %v", v, err)
	}
}

func TestProcesoActivo(t *testing.T) {
	if !ProcesoActivo(filepath.Base(os.Args[0])) {
		t.Fatal("no ve este mismo proceso")
	}
	if ProcesoActivo("no-existe-yo2-prueba.exe") {
		t.Fatal("ve un proceso que no existe")
	}
}

func TestLimpiarTmpSoloLaPropia(t *testing.T) {
	d := pruebas.TempDir(t)
	tmp := filepath.Join(d, "tmp")
	os.MkdirAll(filepath.Join(tmp, "frida-abc"), 0o755)
	os.WriteFile(filepath.Join(tmp, "x"), nil, 0o644)
	limpiarTmp(tmp)
	if ents, _ := os.ReadDir(tmp); len(ents) != 0 {
		t.Fatalf("no se vació: %v", ents)
	}
	otra := filepath.Join(d, "documentos") // otra carpeta cualquiera: no se toca nunca
	os.MkdirAll(otra, 0o755)
	os.WriteFile(filepath.Join(otra, "importante.txt"), nil, 0o644)
	limpiarTmp(otra)
	if _, err := os.Stat(filepath.Join(otra, "importante.txt")); err != nil {
		t.Fatal("borró fuera de su carpeta tmp")
	}
}
