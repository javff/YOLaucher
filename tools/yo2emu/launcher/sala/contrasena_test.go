package sala

// Pruebas del modo contraseña (contrasena.go): saludo «YO2W3», límite de intentos, compatibilidad con lanzadores viejos
// y el código de sala (que entra sin contraseña).

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net"
	"strings"
	"testing"
	"time"
)

// kdfRapido: Argon2id barato para que las pruebas no tarden (las dos puntas leen los mismos parámetros).
func kdfRapido(t *testing.T) {
	t.Helper()
	tm, mem, hi := kdfTiempo, kdfMemoria, kdfHilos
	kdfTiempo, kdfMemoria, kdfHilos = 1, 64, 1
	t.Cleanup(func() { kdfTiempo, kdfMemoria, kdfHilos = tm, mem, hi })
}

// limites: cambia los límites de intentos durante la prueba.
func limites(t *testing.T, porID, global float64, ventana time.Duration) {
	t.Helper()
	a, b, c := intentosPorID, intentosGlobal, ventanaIntentos
	intentosPorID, intentosGlobal, ventanaIntentos = porID, global, ventana
	t.Cleanup(func() { intentosPorID, intentosGlobal, ventanaIntentos = a, b, c })
}

// anfitrionConClave: servidor público con contraseña; devuelve además la sala pública que ve un invitado (de la ficha)
// y la privada (código).
func anfitrionConClave(t *testing.T, contrasena string) (*Anfitrion, *Sala, *Sala, *registro) {
	t.Helper()
	a, gPriv, r := nuevoAnfitrion(t)
	a.Publica = a.Sala.Publica()
	if contrasena != "" {
		cs, err := NuevaClaveServidor(contrasena, a.Sala.pub)
		if err != nil {
			t.Fatal(err)
		}
		a.Clave = cs
	}
	gPub, err := SalaPublica(a.Sala.pub, a.Publica.secreto[:])
	if err != nil {
		t.Fatal(err)
	}
	return a, gPub, gPriv, r
}

// intentar: un saludo completo; devuelve la sesión (o nil) y el error.
func intentar(a *Anfitrion, g *Sala, clave []byte) (net.Conn, error) {
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	ses, err := Entrar(c2, g, clave, io.Discard)
	if err != nil {
		c2.Close()
		return nil, err
	}
	st, err := AbrirPuerto(ses, 0)
	if err != nil {
		ses.Close()
		return nil, err
	}
	return st, nil
}

func TestContrasenaBuena(t *testing.T) {
	kdfRapido(t)
	a, gPub, _, r := anfitrionConClave(t, "duelo 2008 ñ")
	cred := NuevaCredencial("duelo 2008 ñ")
	k := claveNueva(t)
	eco(t, conectar(t, a, gPub.ConCredencial(cred), k))
	ipsDe(t, a, k, 1)
	ver, sal, clave, ok := cred.Derivada()
	if !ok || ver != kdfV1 || !bytes.Equal(sal, a.Clave.Sal) || !bytes.Equal(clave, a.Clave.Clave) {
		t.Fatal("la clave derivada del invitado no es la del anfitrión")
	}
	// reconexión con la clave guardada (sin la contraseña): entra sin volver a derivar
	eco(t, conectar(t, a, gPub.ConCredencial(CredencialGuardada(ver, sal, clave)), k))
	for _, x := range r.log {
		if strings.Contains(x, "duelo 2008") {
			t.Fatal("la contraseña sale en el log")
		}
	}
}

func TestContrasenaMala(t *testing.T) {
	kdfRapido(t)
	a, gPub, _, r := anfitrionConClave(t, "secreta")
	_, err := intentar(a, gPub.ConCredencial(NuevaCredencial("Secreta")), claveNueva(t))
	var rz *ErrRechazado
	if !errors.As(err, &rz) || !errors.Is(err, ErrContrasena) {
		t.Fatalf("debía ser «contraseña incorrecta»: %v", err)
	}
	if a.Invitados() != 0 {
		t.Fatal("entró con la contraseña mala")
	}
	r.contiene(t, false, "contraseña incorrecta")
}

func TestContrasenaVacia(t *testing.T) {
	kdfRapido(t)
	if _, err := NuevaClaveServidor("", make([]byte, 32)); err == nil {
		t.Fatal("el anfitrión no puede poner una contraseña vacía")
	}
	for _, c := range []string{"abc", strings.Repeat("x", MaxContrasena+1), "con\x00nulo", "bidi‮"} {
		if ValidarContrasena(c) == nil {
			t.Fatalf("contraseña %q aceptada", c)
		}
	}
	a, gPub, _, _ := anfitrionConClave(t, "secreta")
	// el invitado sin contraseña (credencial vacía): ni siquiera manda una prueba, y es «pide contraseña»
	_, err := intentar(a, gPub.ConCredencial(NuevaCredencial("")), claveNueva(t))
	if !errors.Is(err, ErrPideContrasena) {
		t.Fatalf("credencial vacía: %v", err)
	}
	if a.Invitados() != 0 {
		t.Fatal("entró sin contraseña")
	}
}

func TestContrasenaFuerzaBrutaLimitada(t *testing.T) {
	kdfRapido(t)
	limites(t, 5, 8, 2*time.Second)
	a, gPub, _, r := anfitrionConClave(t, "secreta")
	k := claveNueva(t)
	for i := 0; i < 5; i++ {
		if _, err := intentar(a, gPub.ConCredencial(NuevaCredencial("mala")), k); !errors.Is(err, ErrContrasena) {
			t.Fatalf("intento %d: %v", i, err)
		}
	}
	// el 6.º en el mismo minuto: rechazado SIN mirar, aunque esta vez la contraseña sea la buena
	if _, err := intentar(a, gPub.ConCredencial(NuevaCredencial("secreta")), k); !errors.Is(err, ErrIntentos) {
		t.Fatalf("6.º intento: %v", err)
	}
	r.contiene(t, false, "demasiados intentos")
	// otro invitado (otra identidad) aún puede: el límite global es 8 y van 5 fallos
	if st, err := intentar(a, gPub.ConCredencial(NuevaCredencial("secreta")), claveNueva(t)); err != nil {
		t.Fatalf("otro invitado con la buena: %v", err)
	} else {
		st.Close()
	}
	// cambiar de identidad en cada intento no sirve: tras 3 fallos más (8 en total) nadie puede probar
	for i := 0; i < 3; i++ {
		intentar(a, gPub.ConCredencial(NuevaCredencial("mala")), claveNueva(t))
	}
	if _, err := intentar(a, gPub.ConCredencial(NuevaCredencial("secreta")), claveNueva(t)); !errors.Is(err, ErrIntentos) {
		t.Fatalf("límite global: %v", err)
	}
	// pasado el minuto (aquí 2 s) se puede volver a probar
	time.Sleep(2100 * time.Millisecond)
	st, err := intentar(a, gPub.ConCredencial(NuevaCredencial("secreta")), k)
	if err != nil {
		t.Fatalf("tras esperar: %v", err)
	}
	st.Close()
}

func TestContrasenaAnfitrionSinContrasena(t *testing.T) {
	kdfRapido(t)
	a, gPub, _, _ := anfitrionConClave(t, "") // público SIN contraseña
	// la ficha del invitado decía «password» (el anfitrión la quitó después): entra igual, con cualquier cosa
	st, err := intentar(a, gPub.ConCredencial(NuevaCredencial("lo que sea")), claveNueva(t))
	if err != nil {
		t.Fatalf("YO2W3 en un público sin contraseña: %v", err)
	}
	st.Close()
	st, err = intentar(a, gPub, claveNueva(t)) // y el saludo público de siempre
	if err != nil {
		t.Fatal(err)
	}
	st.Close()
}

func TestContrasenaInvitadoViejo(t *testing.T) {
	kdfRapido(t)
	a, gPub, _, r := anfitrionConClave(t, "secreta")
	// un lanzador viejo: lee la ficha (no conoce «password», la ignora) y saluda con «YO2P3»
	var vieja struct {
		Secreto string `json:"secreto"`
	}
	ff, err := FirmarFicha(a.Publica, make([]byte, lenNonceFicha), Ficha{Nombre: "X", Max: 8, Contrasena: true})
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(ff.Datos, &vieja); err != nil || vieja.Secreto == "" {
		t.Fatalf("ficha para un lanzador viejo: %v", err)
	}
	c1, c2 := net.Pipe()
	go a.Servir(c1, "prueba")
	t0 := time.Now()
	ses, err := Entrar(c2, gPub, claveNueva(t), io.Discard)
	if ses != nil {
		ses.Close()
		t.Fatal("un lanzador viejo entró sin contraseña")
	}
	var rz *ErrRechazado
	if !errors.As(err, &rz) || !errors.Is(err, ErrPideContrasena) {
		t.Fatalf("debía ser rechazo «pide contraseña»: %v", err)
	}
	if d := time.Since(t0); d > 2*time.Second {
		t.Fatalf("tardó %v", d)
	}
	if a.Invitados() != 0 {
		t.Fatal("hay túnel")
	}
	r.contiene(t, false, "SIN contraseña en un servidor con contraseña")
	// lo que ve un invitado viejo es un rechazo de motivo desconocido para él (no se queda colgado ni prueba Tor)
	if !strings.Contains(rz.Error(), "pide contraseña") {
		t.Fatal(rz.Error())
	}
}

func TestContrasenaCodigoEntraSinElla(t *testing.T) {
	kdfRapido(t)
	a, _, gPriv, _ := anfitrionConClave(t, "secreta")
	k := claveNueva(t)
	eco(t, conectar(t, a, gPriv, k)) // el código de sala es el secreto fuerte: no pide contraseña
	ipsDe(t, a, k, 1)
}

func TestContrasenaFichaFirmada(t *testing.T) {
	a := anfitrionDePrueba(t)
	nonce := make([]byte, lenNonceFicha)
	ff, err := FirmarFicha(a.Publica(), nonce, Ficha{Nombre: "Con clave", Max: 8, Contrasena: true})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(ff.Datos), `"password":true`) {
		t.Fatalf("la ficha no anuncia la contraseña: %s", ff.Datos)
	}
	v, err := VerificarFicha(ff.ID, nonce, ff.Datos, ff.Firma)
	if err != nil || !v.Ficha.Contrasena {
		t.Fatalf("verificar: %v %+v", err, v)
	}
	// quitar el «password» de la ficha (para que parezca abierta) rompe la firma
	sin := bytes.Replace(ff.Datos, []byte(`,"password":true`), nil, 1)
	if _, err := VerificarFicha(ff.ID, nonce, sin, ff.Firma); err == nil {
		t.Fatal("una ficha retocada pasó la firma")
	}
	// sin contraseña el campo ni aparece (las fichas de siempre no cambian)
	ff2, _ := FirmarFicha(a.Publica(), nonce, Ficha{Nombre: "Abierto", Max: 8})
	if strings.Contains(string(ff2.Datos), "password") {
		t.Fatalf("ficha sin contraseña: %s", ff2.Datos)
	}
}

// Un impostor con todo lo público (pública del anfitrión + secreto público) no recibe la prueba: el invitado comprueba
// la firma del paso 2 antes de derivar la clave.
func TestContrasenaImpostorNoRecibePrueba(t *testing.T) {
	kdfRapido(t)
	a, gPub, _, r := anfitrionConClave(t, "secreta")
	imp := anfitrionDePrueba(t)
	falso := &Anfitrion{Sala: imp, Publica: &Sala{pub: a.Sala.pub, priv: imp.priv, secreto: a.Publica.secreto, publica: true},
		Clave: a.Clave, Logf: r.logf, LogW: io.Discard, Aviso: r.aviso}
	cred := NuevaCredencial("secreta")
	c1, c2 := net.Pipe()
	go falso.Servir(c1, "prueba")
	if _, err := Entrar(c2, gPub.ConCredencial(cred), claveNueva(t), io.Discard); !errors.Is(err, ErrImpostor) {
		t.Fatalf("impostor no detectado: %v", err)
	}
	if _, _, _, ok := cred.Derivada(); ok {
		t.Fatal("se derivó la clave para un impostor")
	}
}

// Lo guardado con «Remember password» deja de valer si el anfitrión cambia la contraseña (sal nueva): se pide otra vez.
func TestContrasenaGuardadaYCambiada(t *testing.T) {
	kdfRapido(t)
	a, gPub, _, _ := anfitrionConClave(t, "vieja1")
	cred := NuevaCredencial("vieja1")
	st, err := intentar(a, gPub.ConCredencial(cred), claveNueva(t))
	if err != nil {
		t.Fatal(err)
	}
	st.Close()
	ver, sal, k, _ := cred.Derivada()
	nueva, _ := NuevaClaveServidor("nueva2", a.Sala.pub)
	a.Clave = nueva
	if _, err := intentar(a, gPub.ConCredencial(CredencialGuardada(ver, sal, k)), claveNueva(t)); !errors.Is(err, ErrPideContrasena) {
		t.Fatalf("clave guardada de otra contraseña: %v", err)
	}
	// texto para retomar tras actualizar: ida y vuelta
	c2, err := CredencialDeTexto(cred.Texto())
	if err != nil {
		t.Fatal(err)
	}
	if v2, s2, k2, ok := c2.Derivada(); !ok || v2 != ver || !bytes.Equal(s2, sal) || !bytes.Equal(k2, k) {
		t.Fatal("credencial en texto: no vuelve igual")
	}
	if _, err := CredencialDeTexto("v1:xx:yy"); err == nil {
		t.Fatal("texto roto aceptado")
	}
}

// El coste real del KDF (lo que espera un jugador al pulsar Join): orientativo.
func TestContrasenaCosteKDF(t *testing.T) {
	t0 := time.Now()
	if _, err := NuevaClaveServidor("contraseña de prueba", make([]byte, 32)); err != nil {
		t.Fatal(err)
	}
	d := time.Since(t0)
	t.Logf("Argon2id t=%d m=%d KiB p=%d: %v", kdfTiempo, kdfMemoria, kdfHilos, d)
	if d > 5*time.Second {
		t.Fatalf("el KDF tarda demasiado: %v", d)
	}
}
