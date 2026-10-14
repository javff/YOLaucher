package juego

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"yo2online/internal/pruebas"
)

func TestMazosPremontadosIncrustados(t *testing.T) {
	m := MazosPremontados()
	// Tantos como .ydc haya en mazos/ (los 7 populares; los 8 Structure Decks salen del cliente: mazos_cliente.go)
	ents, err := os.ReadDir("mazos")
	if err != nil {
		t.Fatal(err)
	}
	enDisco := 0
	for _, e := range ents {
		if !e.IsDir() && strings.HasSuffix(strings.ToLower(e.Name()), ".ydc") && !strings.EqualFold(e.Name(), "deck.ydc") {
			enDisco++
		}
	}
	if len(m) == 0 || len(m) != enDisco {
		t.Fatalf("hay %d mazos incrustados, se esperaban %d (los .ydc de mazos/): %v", len(m), enDisco, m)
	}
	for _, o := range RecetasOficiales() { // un incrustado con el nombre de un oficial lo taparía
		for _, n := range m {
			if strings.EqualFold(n, o+".ydc") {
				t.Fatalf("%s está incrustado y también sale del cliente", n)
			}
		}
	}
	for _, n := range m {
		d, err := mazosFS.ReadFile("mazos/" + n)
		if err != nil || len(d) == 0 {
			t.Fatalf("%s: %v (%d bytes)", n, err, len(d))
		}
	}
}

func TestInstalarMazos(t *testing.T) {
	juego := pruebas.TempDir(t)
	if n := InstalarMazos(juego); n != 0 { // sin profiles\ no se crea nada
		t.Fatalf("sin profiles se copiaron %d", n)
	}
	if Existe(filepath.Join(juego, "profiles")) {
		t.Fatal("se creó profiles\\")
	}
	prof := filepath.Join(juego, "profiles")
	a, b := filepath.Join(prof, "Alice"), filepath.Join(prof, "Bob")
	os.MkdirAll(a, 0o755)
	os.MkdirAll(b, 0o755)
	os.WriteFile(filepath.Join(prof, "d97636ff.bin"), []byte("x"), 0o644) // fichero suelto: se ignora
	deck := []byte("mazo activo del jugador")
	os.WriteFile(filepath.Join(a, "deck.ydc"), deck, 0o644)
	suyo := []byte("su propio Zombies")
	os.WriteFile(filepath.Join(a, "Zombies.ydc"), suyo, 0o644)

	total := len(MazosPremontados())
	if n := InstalarMazos(juego); n != 2*total-1 {
		t.Fatalf("copiados %d, se esperaban %d", n, 2*total-1)
	}
	// no pisa lo del jugador ni toca deck.ydc
	if d, _ := os.ReadFile(filepath.Join(a, "Zombies.ydc")); !bytes.Equal(d, suyo) {
		t.Fatal("se pisó un .ydc del jugador")
	}
	if d, _ := os.ReadFile(filepath.Join(a, "deck.ydc")); !bytes.Equal(d, deck) {
		t.Fatal("se tocó deck.ydc")
	}
	if Existe(filepath.Join(b, "deck.ydc")) {
		t.Fatal("se creó deck.ydc")
	}
	for _, n := range MazosPremontados() {
		want, _ := mazosFS.ReadFile("mazos/" + n)
		if got, err := os.ReadFile(filepath.Join(b, n)); err != nil || !bytes.Equal(got, want) {
			t.Fatalf("%s en Bob: %v", n, err)
		}
	}
	for _, p := range []string{a, b} {
		if !Existe(filepath.Join(p, MarcaMazos)) {
			t.Fatalf("falta la marca en %s", p)
		}
		tmps, _ := filepath.Glob(filepath.Join(p, "*.tmp"))
		if len(tmps) != 0 {
			t.Fatalf("quedan temporales: %v", tmps)
		}
	}
	// lo que el jugador borra no reaparece (marca)
	os.Remove(filepath.Join(b, "Monarchs.ydc"))
	if n := InstalarMazos(juego); n != 0 || Existe(filepath.Join(b, "Monarchs.ydc")) {
		t.Fatalf("segunda pasada copió %d", n)
	}
	// perfil nuevo creado durante la partida: se le copian en la siguiente pasada
	c := filepath.Join(prof, "Carol")
	os.MkdirAll(c, 0o755)
	if n := InstalarMazos(juego); n != total {
		t.Fatalf("perfil nuevo: copiados %d, se esperaban %d", n, total)
	}
	// sin la marca, solo vuelven los que faltan
	os.Remove(filepath.Join(b, MarcaMazos))
	if n := InstalarMazos(juego); n != 1 || !Existe(filepath.Join(b, "Monarchs.ydc")) {
		t.Fatalf("sin marca: copiados %d, se esperaba 1", n)
	}
}
