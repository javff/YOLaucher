package juego

import (
	"bytes"
	"encoding/binary"
	"os"
	"path/filepath"
	"testing"
)

// kcejDePrueba: un merge.dat mínimo con las entradas dadas (nombre -> [guardado, tamaño original]).
func kcejDePrueba(t *testing.T, ruta string, ents []struct {
	nombre   string
	guardado []byte
	tam      int
}) {
	t.Helper()
	var b bytes.Buffer
	b.WriteString(kcejMagia)
	binary.Write(&b, binary.LittleEndian, uint32(len(ents)))
	off := 12 + len(ents)*kcejReg
	for _, e := range ents {
		nom := make([]byte, 256)
		for i, c := range []byte(e.nombre) {
			nom[i] = c<<4 | c>>4
		}
		b.Write(nom)
		binary.Write(&b, binary.LittleEndian, []uint32{uint32(off), uint32(e.tam), uint32(len(e.guardado))})
		off += len(e.guardado)
	}
	for _, e := range ents {
		b.Write(e.guardado)
	}
	if err := os.WriteFile(ruta, b.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestLeerKCEJyLZSS(t *testing.T) {
	// LZSS: 8 literales «ABCDABCD» con flags 0xFF y luego una referencia (flags 0x00) a 0xFEE, longitud 3+5 = 8
	comp := append([]byte{0xFF}, []byte("ABCDABCD")...)
	comp = append(comp, 0x00, 0xEE, 0xF5)
	d := t.TempDir()
	merge := filepath.Join(d, "merge.dat")
	kcejDePrueba(t, merge, []struct {
		nombre   string
		guardado []byte
		tam      int
	}{
		{`data\cardpack\1.iid`, []byte("hola"), 4},
		{`data\Cardpack\7011.ydc`, comp, 16},
	})
	got, err := leerKCEJ(merge, []string{`DATA\cardpack\7011.ydc`, `data\cardpack\1.iid`, `data\no\esta`})
	if err != nil {
		t.Fatal(err)
	}
	if string(got[`data\cardpack\1.iid`]) != "hola" {
		t.Fatalf("guardado: %q", got[`data\cardpack\1.iid`])
	}
	if string(got[`data\cardpack\7011.ydc`]) != "ABCDABCDABCDABCD" {
		t.Fatalf("lzss: %q", got[`data\cardpack\7011.ydc`])
	}
	if len(got) != 2 {
		t.Fatalf("entradas: %d", len(got))
	}
	os.WriteFile(merge, []byte("NOKCEJ--\x01\x00\x00\x00"), 0o644)
	if _, err := leerKCEJ(merge, nil); err != errNoKCEJ {
		t.Fatalf("magia mala: %v", err)
	}
}

func TestAplicarReceta(t *testing.T) {
	v := make([]uint16, tamYDC/2)
	copy(v, []uint16{10, 20, 30, 20, 40}) // main
	v[80], v[95] = 7, 9                   // side, extra
	var b bytes.Buffer
	binary.Write(&b, binary.LittleEndian, v)
	fuente := b.Bytes()
	w := make([]uint16, tamYDC/2)
	copy(w, []uint16{10, 30, 40, 50}) // sin el primer 20 ni el segundo (Quita 20 ×2), con 50 al final
	w[80], w[95] = 7, 9
	var e bytes.Buffer
	binary.Write(&e, binary.LittleEndian, w)
	r := recetaOficial{"Prueba", 1, []uint16{20, 20}, []uint16{50}, hexSHA(fuente), hexSHA(e.Bytes())}
	got, err := r.Aplicar(fuente)
	if err != nil || !bytes.Equal(got, e.Bytes()) {
		t.Fatalf("receta: %v", err)
	}
	if _, err := r.Aplicar(append([]byte{}, e.Bytes()...)); err == nil {
		t.Fatal("aceptó un .ydc con otro sha256")
	}
	r.SHA = hexSHA(fuente)
	if _, err := r.Aplicar(fuente); err == nil {
		t.Fatal("aceptó un resultado con otro sha256")
	}
}

func TestOficialesSinMergeDat(t *testing.T) {
	d := t.TempDir()
	m, avisos, err := MazosOficialesDelCliente(d)
	if err != nil || len(m) != 0 || len(avisos) != 1 {
		t.Fatalf("sin merge.dat: %d mazos, avisos %v, err %v", len(m), avisos, err)
	}
}

// Con el cliente 2008 de verdad (YO2_CLIENTE o el _original del repo privado): los 8 sha256 cuadran. Sin él, se salta.
func TestOficialesClienteReal(t *testing.T) {
	juego := os.Getenv("YO2_CLIENTE")
	if juego == "" {
		juego = filepath.Join("..", "..", "..", "..", "_original", "yo2_2008-11")
	}
	if !Existe(filepath.Join(juego, "merge.dat")) {
		t.Skip("sin el cliente 2008 (YO2_CLIENTE)")
	}
	m, avisos, err := MazosOficialesDelCliente(juego)
	if err != nil || len(avisos) != 0 || len(m) != len(recetasOficiales) {
		t.Fatalf("%d mazos, avisos %v, err %v", len(m), avisos, err)
	}
	for _, x := range recetasOficiales {
		if hexSHA(m[x.Nombre+".ydc"]) != x.SHA {
			t.Fatalf("%s distinto", x.Nombre)
		}
	}
	// los que se copian a cada perfil: populares (incrustados) + oficiales (del cliente)
	mazos, fallo := mazosAInstalar(juego, nil)
	if fallo || len(mazos) != len(recetasOficiales)+len(MazosPremontados()) {
		t.Fatalf("a instalar: %d (fallo %v)", len(mazos), fallo)
	}
}
