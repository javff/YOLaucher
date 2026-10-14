package pruebas

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

// Un fichero que sigue abierto de verdad NO se esconde: tras la espera, el borrado da error (en Windows, un fichero
// abierto sin FILE_SHARE_DELETE no se puede borrar). Cerrado, se borra.
func TestBorrarConReintentosNoEscondeFugas(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("solo Windows impide borrar un fichero abierto")
	}
	dir, err := os.MkdirTemp("", "pruebas-fuga-")
	if err != nil {
		t.Fatal(err)
	}
	f, err := os.Create(filepath.Join(dir, "abierto.txt"))
	if err != nil {
		t.Fatal(err)
	}
	t0 := time.Now()
	n, err := BorrarConReintentos(dir, 300*time.Millisecond)
	if err == nil {
		t.Fatalf("con un fichero abierto se borró la carpeta (%d intentos)", n)
	}
	if d := time.Since(t0); d < 300*time.Millisecond || n < 2 {
		t.Fatalf("no reintentó: %d intentos en %s", n, d)
	}
	f.Close()
	if _, err := BorrarConReintentos(dir, 5*time.Second); err != nil {
		t.Fatalf("cerrado, no se borra: %v", err)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatalf("la carpeta sigue ahí: %v", err)
	}
}

func TestTempDirSeBorraAlAcabar(t *testing.T) {
	var dir string
	t.Run("hija", func(t *testing.T) {
		dir = TempDir(t)
		if err := os.WriteFile(filepath.Join(dir, "x"), []byte("x"), 0o600); err != nil {
			t.Fatal(err)
		}
	})
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatalf("%s sigue ahí tras la prueba: %v", dir, err)
	}
}
