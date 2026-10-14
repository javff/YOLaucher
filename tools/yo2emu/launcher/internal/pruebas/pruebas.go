// Package pruebas: ayudas comunes para los _test.go del lanzador (no entra en ningún exe).
//
// TempDir sustituye a t.TempDir(). En Windows, t.TempDir() falla de vez en cuando al acabar la prueba con
// «TempDir RemoveAll cleanup: unlinkat …: El directorio no está vacío» (ERROR_DIR_NOT_EMPTY): un fichero recién
// escrito y ya cerrado sigue «pendiente de borrar» mientras otro proceso (el antivirus, el indexador) lo tiene abierto
// para mirarlo, y la carpeta no se puede quitar hasta que lo suelta. testing solo reintenta ERROR_ACCESS_DENIED y
// ERROR_SHARING_VIOLATION (2 s), no este. Aquí se reintenta CUALQUIER error durante unos segundos; si al final la
// carpeta sigue sin poder borrarse (p. ej. un fichero que la propia prueba dejó abierto), la prueba FALLA igual que
// antes: no se esconden fugas de handles de verdad, solo se espera a que terceros suelten los suyos.
package pruebas

import (
	"os"
	"strings"
	"testing"
	"time"
)

// Espera es cuánto se reintenta el borrado antes de dar la prueba por fallida.
var Espera = 15 * time.Second

// TempDir crea una carpeta temporal para la prueba y la borra al acabar (con reintentos; ver el paquete).
func TempDir(t testing.TB) string {
	t.Helper()
	dir, err := os.MkdirTemp("", nombre(t.Name()))
	if err != nil {
		t.Fatalf("TempDir: %v", err)
	}
	t.Cleanup(func() {
		n, err := BorrarConReintentos(dir, Espera)
		if err != nil {
			t.Errorf("TempDir: %s no se pudo borrar tras %d intentos en %s (¿un fichero que la prueba dejó abierto?): %v",
				dir, n, Espera, err)
		} else if n > 1 {
			t.Logf("TempDir: %s borrada al intento %d (un fichero seguía abierto un momento: antivirus o un proceso que acababa)", dir, n)
		}
	})
	return dir
}

// BorrarConReintentos hace os.RemoveAll(dir) hasta que sale bien o pasa `espera`. Devuelve los intentos hechos.
func BorrarConReintentos(dir string, espera time.Duration) (int, error) {
	fin := time.Now().Add(espera)
	pausa := 10 * time.Millisecond
	for n := 1; ; n++ {
		err := os.RemoveAll(dir)
		if err == nil {
			return n, nil
		}
		if time.Now().After(fin) {
			return n, err
		}
		time.Sleep(pausa)
		if pausa < 500*time.Millisecond {
			pausa *= 2
		}
	}
}

// nombre: prefijo de carpeta a partir del nombre de la prueba (sin separadores ni caracteres raros, corto).
func nombre(s string) string {
	b := strings.Builder{}
	for _, r := range s {
		if r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '_' {
			b.WriteRune(r)
		} else {
			b.WriteRune('_')
		}
		if b.Len() >= 48 {
			break
		}
	}
	return b.String() + "-"
}
