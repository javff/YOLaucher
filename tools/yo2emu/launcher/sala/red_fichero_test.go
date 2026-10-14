package sala

import (
	"os"
	"testing"
)

// TestNodosDelEditor: launcher/red/nodos.txt (lo que firma empaquetar.py --firmar) se lee entero, sin líneas malas.
func TestNodosDelEditor(t *testing.T) {
	b, err := os.ReadFile("../red/nodos.txt")
	if err != nil {
		t.Fatal(err)
	}
	a, r, malas := LeerNodosTexto(b)
	if len(malas) > 0 || len(a) == 0 || len(a) > maxNodosRed || len(r) > maxNodosRed {
		t.Fatalf("red/nodos.txt: %d de arranque, %d relays, malas %v", len(a), len(r), malas)
	}
	l := ListaRed{V: 1, Version: 1, Arranque: a, Relays: r}
	if err := l.Validar(); err != nil {
		t.Fatal(err)
	}
}
