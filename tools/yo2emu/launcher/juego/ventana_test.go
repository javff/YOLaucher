package juego

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

func TestNormalizarVentana(t *testing.T) {
	casos := []struct {
		en   string
		sale string
		ok   bool
	}{
		{"", VentanaOriginal, true}, {"orig", VentanaOriginal, true}, {"Original", VentanaOriginal, true}, {"0", VentanaOriginal, true},
		{"1.5", VentanaX15, true}, {"1.5x", VentanaX15, true}, {" 1.5X ", VentanaX15, true},
		{"2", VentanaX2, true}, {"2x", VentanaX2, true},
		{"fit", VentanaAjustar, true}, {"FIT", VentanaAjustar, true},
		{"3", VentanaOriginal, false}, {"fit2", VentanaOriginal, false}, {"1,5", VentanaOriginal, false}, {"1.50", VentanaOriginal, false},
		{"2;calc", VentanaOriginal, false},
	}
	for _, c := range casos {
		got, ok := NormalizarVentana(c.en)
		if got != c.sale || ok != c.ok {
			t.Errorf("NormalizarVentana(%q) = %q,%v; quería %q,%v", c.en, got, ok, c.sale, c.ok)
		}
	}
}

// Original (por defecto) y valores raros no pasan nada a la DLL: apagado = el juego tal cual.
func TestEntornoVentana(t *testing.T) {
	for _, v := range []string{"", "orig", "basura", "3"} {
		if e := entornoVentana(v); e != nil {
			t.Errorf("entornoVentana(%q) = %v; debía ser nada", v, e)
		}
	}
	for v, esperado := range map[string]string{"1.5": "YO2ONLINE_WINDOW=1.5", "2x": "YO2ONLINE_WINDOW=2", "FIT": "YO2ONLINE_WINDOW=fit"} {
		if e := entornoVentana(v); !slices.Equal(e, []string{esperado}) {
			t.Errorf("entornoVentana(%q) = %v; quería %s", v, e, esperado)
		}
	}
}

// buscarGcc: un gcc NORMAL del PC (no el de 32 bits sin CRT de la DLL) para compilar dll/test_ventana.c.
func buscarGcc() string {
	if p, err := exec.LookPath("gcc"); err == nil {
		return p
	}
	cands := []string{os.Getenv("W64DEVKIT")}
	if h, err := os.UserHomeDir(); err == nil {
		cands = append(cands, filepath.Join(h, "Desktop", "Herramientas", "w64devkit"), `C:\w64devkit`)
	}
	for _, c := range cands {
		if c == "" {
			continue
		}
		g := filepath.Join(c, "bin", "gcc.exe")
		if runtime.GOOS != "windows" {
			g = filepath.Join(c, "bin", "gcc")
		}
		if _, err := os.Stat(g); err == nil {
			return g
		}
	}
	return ""
}

// Las cuentas del modo «ventana ampliada» (dll/yo2win.h: coordenadas del ratón, «fit», tamaños) son C puro; se prueban aquí
// compilando dll/test_ventana.c con el gcc del PC. Sin gcc se salta (la comprobación también la hace build_dll.ps1).
func TestCuentasVentanaC(t *testing.T) {
	gcc := buscarGcc()
	if gcc == "" {
		t.Skip("no hay gcc: se salta dll/test_ventana.c")
	}
	dir := t.TempDir()
	exe := filepath.Join(dir, "test_ventana.exe")
	src := filepath.Join("dll", "test_ventana.c")
	cmd := exec.Command(gcc, "-O1", "-Wall", "-Werror", "-o", exe, src)
	cmd.Env = append(os.Environ(), "PATH="+filepath.Dir(gcc)+string(os.PathListSeparator)+os.Getenv("PATH"))
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("no compila %s: %v\n%s", src, err, out)
	}
	out, err := exec.Command(exe).CombinedOutput()
	if err != nil {
		t.Fatalf("test_ventana falló: %v\n%s", err, out)
	}
	if !strings.Contains(string(out), " 0 fallos") {
		t.Fatalf("salida inesperada:\n%s", out)
	}
}
