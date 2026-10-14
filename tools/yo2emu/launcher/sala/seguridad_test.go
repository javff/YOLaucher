package sala

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
	"yo2online/internal/pruebas"
)

func anfitrionDePrueba(t *testing.T) *Sala {
	t.Helper()
	s, cambiado, err := CargarAnfitrion(pruebas.TempDir(t), false, false)
	if err != nil || !cambiado {
		t.Fatalf("CargarAnfitrion: %v %v", err, cambiado)
	}
	return s
}

func TestCodigoIdaYVuelta(t *testing.T) {
	a := anfitrionDePrueba(t)
	c := a.Codigo()
	if len(c) != len("YO2-")+72+11 {
		t.Fatalf("longitud %d", len(c))
	}
	g, err := Parse(" " + c + "\r\n")
	if err != nil {
		t.Fatal(err)
	}
	if g.EsAnfitrion() || !g.pub.Equal(a.pub) || g.secreto != a.secreto || g.PeerID() != a.PeerID() ||
		g.Onion() != a.Onion() || g.Huella() != a.Huella() || g.Encuentro() != a.Encuentro() {
		t.Fatal("el invitado no deriva lo mismo que el anfitrión")
	}
	// Una letra cambiada se detecta (casi siempre: 1/256 no)
	m := []byte(c)
	if m[10] == 'A' {
		m[10] = 'B'
	} else {
		m[10] = 'A'
	}
	if _, err := Parse(string(m)); err == nil {
		t.Log("aviso: cambio no detectado por el byte de control (1/256)")
	}
}

func TestCodigoV1Rechazado(t *testing.T) {
	if _, err := Parse("YO2-ABCD-EFGH-IJKL-MNOP-QRST-UVWX"); !errors.Is(err, ErrCodigoV1) {
		t.Fatalf("esperaba ErrCodigoV1, %v", err)
	}
}

func TestPersistencia(t *testing.T) {
	dir := pruebas.TempDir(t)
	a, _, _ := CargarAnfitrion(dir, false, false)
	b, cambiado, _ := CargarAnfitrion(dir, false, false)
	if cambiado || b.Codigo() != a.Codigo() {
		t.Fatal("el código debe ser el mismo en cada arranque")
	}
	c, _, _ := CargarAnfitrion(dir, true, false)
	if c.Codigo() == a.Codigo() || !c.pub.Equal(a.pub) {
		t.Fatal("-nuevo-codigo: mismo anfitrión, otro secreto")
	}
	d, _, _ := CargarAnfitrion(dir, true, true)
	if d.pub.Equal(a.pub) {
		t.Fatal("-nueva-identidad: otra clave")
	}
	if _, err := os.Stat(filepath.Join(dir, "identidad.key.antes")); err != nil {
		t.Fatal("la identidad anterior se guarda como .antes")
	}
}

// saludo: anfitrión (srv) e invitado (cli) por una tubería en memoria.
func saludo(t *testing.T, srv, cli *Sala) error {
	t.Helper()
	c1, c2 := net.Pipe()
	a := &Anfitrion{Sala: srv, Logf: func(string, ...any) {}, LogW: io.Discard, Aviso: func(string) {}}
	go a.Servir(c1, "prueba")
	_, clave, _ := ed25519.GenerateKey(rand.Reader)
	ses, err := Entrar(c2, cli, clave, io.Discard)
	if ses != nil {
		ses.Close()
	}
	c2.Close()
	return err
}

func TestSaludoBueno(t *testing.T) {
	a := anfitrionDePrueba(t)
	g, _ := Parse(a.Codigo())
	if err := saludo(t, a, g); err != nil {
		t.Fatal(err)
	}
}

// Un invitado con el código (clave pública + secreto) monta un anfitrión con OTRA clave: el invitado lo rechaza.
func TestSuplantacionRechazada(t *testing.T) {
	a := anfitrionDePrueba(t)
	g, _ := Parse(a.Codigo())
	_, otra, _ := ed25519.GenerateKey(rand.Reader)
	impostor := &Sala{pub: a.pub, secreto: a.secreto, priv: otra}
	if err := saludo(t, impostor, g); !errors.Is(err, ErrImpostor) {
		t.Fatalf("esperaba ErrImpostor, %v", err)
	}
}

// Quien no tiene el secreto no entra (aunque tenga la clave pública).
func TestSinSecretoNoEntra(t *testing.T) {
	a := anfitrionDePrueba(t)
	g, _ := Parse(a.Codigo())
	g.secreto[0] ^= 1
	done := make(chan error, 1)
	go func() { done <- saludo(t, a, g) }()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("entró sin el secreto")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("colgado")
	}
}

func TestFirmaLanzador(t *testing.T) {
	// Con la clave pública incrustada no podemos firmar aquí (la privada no está en el repo): se comprueba que una
	// firma hecha con OTRA clave y una firma manipulada se rechazan.
	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	f := &FirmaLanzador{V: FirmaVersionV, Nombre: NombreLanzador, Version: 99999999999999,
		SHA256: "0000000000000000000000000000000000000000000000000000000000000000", Tam: 1}
	f.Firma = base64.StdEncoding.EncodeToString(ed25519.Sign(priv, f.Mensaje()))
	if err := f.Verificar(); err == nil {
		t.Fatal("aceptó una firma que no es del editor")
	}
	if _, err := ClaveEditor(); err != nil {
		t.Fatal(err)
	}
	var nada *FirmaLanzador
	if nada.Verificar() == nil {
		t.Fatal("aceptó sin firma")
	}
}

func TestNombreMazo(t *testing.T) {
	for _, n := range []string{"facil_agua.ydc", "Mazo 1.YDC"} {
		if !NombreMazoValido(n) {
			t.Errorf("%q debería valer", n)
		}
	}
	for _, n := range []string{"../x.ydc", `a\b.ydc`, "a/b.ydc", "x.exe", "c:x.ydc", "CON.ydc", ".ydc", "x.ydc:ads", "x.ydc "} {
		if NombreMazoValido(n) {
			t.Errorf("%q NO debería valer", n)
		}
	}
}
