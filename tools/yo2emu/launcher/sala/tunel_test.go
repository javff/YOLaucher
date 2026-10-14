package sala

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/hashicorp/yamux"
	"yo2online/internal/pruebas"
)

// ---------------------------------------------------------------- utilidades

// registro: Logf/Aviso de prueba que guardan lo que se dice.
type registro struct {
	mu     sync.Mutex
	log    []string
	avisos []string
}

func (r *registro) logf(f string, a ...any) {
	r.mu.Lock()
	r.log = append(r.log, fmt.Sprintf(f, a...))
	r.mu.Unlock()
}

func (r *registro) aviso(m string) {
	r.mu.Lock()
	r.avisos = append(r.avisos, m)
	r.mu.Unlock()
}

// contiene espera (hasta 5 s) a que alguna línea del log (o de los avisos) contenga t.
func (r *registro) contiene(t *testing.T, enAvisos bool, txt string) {
	t.Helper()
	fin := time.Now().Add(5 * time.Second)
	for time.Now().Before(fin) {
		r.mu.Lock()
		l := r.log
		if enAvisos {
			l = r.avisos
		}
		for _, x := range l {
			if strings.Contains(x, txt) {
				r.mu.Unlock()
				return
			}
		}
		r.mu.Unlock()
		time.Sleep(10 * time.Millisecond)
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	t.Fatalf("no aparece %q (log=%q avisos=%q)", txt, r.log, r.avisos)
}

func nuevoAnfitrion(t *testing.T) (*Anfitrion, *Sala, *registro) {
	t.Helper()
	s := anfitrionDePrueba(t)
	g, _ := Parse(s.Codigo())
	r := &registro{}
	return &Anfitrion{Sala: s, Logf: r.logf, LogW: io.Discard, Aviso: r.aviso}, g, r
}

func claveNueva(t *testing.T) ed25519.PrivateKey {
	t.Helper()
	_, k, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return k
}

// conectar: un invitado completo (handshake + yamux) contra a por una tubería en memoria.
func conectar(t *testing.T, a *Anfitrion, g *Sala, clave ed25519.PrivateKey) *yamux.Session {
	t.Helper()
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	ses, err := Entrar(c2, g, clave, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ses.Close() })
	return ses
}

// ipDe espera a que el anfitrión tenga n túneles vivos con el id de clave y devuelve sus IPs.
func ipsDe(t *testing.T, a *Anfitrion, clave ed25519.PrivateKey, n int) []string {
	t.Helper()
	id := IDInvitado(clave.Public().(ed25519.PublicKey))
	fin := time.Now().Add(5 * time.Second)
	for time.Now().Before(fin) {
		ts := a.delMismoID(id)
		if len(ts) == n {
			var r []string
			for _, x := range ts {
				r = append(r, x.ip)
			}
			return r
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("esperaba %d túneles vivos con ese id, hay %d", n, len(a.delMismoID(id)))
	return nil
}

// eco: abre el puerto 0 y comprueba que vuelve lo mismo (el invitado está dentro y se le atiende).
func eco(t *testing.T, ses *yamux.Session) {
	t.Helper()
	hecho := make(chan error, 1)
	go func() {
		st, err := AbrirPuerto(ses, 0)
		if err != nil {
			hecho <- err
			return
		}
		defer st.Close()
		if _, err := st.Write([]byte("hola")); err != nil {
			hecho <- err
			return
		}
		b := make([]byte, 4)
		if _, err := io.ReadFull(st, b); err != nil {
			hecho <- err
			return
		}
		if string(b) != "hola" {
			hecho <- errors.New("eco distinto")
			return
		}
		hecho <- nil
	}()
	select {
	case err := <-hecho:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("el anfitrión no atiende al invitado (¿bloqueado?)")
	}
}

// saludoCrudo: el handshake v3 a mano (sin yamux después), para falsificar partes. pubDecl = la pública que se dice
// tener; firma = con qué clave se firma. Devuelve la conexión del invitado (tras el saludo) o error.
func saludoCrudo(c net.Conn, g *Sala, pubDecl ed25519.PublicKey, firma ed25519.PrivateKey) error {
	c.SetDeadline(time.Now().Add(5 * time.Second))
	defer c.SetDeadline(time.Time{})
	nI := make([]byte, 32)
	rand.Read(nI)
	if _, err := c.Write(append([]byte(magia), nI...)); err != nil {
		return err
	}
	r := make([]byte, lenPaso2)
	if _, err := io.ReadFull(c, r); err != nil {
		return err
	}
	nA := r[:32]
	p3 := append(mac(g.ClaveAuth(), "invitado", nA, nI), pubDecl...)
	p3 = append(p3, ed25519.Sign(firma, msgFirmaI(g.pub, nI, nA, pubDecl))...)
	_, err := c.Write(p3)
	return err
}

// cerrada: el anfitrión ha cortado (lectura con EOF / error en < 5 s).
func cerrada(t *testing.T, c net.Conn) {
	t.Helper()
	c.SetReadDeadline(time.Now().Add(5 * time.Second))
	_, err := c.Read(make([]byte, 1))
	if err == nil {
		t.Fatal("el anfitrión no ha cerrado")
	}
	var ne net.Error
	if errors.As(err, &ne) && ne.Timeout() {
		t.Fatal("el anfitrión no ha cerrado (sigue esperando)")
	}
}

// ---------------------------------------------------------------- T2: identidad firmada del invitado

func TestClaveInvitadoPersistente(t *testing.T) {
	dir := pruebas.TempDir(t)
	k1, nueva, err := CargarClaveInvitado(dir)
	if err != nil || !nueva {
		t.Fatalf("primera vez: %v nueva=%v", err, nueva)
	}
	k2, nueva, err := CargarClaveInvitado(dir)
	if err != nil || nueva || !bytes.Equal(k1, k2) {
		t.Fatalf("segunda vez debe ser la misma: %v nueva=%v", err, nueva)
	}
	// Dañada: error y NO se pisa (se perdería la identidad)
	p := filepath.Join(dir, NombreClaveInvitado)
	os.WriteFile(p, []byte("corta"), 0o600)
	if _, _, err := CargarClaveInvitado(dir); err == nil {
		t.Fatal("aceptó una clave dañada")
	}
	if b, _ := os.ReadFile(p); string(b) != "corta" {
		t.Fatal("pisó la clave dañada")
	}
	if IDInvitado(k1.Public().(ed25519.PublicKey)) == IDInvitado(claveNueva(t).Public().(ed25519.PublicKey)) {
		t.Fatal("dos claves con el mismo id")
	}
}

// El anfitrión saca el id de la clave pública probada, no de lo que diga el invitado.
func TestIDDeLaClave(t *testing.T) {
	a, g, r := nuevoAnfitrion(t)
	k := claveNueva(t)
	ses := conectar(t, a, g, k)
	eco(t, ses)
	if ips := ipsDe(t, a, k, 1); ips[0] != "127.0.0.20" {
		t.Fatalf("primera IP %v", ips)
	}
	id := IDInvitado(k.Public().(ed25519.PublicKey))
	r.contiene(t, false, fmt.Sprintf("invitado %x dentro", id))
}

// Otro invitado (con el código) dice ser la víctima (su pública) pero firma con su clave: rechazado.
func TestSuplantarInvitadoRechazado(t *testing.T) {
	a, g, r := nuevoAnfitrion(t)
	victima, atacante := claveNueva(t), claveNueva(t)
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	if err := saludoCrudo(c2, g, victima.Public().(ed25519.PublicKey), atacante); err != nil {
		t.Fatal(err)
	}
	cerrada(t, c2)
	r.contiene(t, false, "firma de identidad que no vale")
	if n := len(a.delMismoID(IDInvitado(victima.Public().(ed25519.PublicKey)))); n != 0 {
		t.Fatalf("el impostor tiene %d túnel(es) con el id de la víctima", n)
	}
}

// Una firma hecha para OTRA sala (otra clave de anfitrión) no vale aquí aunque los nonces coincidieran: el mensaje
// firmado lleva la pública del anfitrión. Y una firma correcta de la propia clave sí entra (control).
func TestFirmaLigadaALaSala(t *testing.T) {
	if bytes.Equal(msgFirmaI([]byte("A"), []byte("n"), []byte("m"), []byte("p")),
		msgFirmaI([]byte("B"), []byte("n"), []byte("m"), []byte("p"))) {
		t.Fatal("el mensaje firmado no depende de la sala")
	}
	a, g, _ := nuevoAnfitrion(t)
	k := claveNueva(t)
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	if err := saludoCrudo(c2, g, k.Public().(ed25519.PublicKey), k); err != nil {
		t.Fatal(err)
	}
	ipsDe(t, a, k, 1)
	c2.Close()
}

// Un JoinServer antiguo (saludo v2 o v1) no entra y el anfitrión lo dice en el log y en la ventana.
func TestJoinServerAntiguo(t *testing.T) {
	for _, v := range []string{"v2", "v1"} {
		a, _, r := nuevoAnfitrion(t)
		c1, c2 := net.Pipe()
		go a.Servir(c1, "prueba")
		go c2.Write(append([]byte("YO2T"+v[1:]), make([]byte, 32)...))
		cerrada(t, c2)
		r.contiene(t, false, "JoinServer ANTIGUO (saludo "+v+")")
		r.contiene(t, true, "JoinServer ANTIGUO ("+v+")")
	}
}

// ---------------------------------------------------------------- T1: IPs de origen

func TestIPsNoSeRepiten(t *testing.T) {
	a := &Anfitrion{}
	vistas := map[string]bool{}
	var ts []*tunel
	for i := 0; i < ipMaxDef-ipMinDef+1; i++ { // 230 invitados distintos
		var id [16]byte
		rand.Read(id[:])
		x := &tunel{id: id}
		if err := a.asignarIP(x); err != nil {
			t.Fatalf("invitado %d: %v", i, err)
		}
		if vistas[x.ip] {
			t.Fatalf("invitado %d repite la IP %s", i, x.ip)
		}
		vistas[x.ip] = true
		ts = append(ts, x)
	}
	// El n.º 231 ya NO recibe la del 1.º (antes: 20 + 230 % 230 = .20): se rechaza.
	var id [16]byte
	rand.Read(id[:])
	if err := a.asignarIP(&tunel{id: id}); !errors.Is(err, ErrSinIPs) {
		t.Fatalf("esperaba ErrSinIPs, %v", err)
	}
	// Se va uno: su IP no pasa a OTRO durante la cuarentena...
	a.liberarIP(ts[0])
	if err := a.asignarIP(&tunel{id: id}); !errors.Is(err, ErrSinIPs) {
		t.Fatalf("dio a otro una IP recién liberada: %v", err)
	}
	// ...pero el mismo invitado sí la recupera al momento (reconexión).
	vuelve := &tunel{id: ts[0].id}
	if err := a.asignarIP(vuelve); err != nil || vuelve.ip != ts[0].ip {
		t.Fatalf("la reconexión no recupera su IP: %v %s != %s", err, vuelve.ip, ts[0].ip)
	}
}

func TestCuarentena(t *testing.T) {
	a := &Anfitrion{ipMin: 20, ipMax: 21, cuarentena: 100 * time.Millisecond}
	x, y, z := &tunel{id: [16]byte{1}}, &tunel{id: [16]byte{2}}, &tunel{id: [16]byte{3}}
	if a.asignarIP(x) != nil || a.asignarIP(y) != nil || x.ip == y.ip {
		t.Fatal("dos primeras")
	}
	a.liberarIP(x)
	a.liberarIP(x) // idempotente
	if err := a.asignarIP(z); !errors.Is(err, ErrSinIPs) {
		t.Fatalf("cuarentena no respetada: %v", err)
	}
	time.Sleep(150 * time.Millisecond)
	if err := a.asignarIP(z); err != nil || z.ip != x.ip {
		t.Fatalf("pasada la cuarentena debía dar %s: %v %s", x.ip, err, z.ip)
	}
	// x vuelve: su IP ya es de z, así que recibe otra (no la comparte)
	a.liberarIP(y)
	time.Sleep(150 * time.Millisecond)
	x2 := &tunel{id: x.id}
	if err := a.asignarIP(x2); err != nil || x2.ip == z.ip {
		t.Fatalf("x comparte IP con z: %v %s", err, x2.ip)
	}
}

// Dos túneles VIVOS con el mismo id: cada uno con su IP, y aviso de id duplicado.
func TestMismoIDDosTunelesVivos(t *testing.T) {
	a, g, r := nuevoAnfitrion(t)
	k := claveNueva(t)
	s1 := conectar(t, a, g, k)
	eco(t, s1)
	s2 := conectar(t, a, g, k)
	eco(t, s2)
	ips := ipsDe(t, a, k, 2)
	if ips[0] == ips[1] {
		t.Fatalf("dos túneles vivos con la misma IP %s", ips[0])
	}
	r.contiene(t, false, "AVISO id duplicado")
	r.contiene(t, true, "dos veces a la vez")
	// Al irse uno, el otro sigue con la suya
	s1.Close()
	ipsDe(t, a, k, 1)
	eco(t, s2)
}

// Reconexión: el túnel anterior del mismo id está colgado (no contesta al ping): se cierra y el nuevo se queda con
// SU IP (run.py le sigue viendo igual), sin aviso de duplicado.
func TestReconexionTunelColgado(t *testing.T) {
	a, g, r := nuevoAnfitrion(t)
	a.esperaPing = 200 * time.Millisecond
	k := claveNueva(t)
	// Túnel 1: saludo correcto y luego el invitado «desaparece» (no lee: el ping del anfitrión no tiene respuesta)
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	if err := saludoCrudo(c2, g, k.Public().(ed25519.PublicKey), k); err != nil {
		t.Fatal(err)
	}
	ip1 := ipsDe(t, a, k, 1)[0]
	s2 := conectar(t, a, g, k)
	eco(t, s2)
	ips := ipsDe(t, a, k, 1)
	if ips[0] != ip1 {
		t.Fatalf("la reconexión debía recuperar %s, tiene %s", ip1, ips[0])
	}
	r.contiene(t, false, "no contesta: se cierra")
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, l := range r.log {
		if strings.Contains(l, "id duplicado") {
			t.Fatal("aviso de duplicado en una reconexión normal")
		}
	}
	c2.Close()
}

// ---------------------------------------------------------------- consola que no bloquea

// ventanaCongelada: un io.Writer que no vuelve hasta que se suelta (como la consola en «Seleccionar»).
type ventanaCongelada struct {
	suelta chan struct{}
	mu     sync.Mutex
	buf    bytes.Buffer
}

func (v *ventanaCongelada) Write(p []byte) (int, error) {
	<-v.suelta
	v.mu.Lock()
	defer v.mu.Unlock()
	return v.buf.Write(p)
}

func (v *ventanaCongelada) texto() string {
	v.mu.Lock()
	defer v.mu.Unlock()
	return v.buf.String()
}

func TestAvisoNoBloquea(t *testing.T) {
	v := &ventanaCongelada{suelta: make(chan struct{})}
	c := NuevaConsola(v, 4)
	t0 := time.Now()
	for i := 0; i < 1000; i++ {
		c.Aviso(fmt.Sprintf("aviso %d", i))
	}
	if d := time.Since(t0); d > time.Second {
		t.Fatalf("1000 avisos con la ventana congelada tardaron %v", d)
	}
	if c.Descartadas() == 0 {
		t.Fatal("con el búfer lleno debían descartarse")
	}
	close(v.suelta) // la ventana vuelve
	c.Vaciar(5 * time.Second)
	c.Linea("después")
	if !c.Vaciar(5 * time.Second) {
		t.Fatal("no se vació")
	}
	if s := v.texto(); !strings.Contains(s, "no se pudieron enseñar") || !strings.Contains(s, "después") ||
		!strings.Contains(s, "aviso 0") {
		t.Fatalf("salida: %q", s)
	}
}

// El incidente del 07-10, de punta a punta: con la ventana del anfitrión congelada, un invitado entra y se le atiende.
func TestInvitadoEntraConVentanaCongelada(t *testing.T) {
	v := &ventanaCongelada{suelta: make(chan struct{})}
	defer close(v.suelta)
	con := NuevaConsola(v, 2)
	s := anfitrionDePrueba(t)
	g, _ := Parse(s.Codigo())
	r := &registro{}
	a := &Anfitrion{Sala: s, Logf: r.logf, LogW: io.Discard, Aviso: func(m string) { con.Aviso(m) }}
	for i := 0; i < 4; i++ { // más invitados que el búfer de la ventana
		ses := conectar(t, a, g, claveNueva(t))
		eco(t, ses)
	}
	r.contiene(t, false, "dentro, sale como 127.0.0.23")
}

// Salas por idioma 1:1: 5800-5876 cruzan el túnel; la consola de admin (5799) y lo demás, no.
func TestPuertosSalasPorIdioma(t *testing.T) {
	for _, p := range []int{5730, 5745, 5750, 5760, 5800, 5838, 5876, 8730} {
		if !permitido(p) {
			t.Errorf("el puerto %d debería cruzar el túnel", p)
		}
	}
	for _, p := range []int{22, 5733, 5751, 5799, 5877, 8790, 8791, 3389} { // 8790 = panel, 8791 = API de gestión (11-10)
		if permitido(p) {
			t.Errorf("el puerto %d NO debería cruzar el túnel", p)
		}
	}
}

// ---------------------------------------------------------------- rechazo explícito (T4)

// Un invitado de la lista (saludo público) llega a un anfitrión que ahora es PRIVADO: antes el anfitrión cerraba a secas
// y el invitado se quedaba en «Buscando al anfitrión» (y probaba Tor minutos); ahora recibe el motivo al momento.
func TestRechazoPublicoEnSalaPrivada(t *testing.T) {
	a, _, r := nuevoAnfitrion(t) // a.Publica == nil: privado
	pub := a.Sala.Publica()      // lo que el invitado sacó de la ficha cuando aún era público
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	t0 := time.Now()
	ses, err := Entrar(c2, pub, claveNueva(t), io.Discard)
	if ses != nil {
		ses.Close()
		t.Fatal("entró en una sala privada con el saludo público")
	}
	var rz *ErrRechazado
	if !errors.As(err, &rz) || !errors.Is(err, ErrPrivado) {
		t.Fatalf("debía ser un rechazo «privado»: %v", err)
	}
	if errors.Is(err, ErrLleno) {
		t.Fatal("un rechazo por privado no es «lleno»")
	}
	if d := time.Since(t0); d > 2*time.Second {
		t.Fatalf("tardó %v en enterarse", d)
	}
	r.contiene(t, false, "sala privada: rechazado")
}

// Servidor lleno: el invitado entra al saludo, pero el rechazo llega justo después y se sabe (Rechazo) en vez de un
// simple «túnel roto».
func TestRechazoServidorLleno(t *testing.T) {
	a, g, _ := nuevoAnfitrion(t)
	a.MaxInvitados = 1
	k1 := claveNueva(t)
	eco(t, conectar(t, a, g, k1))
	ipsDe(t, a, k1, 1)
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	ses, cr, err := entrar(c2, g, claveNueva(t), io.Discard)
	if err != nil {
		t.Fatalf("el saludo sí se hace: %v", err)
	}
	defer ses.Close()
	var rz *ErrRechazado
	fin := time.Now().Add(3 * time.Second)
	for time.Now().Before(fin) && cr.Rechazo() == nil {
		time.Sleep(10 * time.Millisecond)
	}
	if rz = cr.Rechazo(); rz == nil || !errors.Is(rz, ErrLleno) {
		t.Fatalf("debía llegar el rechazo «lleno»: %v", rz)
	}
	if _, err := AbrirPuerto(ses, 0); err == nil {
		// el túnel está roto: abrir un puerto no debe funcionar mucho rato
		time.Sleep(200 * time.Millisecond)
		if !ses.IsClosed() {
			t.Fatal("la sesión debía haberse cerrado")
		}
	}
}

// Un invitado normal no se ve afectado: lo primero que llega en yamux no empieza por «YO2NO».
func TestEntradaNormalNoTocaElRechazo(t *testing.T) {
	a, g, _ := nuevoAnfitrion(t)
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	ses, cr, err := entrar(c2, g, claveNueva(t), io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	defer ses.Close()
	eco(t, ses)
	if cr.Rechazo() != nil {
		t.Fatal("no hubo rechazo")
	}
}

// Un anfitrión antiguo que simplemente cierra sigue dando el error de siempre (no un rechazo).
func TestCierreASecasNoEsRechazo(t *testing.T) {
	g := anfitrionDePrueba(t)
	c1, c2 := net.Pipe()
	go func() { io.ReadFull(c1, make([]byte, len(magia)+32)); c1.Close() }()
	_, err := Entrar(c2, g, claveNueva(t), io.Discard)
	var rz *ErrRechazado
	if err == nil || errors.As(err, &rz) {
		t.Fatalf("un cierre sin más no es un rechazo explícito: %v", err)
	}
}
