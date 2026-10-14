package sala

// Servidores con CONTRASEÑA (modo «Password» de la ventana, 12-10; NOTES-red.md §11).
//
// Un servidor con contraseña es un servidor PÚBLICO (sale en la lista, ficha firmada con «password: true») que además
// exige, en el saludo del túnel, la prueba de que el invitado conoce la contraseña. La contraseña no viaja nunca ni se
// guarda en claro:
//
//   - El anfitrión, al ponerla, saca una clave K = Argon2id(contraseña, sal | pública del anfitrión) con una sal aleatoria
//     de 16 bytes, y guarda solo (sal, K) (la ventana protege K con DPAPI). Cambiar la contraseña = sal nueva.
//   - Saludo «YO2W3» (como el público «YO2P3», con otras etiquetas de firma):
//       1. invitado -> "YO2W3" + nonceI(32)
//       2. anfitrión -> nonceA(32) + HMAC(auth pública, "anfitrion"|nI|nA) + versión del KDF (1) + sal (16)
//                       + FIRMA del anfitrión sobre ("yo2online-tunel-v3w anfitrion"|nI|nA|versión|sal)
//          (el invitado comprueba la firma ANTES de calcular nada: solo el anfitrión de verdad ve la prueba)
//       3. invitado -> HMAC(auth pública, "invitado"|nA|nI) + pública del invitado + su firma (T2, etiqueta «v3w»)
//                       + PRUEBA = HMAC-SHA256(K, "yo2online-contrasena-v1 prueba"|pública A|nI|nA|pública I)
//       4. anfitrión -> "YO2SI" 0x00 (dentro) o "YO2NO" + motivo (rechazoClave, rechazoIntentos) y cierra.
//     La prueba va ligada a esta conexión (nonces) y a este invitado (su pública): no se puede reutilizar, y quien la
//     vea no puede sacar la contraseña sin probarlas una a una con Argon2id (lento a propósito).
//   - Fuerza bruta: el anfitrión solo hace un HMAC por intento, pero cuenta los FALLOS: como mucho intentosPorID por
//     minuto por invitado (su id sale de su clave, gratis de cambiar) y intentosGlobal por minuto entre todos (frena a
//     quien cambia de identidad en cada intento). Pasado el límite contesta rechazoIntentos sin mirar la prueba.
//   - Compatibilidad: un lanzador VIEJO que ve la ficha con «password» la ignora y entra con «YO2P3»: el anfitrión con
//     contraseña lo rechaza al primer mensaje (rechazoPideClave) sin abrir nada. El CÓDIGO de sala («YO2T3») sigue
//     valiendo y NO pide contraseña: el código es el secreto fuerte (72 caracteres) que el anfitrión da a quien quiere.

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"golang.org/x/crypto/argon2"
)

const (
	magiaClave = "YO2W3" // saludo de la sala pública con contraseña
	magiaSi    = "YO2SI" // paso 4: dentro (seguido de un 0x00, mismo largo que un rechazo)

	etqFirmaAW = "yo2online-tunel-v3w anfitrion"
	etqFirmaIW = "yo2online-tunel-v3w invitado"
	etqPrueba  = "yo2online-contrasena-v1 prueba"

	LenSalClave = 16
	LenClave    = 32
	kdfV1       = byte(1) // Argon2id t=3, m=64 MiB, p=4

	lenPaso2W = 32 + 32 + 1 + LenSalClave + 64
	lenPaso3W = lenPaso3 + 32

	// límites de la contraseña (en letras)
	MinContrasena = 4
	MaxContrasena = 64

	// motivos de rechazo propios del modo contraseña (los de siempre en tunel.go)
	rechazoClave     byte = 4 // contraseña incorrecta
	rechazoPideClave byte = 5 // el servidor pide contraseña y no se ha dado (lanzador viejo, ficha vieja o contraseña cambiada)
	rechazoIntentos  byte = 6 // demasiados intentos fallidos: esperar
)

// Coste del KDF (variables solo para que las pruebas no tarden: las dos puntas las leen de aquí).
var (
	kdfTiempo       uint32 = 3
	kdfMemoria      uint32 = 64 * 1024 // KiB
	kdfHilos        uint8  = 4
	intentosPorID          = 5.0  // fallos por minuto y por invitado
	intentosGlobal         = 30.0 // fallos por minuto entre todos
	ventanaIntentos        = time.Minute
)

var (
	ErrContrasena     = errors.New("contraseña incorrecta")
	ErrPideContrasena = errors.New("ese servidor pide contraseña")
	ErrIntentos       = errors.New("demasiados intentos con contraseña incorrecta: hay que esperar un minuto")
)

// derivarClave: K = Argon2id(contraseña, sal | pública del anfitrión).
func derivarClave(ver byte, contrasena string, sal, pubA []byte) ([]byte, error) {
	if ver != kdfV1 {
		return nil, fmt.Errorf("versión de KDF %d desconocida (¿lanzador viejo?)", ver)
	}
	s := append(append([]byte{}, sal...), pubA...)
	return argon2.IDKey([]byte(contrasena), s, kdfTiempo, kdfMemoria, kdfHilos, LenClave), nil
}

// ValidarContrasena: lo que se exige a la contraseña de un servidor (texto normal, MinContrasena-MaxContrasena letras).
func ValidarContrasena(c string) error {
	n := utf8.RuneCountInString(c)
	switch {
	case !utf8.ValidString(c) || strings.IndexFunc(c, prohibida) >= 0:
		return errors.New("la contraseña lleva caracteres no permitidos")
	case n < MinContrasena:
		return fmt.Errorf("la contraseña es demasiado corta (mínimo %d)", MinContrasena)
	case n > MaxContrasena:
		return fmt.Errorf("la contraseña es demasiado larga (máximo %d)", MaxContrasena)
	}
	return nil
}

// ---------------------------------------------------------------- anfitrión

// ClaveServidor: lo que guarda el anfitrión de su contraseña (nunca la contraseña).
type ClaveServidor struct {
	Ver   byte
	Sal   []byte // LenSalClave
	Clave []byte // LenClave: K (equivale a la contraseña PARA ESTE servidor: se guarda protegida)
}

// NuevaClaveServidor: la contraseña nueva del anfitrión pub (sal aleatoria).
func NuevaClaveServidor(contrasena string, pub []byte) (*ClaveServidor, error) {
	if err := ValidarContrasena(contrasena); err != nil {
		return nil, err
	}
	sal := make([]byte, LenSalClave)
	if _, err := rand.Read(sal); err != nil {
		return nil, err
	}
	k, err := derivarClave(kdfV1, contrasena, sal, pub)
	if err != nil {
		return nil, err
	}
	return &ClaveServidor{Ver: kdfV1, Sal: sal, Clave: k}, nil
}

// Valida: formato correcto (lo leído de disco).
func (c *ClaveServidor) Valida() bool {
	return c != nil && c.Ver == kdfV1 && len(c.Sal) == LenSalClave && len(c.Clave) == LenClave
}

// limiteIntentos: fallos de contraseña por invitado y globales (cubos que se rellenan en ventanaIntentos).
type limiteIntentos struct {
	mu     sync.Mutex
	porID  map[[16]byte]*cubo
	global cubo
	ini    bool
}

func (l *limiteIntentos) rellenar(c *cubo, cap float64, ahora time.Time) {
	c.fichas = min(cap, c.fichas+ahora.Sub(c.ultimo).Seconds()*cap/ventanaIntentos.Seconds())
	c.ultimo = ahora
}

func (l *limiteIntentos) prep(id [16]byte, ahora time.Time) *cubo {
	if !l.ini {
		l.porID, l.global, l.ini = map[[16]byte]*cubo{}, cubo{fichas: intentosGlobal, ultimo: ahora}, true
	}
	if len(l.porID) > maxCubos {
		l.porID = map[[16]byte]*cubo{} // muchos ids distintos: se olvidan (el límite global sigue)
	}
	c := l.porID[id]
	if c == nil {
		c = &cubo{fichas: intentosPorID, ultimo: ahora}
		l.porID[id] = c
	}
	l.rellenar(c, intentosPorID, ahora)
	l.rellenar(&l.global, intentosGlobal, ahora)
	return c
}

// puede: ¿se le deja intentar? (queda al menos un fallo en su cubo y en el global)
func (l *limiteIntentos) puede(id [16]byte) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	c := l.prep(id, time.Now())
	return c.fichas >= 1 && l.global.fichas >= 1
}

// fallo: apunta un intento fallido.
func (l *limiteIntentos) fallo(id [16]byte) {
	l.mu.Lock()
	defer l.mu.Unlock()
	c := l.prep(id, time.Now())
	c.fichas = max(0, c.fichas-1)
	l.global.fichas = max(0, l.global.fichas-1)
}

func msgFirmaAW(nI, nA []byte, ver byte, sal []byte) []byte {
	m := append([]byte(etqFirmaAW), nI...)
	m = append(m, nA...)
	m = append(m, ver)
	return append(m, sal...)
}

func msgFirmaIW(pubA, nI, nA, pubI []byte) []byte {
	m := append([]byte(etqFirmaIW), pubA...)
	m = append(m, nI...)
	m = append(m, nA...)
	return append(m, pubI...)
}

func pruebaClave(k, pubA, nI, nA, pubI []byte) []byte {
	return mac(k, etqPrueba, pubA, nI, nA, pubI)
}

// ---------------------------------------------------------------- invitado

// Credencial: la contraseña que el jugador escribió (o la clave K que guardó con «Remember password») para UN servidor.
// Se calcula K una vez por sal y se recuerda en memoria (reconexiones sin volver a pasar por Argon2id).
type Credencial struct {
	mu         sync.Mutex
	contrasena string
	ver        byte
	sal        []byte
	clave      []byte
}

// NuevaCredencial: con la contraseña escrita.
func NuevaCredencial(contrasena string) *Credencial { return &Credencial{contrasena: contrasena} }

// CredencialGuardada: con la clave guardada de otra vez (sin la contraseña). Si la sal del anfitrión ya no es esa (cambió
// la contraseña), el saludo para con ErrPideContrasena.
func CredencialGuardada(ver byte, sal, clave []byte) *Credencial {
	return &Credencial{ver: ver, sal: append([]byte{}, sal...), clave: append([]byte{}, clave...)}
}

// Derivada: la clave K y su sal (para «Remember password» y para retomar tras actualizar). ok=false si aún no se ha
// usado en un saludo (no se sabe la sal).
func (c *Credencial) Derivada() (ver byte, sal, clave []byte, ok bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.clave == nil {
		return 0, nil, nil, false
	}
	return c.ver, append([]byte{}, c.sal...), append([]byte{}, c.clave...), true
}

// TieneContrasena: la escribió el jugador en esta sesión (no viene de lo guardado).
func (c *Credencial) TieneContrasena() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.contrasena != ""
}

func (c *Credencial) claveParaSal(ver byte, sal, pubA []byte) ([]byte, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.clave != nil && c.ver == ver && bytes.Equal(c.sal, sal) {
		return c.clave, nil
	}
	if c.contrasena == "" {
		return nil, &ErrRechazado{Motivo: rechazoPideClave} // lo guardado es de una contraseña anterior
	}
	k, err := derivarClave(ver, c.contrasena, sal, pubA)
	if err != nil {
		return nil, err
	}
	c.ver, c.sal, c.clave = ver, append([]byte{}, sal...), k
	return k, nil
}

// ConCredencial: la misma sala pública, entrando con contraseña (saludo «YO2W3»). Solo salas públicas.
func (s *Sala) ConCredencial(c *Credencial) *Sala {
	if !s.publica || c == nil {
		return s
	}
	x := *s
	x.cred = c
	return &x
}

// ConContrasena: ¿esta sala entra con contraseña?
func (s *Sala) ConContrasena() bool { return s.cred != nil }

// ---------------------------------------------------------------- texto (retomar tras actualizar)

// Texto / CredencialDeTexto: la clave derivada en una línea («v1:<sal>:<clave>», base64) para pasarla por el entorno
// al proceso nuevo tras una autoactualización (como el código de sala: nunca por argumentos ni al log).
func (c *Credencial) Texto() string {
	ver, sal, k, ok := c.Derivada()
	if !ok {
		return ""
	}
	e := base64.RawStdEncoding
	return fmt.Sprintf("v%d:%s:%s", ver, e.EncodeToString(sal), e.EncodeToString(k))
}

func CredencialDeTexto(t string) (*Credencial, error) {
	p := strings.Split(t, ":")
	e := base64.RawStdEncoding
	if len(p) != 3 || p[0] != "v1" {
		return nil, errors.New("credencial mal formada")
	}
	sal, err1 := e.DecodeString(p[1])
	k, err2 := e.DecodeString(p[2])
	if err1 != nil || err2 != nil || len(sal) != LenSalClave || len(k) != LenClave {
		return nil, errors.New("credencial mal formada")
	}
	return CredencialGuardada(kdfV1, sal, k), nil
}
