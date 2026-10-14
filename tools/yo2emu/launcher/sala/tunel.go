package sala

// Protocolo del túnel (igual por P2P y por Tor). Sobre UN flujo ya cifrado (libp2p Noise/TLS o circuito Tor):
//   1. invitado -> anfitrión: "YO2T3" + nonceI(32)
//   2. anfitrión -> invitado: nonceA(32) + HMAC(auth, "anfitrion"|nonceI|nonceA)
//                             + FIRMA ed25519 del anfitrión sobre ("yo2online-tunel-v3 anfitrion"|nonceI|nonceA)
//      El invitado comprueba la firma con la clave pública del código: solo el anfitrión de verdad puede firmar (los
//      invitados conocen el secreto de entrada, pero no su clave privada). Si no vale, el invitado corta.
//   3. invitado -> anfitrión: HMAC(auth, "invitado"|nonceA|nonceI) + clave PÚBLICA ed25519 del invitado (32)
//                             + FIRMA del invitado sobre ("yo2online-tunel-v3 invitado"|pubAnfitrion|nonceI|nonceA|pubInvitado)
//      El anfitrión comprueba el HMAC (¿tiene el código?) y la firma (¿tiene la clave de ese id?); el id del invitado
//      lo calcula él con IDInvitado(pública): ya no lo dice el invitado (T2, ver identidad_invitado.go).
//   Sala pública CON CONTRASEÑA: saludo «YO2W3», con la sal de la contraseña en el paso 2, la prueba (HMAC con la clave
//   derivada) en el paso 3 y un paso 4 «YO2SI»/«YO2NO»: ver contrasena.go.
//   (v1 = "YO2T1", sin firmas; v2 = "YO2T2", el invitado mandaba un id de 16 bytes sin probar nada: ya no se aceptan;
//   el anfitrión los apunta en el log y en su ventana como «JoinServer antiguo».)
//   Habla PRIMERO el invitado a propósito: tor.exe en Windows deja en el búfer (y no reenvía nunca) lo que el servicio
//   escribe mientras su conexión de salida aún figura como «connecting» (probado con tor 0.4.9.13).
//   4. yamux encima (el anfitrión es el servidor). Cada conexión del juego = un stream yamux que empieza con
//      2 bytes big-endian: el puerto LÓGICO (5730, 5731, ... 8730). 0 = eco (para medir la latencia);
//      1 y 2 = autoactualización (manifiesto y descarga, ver actualiza.go).
//   Rechazo explícito (T4, 11-10): si el anfitrión NO va a dejar entrar a este invitado, en vez de cerrar a secas le escribe
//   "YO2NO" + 1 byte de motivo (rechazo*) y cierra. El invitado lo lee en lugar del paso 2 (o, si el rechazo llega después
//   de su paso 3, en lugar de los primeros bytes de yamux: ver conRechazo) y se detiene AL MOMENTO con un mensaje concreto
//   (ErrRechazado), sin probar Tor ni reintentar. Un invitado antiguo, que no conoce esto, ve lo de siempre: un cierre.
// El anfitrión solo deja pasar los puertos del juego (nunca 5799, la consola de admin de run.py).
//
// Cada túnel vivo sale hacia run.py desde su propia 127.0.0.X (run.py distingue a los jugadores por la IP de origen):
// ver asignarIP (T1).

import (
	"bytes"
	"crypto/ed25519"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/hashicorp/yamux"
)

const (
	magia      = "YO2T3"
	magiaPub   = "YO2P3" // sala PÚBLICA (lista pública): igual que YO2T3 con el secreto de la ficha; ver NOTES-red.md §8
	magiaV2    = "YO2T2"
	magiaV1    = "YO2T1"
	etqFirmaA  = "yo2online-tunel-v3 anfitrion"
	etqFirmaI  = "yo2online-tunel-v3 invitado"
	etqFirmaAP = "yo2online-tunel-v3p anfitrion" // las mismas, para la sala pública (no se mezclan con las privadas)
	etqFirmaIP = "yo2online-tunel-v3p invitado"
	lenPaso2   = 32 + 32 + ed25519.SignatureSize
	lenPaso3   = 32 + ed25519.PublicKeySize + ed25519.SignatureSize
	maxPorID   = 8 // túneles vivos a la vez con el mismo id (un JoinServer normal tiene 1; el 07-10 se vieron 9 por la consola congelada)
	ipMinDef   = 20
	ipMaxDef   = 249
	cuarentDef = 5 * time.Minute

	magiaNo = "YO2NO" // rechazo explícito: "YO2NO" + motivo (1 byte)
	// motivos del rechazo
	rechazoPrivado  byte = 1 // la sala ahora es privada: sin el código no se entra (saludo público en un servidor privado)
	rechazoLleno    byte = 2 // no caben más invitados
	rechazoSinSitio byte = 3 // ya tiene demasiados túneles de ese invitado o no quedan IPs libres
)

// ErrRechazado: el anfitrión rechazó la entrada a propósito y lo dijo (definitivo: no se reintenta ni se prueba otro camino).
type ErrRechazado struct{ Motivo byte }

func (e *ErrRechazado) Error() string {
	switch e.Motivo {
	case rechazoPrivado:
		return "el anfitrión rechaza la entrada: ese servidor ahora es privado (hace falta su código)"
	case rechazoLleno:
		return "el anfitrión rechaza la entrada: servidor lleno"
	case rechazoSinSitio:
		return "el anfitrión rechaza la entrada: no tiene sitio para otra conexión ahora mismo"
	case rechazoClave:
		return "el anfitrión rechaza la entrada: contraseña incorrecta"
	case rechazoPideClave:
		return "el anfitrión rechaza la entrada: el servidor pide contraseña"
	case rechazoIntentos:
		return "el anfitrión rechaza la entrada: demasiados intentos con contraseña incorrecta (esperar un minuto)"
	}
	return fmt.Sprintf("el anfitrión rechaza la entrada (motivo %d)", e.Motivo)
}

// Is: errors.Is(err, ErrLleno) también vale para un rechazo por lleno; ErrPrivado para el privado; ErrContrasena,
// ErrPideContrasena y ErrIntentos para los del modo contraseña.
func (e *ErrRechazado) Is(t error) bool {
	return (t == ErrLleno && e.Motivo == rechazoLleno) || (t == ErrPrivado && e.Motivo == rechazoPrivado) ||
		(t == ErrContrasena && e.Motivo == rechazoClave) || (t == ErrPideContrasena && e.Motivo == rechazoPideClave) ||
		(t == ErrIntentos && e.Motivo == rechazoIntentos)
}

// SinSitio: el rechazo es «no tengo sitio para otra conexión» (demasiados túneles de ese invitado o sin IPs libres).
func (e *ErrRechazado) SinSitio() bool { return e.Motivo == rechazoSinSitio }

// ErrPrivado: el servidor al que se entra con el saludo público ahora es privado.
var ErrPrivado = errors.New("el servidor ahora es privado")

// rechazar dice al invitado por qué no entra (best effort: si no se puede escribir, ya se cierra).
func (a *Anfitrion) rechazar(c io.Writer, motivo byte) {
	c.Write(append([]byte(magiaNo), motivo))
}

// conRechazo envuelve el flujo del invitado para yamux: si lo primero que llega es un rechazo (YO2NO + motivo; un
// paquete yamux empieza por la versión 0, nunca por 'Y'), lo guarda y deja la sesión sin datos (EOF).
type conRechazo struct {
	io.ReadWriteCloser
	primero sync.Once
	motivo  atomic.Int32 // 0 = nada
}

func (c *conRechazo) Read(p []byte) (int, error) {
	var n int
	var err error
	hecho := false
	c.primero.Do(func() {
		hecho = true
		n, err = c.ReadWriteCloser.Read(p)
		if n >= len(magiaNo) && string(p[:len(magiaNo)]) == magiaNo {
			if n > len(magiaNo) {
				c.motivo.Store(int32(p[len(magiaNo)]))
			} else {
				c.motivo.Store(-1)
			}
			n, err = 0, io.EOF
		}
	})
	if hecho {
		return n, err
	}
	return c.ReadWriteCloser.Read(p)
}

// Rechazo: el rechazo que llegó tras la entrada, o nil.
func (c *conRechazo) Rechazo() *ErrRechazado {
	if m := c.motivo.Load(); m != 0 {
		if m < 0 {
			m = 0
		}
		return &ErrRechazado{Motivo: byte(m)}
	}
	return nil
}

func msgFirmaA(nI, nA []byte) []byte { return msgFirmaAde(false, nI, nA) }

func msgFirmaI(pubA, nI, nA, pubI []byte) []byte { return msgFirmaIde(false, pubA, nI, nA, pubI) }

func msgFirmaAde(publica bool, nI, nA []byte) []byte {
	etq := etqFirmaA
	if publica {
		etq = etqFirmaAP
	}
	return append(append([]byte(etq), nI...), nA...)
}

func msgFirmaIde(publica bool, pubA, nI, nA, pubI []byte) []byte {
	etq := etqFirmaI
	if publica {
		etq = etqFirmaIP
	}
	m := append([]byte(etq), pubA...)
	m = append(m, nI...)
	m = append(m, nA...)
	return append(m, pubI...)
}

// Puertos del juego que cruzan el túnel (los mismos que el ACL de Tailscale).
// 5800-5876: salas por idioma 1:1 (lobby.ROOM_PER_LANG de run.py; la sala mundial sigue en 5740-5750). Si la bandera
// está apagada nadie escucha ahí y el reenvío falla sin más. NUNCA 5799 (consola de admin de run.py).
func Puertos() []int {
	p := []int{5730, 5731, 5732, 5760, 8730}
	for i := 5740; i <= 5750; i++ {
		p = append(p, i)
	}
	for i := 5800; i <= 5876; i++ {
		p = append(p, i)
	}
	return p
}

func permitido(p int) bool {
	for _, x := range Puertos() {
		if x == p {
			return true
		}
	}
	return false
}

func mac(clave []byte, etiqueta string, partes ...[]byte) []byte {
	m := hmac.New(sha256.New, clave)
	m.Write([]byte(etiqueta))
	for _, p := range partes {
		m.Write(p)
	}
	return m.Sum(nil)
}

func confYamux(logw io.Writer) *yamux.Config {
	c := yamux.DefaultConfig()
	c.KeepAliveInterval = 15 * time.Second
	c.ConnectionWriteTimeout = 30 * time.Second
	c.StreamOpenTimeout = 30 * time.Second
	// Ventana de recepción de hasta 4 MiB (por defecto 256 KiB): con la latencia de Tor (~300 ms) 256 KiB limitan una
	// descarga a <1 MB/s (autoactualización). Solo cambia cuánto se deja enviar sin confirmar: compatible con versiones viejas.
	c.MaxStreamWindowSize = 4 << 20
	c.LogOutput = logw
	return c
}

// ---------------------------------------------------------------- anfitrión

type Anfitrion struct {
	Sala   *Sala
	Offset int // YO2_PORT_OFFSET de la instancia de run.py a la que se reenvía
	Logf   func(string, ...any)
	LogW   io.Writer
	// Aviso: mensajes para la ventana del owner. Se llama SIEMPRE después de Logf y TIENE QUE volver enseguida
	// (OpenServer usa sala.Consola, que no bloquea aunque la ventana esté en «Seleccionar»; ver consola.go).
	Aviso func(string)
	// Catalogo: lo que se reparte por autoactualización (nil = nada; los puertos 1/2 se cierran sin contestar).
	Catalogo *Catalogo
	// Paquete: la versión firmada del paquete que se reparte por el puerto 3 (paquete_red.go; nil = nada: el stream se
	// cierra sin contestar, como un anfitrión antiguo).
	Paquete *ServidorPaquete
	// Publica: la sala pública (Sala.Publica()) si este servidor sale en la lista pública; nil = privado (lo de
	// siempre): un saludo público («YO2P3») se rechaza. Con sala pública, el código privado sigue valiendo.
	Publica *Sala
	// MaxInvitados: invitados DISTINTOS (por id) a la vez; 0 = sin límite. Uno que ya tiene túnel puede reconectar.
	MaxInvitados int
	// Clave: contraseña del servidor (solo con Publica; contrasena.go). nil = sin contraseña. Con ella la entrada pública
	// exige el saludo «YO2W3» con la prueba; el código privado («YO2T3») sigue entrando sin contraseña.
	Clave    *ClaveServidor
	intentos limiteIntentos

	// T1: IPs de origen hacia run.py (ver asignarIP).
	mu       sync.Mutex
	vivos    map[string]*tunel    // IP -> túnel vivo que sale por ella (nunca dos túneles con la misma IP)
	ultimaIP map[[16]byte]string  // id -> la última IP que tuvo (se le devuelve si está libre: estable entre reconexiones)
	duenoIP  map[string][16]byte  // IP -> último id que la tuvo
	liberada map[string]time.Time // IP -> cuándo quedó libre
	// Rango 127.0.0.ipMin..ipMax (0 = 20..249; la .1 es el propio owner y las .2-.19 sus clientes locales de prueba)
	// y cuarentena antes de dar a OTRO invitado una IP que acaba de quedar libre (0 = 5 min). Cambian en las pruebas.
	ipMin, ipMax int
	cuarentena   time.Duration
	esperaPing   time.Duration // 0 = 5 s
	ultAntiguo   atomic.Int64  // última vez (unix) que se avisó en la ventana de un JoinServer antiguo
}

// tunel: un invitado conectado (una sesión yamux).
type tunel struct {
	id  [16]byte
	ip  string
	via string
	ses *yamux.Session
}

// ErrSinIPs: todas las 127.0.0.X del rango están en uso (o acaban de quedar libres y eran de otro invitado).
var ErrSinIPs = errors.New("no quedan IPs libres para invitados")

func (a *Anfitrion) rango() (int, int) {
	if a.ipMin == 0 {
		return ipMinDef, ipMaxDef
	}
	return a.ipMin, a.ipMax
}

// asignarIP (T1). Antes era «20 + n % 230»: el invitado n.º 231 repetía la IP del 1.º, y dos túneles con el mismo id
// compartían IP sin aviso. Ahora:
//   - una IP nunca la tienen dos túneles vivos a la vez (aunque traigan el mismo id);
//   - el mismo invitado recupera su última IP si está libre (reconexiones: run.py lo sigue viendo igual);
//   - a un invitado distinto se le da primero una IP nunca usada y, si no quedan, la que lleve más tiempo libre, pero
//     nunca una que era de OTRO y quedó libre hace menos de `cuarentena` (para que no herede lo que run.py tenga atado
//     a la IP del anterior, p. ej. la reserva de duelo de 90 s de PROPUESTA.md §4);
//   - si no hay ninguna: ErrSinIPs (el túnel se rechaza y se dice en el log).
func (a *Anfitrion) asignarIP(t *tunel) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.vivos == nil {
		a.vivos, a.ultimaIP = map[string]*tunel{}, map[[16]byte]string{}
		a.duenoIP, a.liberada = map[string][16]byte{}, map[string]time.Time{}
	}
	cuar := a.cuarentena
	if cuar == 0 {
		cuar = cuarentDef
	}
	ip, ok := a.ultimaIP[t.id]
	if !ok || a.vivos[ip] != nil {
		ip = ""
		var mejorT time.Time
		min, max := a.rango()
		for n := min; n <= max; n++ {
			c := "127.0.0." + strconv.Itoa(n)
			if a.vivos[c] != nil {
				continue
			}
			dueno, usada := a.duenoIP[c]
			if !usada {
				ip = c // nunca usada: la mejor
				break
			}
			lt := a.liberada[c]
			if dueno != t.id && time.Since(lt) < cuar {
				continue
			}
			if ip == "" || lt.Before(mejorT) {
				ip, mejorT = c, lt
			}
		}
		if ip == "" {
			return ErrSinIPs
		}
		if viejo, ok := a.duenoIP[ip]; ok && viejo != t.id && a.ultimaIP[viejo] == ip {
			delete(a.ultimaIP, viejo) // esa IP ya es de otro: si el viejo vuelve, recibirá otra
		}
	}
	t.ip = ip
	a.vivos[ip] = t
	a.ultimaIP[t.id] = ip
	a.duenoIP[ip] = t.id
	return nil
}

// liberarIP: el túnel se ha cerrado (idempotente: solo libera si la IP sigue siendo de ESE túnel).
func (a *Anfitrion) liberarIP(t *tunel) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if t.ip != "" && a.vivos[t.ip] == t {
		delete(a.vivos, t.ip)
		a.liberada[t.ip] = time.Now()
	}
}

// Invitados: cuántos invitados distintos (por id) tienen túnel vivo ahora.
func (a *Anfitrion) Invitados() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	ids := map[[16]byte]bool{}
	for _, t := range a.vivos {
		ids[t.id] = true
	}
	return len(ids)
}

// delMismoID: túneles vivos con ese id.
func (a *Anfitrion) delMismoID(id [16]byte) []*tunel {
	a.mu.Lock()
	defer a.mu.Unlock()
	var r []*tunel
	for _, t := range a.vivos {
		if t.id == id {
			r = append(r, t)
		}
	}
	return r
}

// contesta: ¿la sesión responde a un ping de yamux en d? (un túnel «colgado» de una reconexión no contesta)
func contesta(ses *yamux.Session, d time.Duration) bool {
	if ses == nil || ses.IsClosed() {
		return false
	}
	r := make(chan error, 1)
	go func() { _, err := ses.Ping(); r <- err }()
	select {
	case err := <-r:
		return err == nil
	case <-time.After(d):
		return false
	}
}

// revisarDuplicados: si ya hay túneles vivos con este id, cierra los que no contestan (reconexión: el JoinServer
// perdió el anterior y el anfitrión aún no se había enterado) y devuelve cuántos siguen vivos de verdad.
func (a *Anfitrion) revisarDuplicados(id [16]byte, via string) int {
	espera := a.esperaPing
	if espera == 0 {
		espera = 5 * time.Second
	}
	var vivos atomic.Int32
	var wg sync.WaitGroup
	for _, d := range a.delMismoID(id) {
		wg.Add(1)
		go func() { // a la vez: si hay varios colgados, no se suman las esperas
			defer wg.Done()
			if contesta(d.ses, espera) {
				vivos.Add(1)
				return
			}
			a.Logf("[%s] invitado %x: su túnel anterior (%s, %s) no contesta: se cierra y su IP queda para el nuevo", via, id, d.via, d.ip)
			d.ses.Close()
			a.liberarIP(d)
		}()
	}
	wg.Wait()
	return int(vivos.Load())
}

func (a *Anfitrion) avisarAntiguo(via, version string) {
	a.Logf("[%s] entrada con un JoinServer ANTIGUO (saludo %s): rechazado; hay que darle el JoinServer nuevo a mano "+
		"(este OpenServer exige la identidad firmada del invitado, saludo v3)", via, version)
	ahora := time.Now().Unix()
	if prev := a.ultAntiguo.Load(); ahora-prev >= 60 && a.ultAntiguo.CompareAndSwap(prev, ahora) {
		a.Aviso("Un invitado con un JoinServer ANTIGUO (" + version + ") ha intentado entrar y no puede: necesita el " +
			"JoinServer.exe nuevo (launcher\\dist), copiado a mano.")
	}
}

// Servir atiende a UN invitado sobre un flujo ya establecido (stream libp2p o conexión del .onion).
func (a *Anfitrion) Servir(c io.ReadWriteCloser, via string) {
	defer c.Close()
	if d, ok := c.(interface{ SetReadDeadline(time.Time) error }); ok {
		d.SetReadDeadline(time.Now().Add(30 * time.Second))
		defer d.SetReadDeadline(time.Time{})
	}
	hola := make([]byte, len(magia)+32)
	_, err := io.ReadFull(c, hola)
	sl := a.Sala // la sala con la que se saluda: la privada (código) o la pública (lista)
	conClave := err == nil && bytes.Equal(hola[:len(magiaClave)], []byte(magiaClave))
	if err == nil && (conClave || bytes.Equal(hola[:len(magiaPub)], []byte(magiaPub))) {
		if a.Publica == nil {
			a.Logf("[%s] entrada PÚBLICA (sin código) en una sala privada: rechazado", via)
			a.rechazar(c, rechazoPrivado) // que el invitado lo sepa al momento (antes se quedaba esperando, y luego probaba Tor)
			return
		}
		if !conClave && a.Clave != nil {
			// lanzador viejo (no sabe de contraseñas) o ficha vieja: no se abre nada sin la prueba
			a.Logf("[%s] entrada pública SIN contraseña en un servidor con contraseña: rechazado", via)
			a.rechazar(c, rechazoPideClave)
			return
		}
		sl = a.Publica
	} else if err != nil || !bytes.Equal(hola[:len(magia)], []byte(magia)) {
		switch {
		case err == nil && bytes.Equal(hola[:len(magiaV2)], []byte(magiaV2)):
			a.avisarAntiguo(via, "v2")
		case err == nil && bytes.Equal(hola[:len(magiaV1)], []byte(magiaV1)):
			a.avisarAntiguo(via, "v1")
		default:
			a.Logf("[%s] saludo inválido: %v", via, err)
		}
		return
	}
	nI := hola[len(magia):]
	nA := make([]byte, 32)
	rand.Read(nA)
	r2 := append(append([]byte{}, nA...), mac(sl.ClaveAuth(), "anfitrion", nI, nA)...)
	if conClave {
		// sal y versión del KDF de la contraseña (firmadas). Un servidor público SIN contraseña también contesta a «YO2W3»
		// (la ficha del invitado era de cuando tenía): sal al azar y la prueba no se mira.
		cs := a.Clave
		if !cs.Valida() {
			cs = &ClaveServidor{Ver: kdfV1, Sal: make([]byte, LenSalClave)}
			rand.Read(cs.Sal)
		}
		r2 = append(append(r2, cs.Ver), cs.Sal...)
		r2 = append(r2, sl.Firmar(msgFirmaAW(nI, nA, cs.Ver, cs.Sal))...)
	} else {
		r2 = append(r2, sl.Firmar(msgFirmaAde(sl.publica, nI, nA))...)
	}
	if _, err := c.Write(r2); err != nil {
		return
	}
	resp := make([]byte, lenPaso3)
	if conClave {
		resp = make([]byte, lenPaso3W)
	}
	if _, err := io.ReadFull(c, resp); err != nil {
		a.Logf("[%s] handshake: %v", via, err)
		return
	}
	if !hmac.Equal(resp[:32], mac(sl.ClaveAuth(), "invitado", nA, nI)) {
		a.Logf("[%s] intento de entrada con una clave de sala incorrecta: rechazado", via)
		return
	}
	pubI := ed25519.PublicKey(resp[32 : 32+ed25519.PublicKeySize])
	msgI := msgFirmaIde(sl.publica, sl.pub, nI, nA, pubI)
	if conClave {
		msgI = msgFirmaIW(sl.pub, nI, nA, pubI)
	}
	if !ed25519.Verify(pubI, msgI, resp[32+ed25519.PublicKeySize:lenPaso3]) {
		a.Logf("[%s] invitado con el código de sala pero con una firma de identidad que no vale: rechazado", via)
		return
	}
	id := IDInvitado(pubI)
	if conClave {
		if a.Clave.Valida() {
			if !a.intentos.puede(id) {
				a.Logf("[%s] invitado %x: demasiados intentos con contraseña incorrecta: rechazado sin mirar", via, id)
				a.rechazar(c, rechazoIntentos)
				return
			}
			if !hmac.Equal(resp[lenPaso3:], pruebaClave(a.Clave.Clave, sl.pub, nI, nA, pubI)) {
				a.intentos.fallo(id)
				a.Logf("[%s] invitado %x: contraseña incorrecta: rechazado", via, id)
				a.rechazar(c, rechazoClave)
				return
			}
		}
		if _, err := c.Write(append([]byte(magiaSi), 0)); err != nil {
			return
		}
	}
	if a.MaxInvitados > 0 && len(a.delMismoID(id)) == 0 && a.Invitados() >= a.MaxInvitados {
		a.Logf("[%s] invitado %x RECHAZADO: servidor lleno (%d invitados, máximo %d)", via, id, a.Invitados(), a.MaxInvitados)
		a.rechazar(c, rechazoLleno)
		return
	}
	if d, ok := c.(interface{ SetReadDeadline(time.Time) error }); ok {
		d.SetReadDeadline(time.Time{})
	}
	ses, err := yamux.Server(c, confYamux(a.LogW))
	if err != nil {
		a.Logf("[%s] yamux: %v", via, err)
		return
	}
	defer ses.Close()

	// T1: una IP propia para ESTE túnel (nunca compartida con otro túnel vivo, aunque sea del mismo id).
	dup := a.revisarDuplicados(id, via)
	if dup >= maxPorID {
		a.Logf("[%s] invitado %x: ya tiene %d túneles vivos a la vez: se rechaza este", via, id, dup)
		a.rechazar(c, rechazoSinSitio)
		return
	}
	t := &tunel{id: id, via: via, ses: ses}
	if err := a.asignarIP(t); err != nil {
		min, max := a.rango()
		a.Logf("[%s] invitado %x RECHAZADO: %v (las 127.0.0.%d-%d están en uso o eran de otro invitado y quedaron libres "+
			"hace menos de la cuarentena)", via, id, err, min, max)
		a.Aviso(fmt.Sprintf("Invitado %x NO ha podido entrar: no quedan IPs libres para invitados (mira el log).", id[:3]))
		a.rechazar(c, rechazoSinSitio)
		return
	}
	defer a.liberarIP(t)
	// (se cuenta otra vez tras asignar: si llegan dos a la vez, ninguno veía al otro al revisar)
	if dup = len(a.delMismoID(id)) - 1; dup > 0 {
		a.Logf("[%s] AVISO id duplicado: el invitado %x ya tiene %d túnel(es) vivo(s) que contestan; a este se le da OTRA IP (%s) "+
			"para que run.py no los mezcle. Si no es un segundo JoinServer suyo, alguien tiene su clave (invitado-ed25519.key)",
			via, id, dup, t.ip)
		a.Aviso(fmt.Sprintf("AVISO: el invitado %x ha entrado dos veces a la vez (el segundo sale como %s): mira el log.", id[:3], t.ip))
	}
	// Primero el log y luego la ventana (que nunca bloquea: consola.go).
	a.Logf("[%s] invitado %x dentro, sale como %s", via, id, t.ip)
	a.Aviso(fmt.Sprintf("Invitado %x conectado por %s (en run.py se le ve como %s)", id[:3], via, t.ip))
	for {
		st, err := ses.AcceptStream()
		if err != nil {
			a.Logf("[%s] invitado %x fuera (%s): %v", via, id, t.ip, err)
			a.Aviso(fmt.Sprintf("Invitado %x desconectado (%s)", id[:3], via))
			return
		}
		go a.flujo(st, t.ip, via)
	}
}

func (a *Anfitrion) flujo(st net.Conn, ip, via string) {
	defer st.Close()
	var cab [2]byte
	st.SetReadDeadline(time.Now().Add(20 * time.Second))
	if _, err := io.ReadFull(st, cab[:]); err != nil {
		return
	}
	st.SetReadDeadline(time.Time{})
	p := int(binary.BigEndian.Uint16(cab[:]))
	if p == 0 { // eco
		io.Copy(st, st)
		return
	}
	if p == PuertoPaquete {
		if a.Paquete != nil {
			a.Paquete.Atender(st, ip+" ("+via+")")
		}
		return
	}
	if p == PuertoManifiesto || p == PuertoDescarga {
		if a.Catalogo != nil {
			a.Catalogo.Servir(st, p, ip+" ("+via+")")
		}
		return
	}
	if !permitido(p) {
		a.Logf("[%s] puerto %d no permitido", via, p)
		return
	}
	d := net.Dialer{Timeout: 10 * time.Second, LocalAddr: &net.TCPAddr{IP: net.ParseIP(ip)}}
	dst := net.JoinHostPort("127.0.0.1", strconv.Itoa(p+a.Offset))
	r, err := d.Dial("tcp", dst)
	if err != nil {
		a.Logf("[%s] dial %s desde %s: %v", via, dst, ip, err)
		return
	}
	defer r.Close()
	Unir(st, r)
}

// ---------------------------------------------------------------- invitado

// ErrImpostor: quien contesta conoce el código pero NO tiene la clave del anfitrión (otro invitado haciéndose pasar).
var ErrImpostor = errors.New("¡quien contesta NO es el anfitrión de esta sala! (conoce el código pero no tiene su clave: " +
	"posible suplantación por otro invitado). No se conecta")

// Entrar: handshake del invitado sobre un flujo recién abierto; devuelve la sesión yamux. clave = la identidad de este
// invitado (CargarClaveInvitado): con ella firma el saludo y el anfitrión saca de su pública el id.
func Entrar(c io.ReadWriteCloser, s *Sala, clave ed25519.PrivateKey, logw io.Writer) (*yamux.Session, error) {
	ses, _, err := entrar(c, s, clave, logw)
	return ses, err
}

// entrar: lo mismo que Entrar, y además devuelve el envoltorio que recoge un rechazo tardío del anfitrión (el que llega
// después del paso 3: servidor lleno, sin sitio).
func entrar(c io.ReadWriteCloser, s *Sala, clave ed25519.PrivateKey, logw io.Writer) (*yamux.Session, *conRechazo, error) {
	if len(clave) != ed25519.PrivateKeySize {
		return nil, nil, errors.New("falta la clave de identidad del invitado")
	}
	if d, ok := c.(interface{ SetDeadline(time.Time) error }); ok {
		d.SetDeadline(time.Now().Add(45 * time.Second))
		defer d.SetDeadline(time.Time{})
	}
	nI := make([]byte, 32)
	rand.Read(nI)
	m := magia
	conClave := s.publica && s.cred != nil
	switch {
	case conClave:
		m = magiaClave
	case s.publica:
		m = magiaPub
	}
	if _, err := c.Write(append([]byte(m), nI...)); err != nil {
		return nil, nil, err
	}
	r := make([]byte, lenPaso2)
	if conClave {
		r = make([]byte, lenPaso2W)
	}
	// primero los 6 bytes de un posible rechazo explícito (YO2NO + motivo): el paso 2 es mucho más largo
	if _, err := io.ReadFull(c, r[:len(magiaNo)+1]); err == nil && string(r[:len(magiaNo)]) == magiaNo {
		return nil, nil, &ErrRechazado{Motivo: r[len(magiaNo)]}
	}
	if _, err := io.ReadFull(c, r[len(magiaNo)+1:]); err != nil {
		return nil, nil, fmt.Errorf("el anfitrión no contesta al saludo: su OpenServer es de otra versión (este JoinServer "+
			"necesita el OpenServer del 07-10 o posterior; avisa al anfitrión) o se ha cortado la conexión: %w", err)
	}
	nA := r[:32]
	if !hmac.Equal(r[32:64], mac(s.ClaveAuth(), "anfitrion", nI, nA)) {
		return nil, nil, errors.New("el anfitrión no demuestra tener el código de sala")
	}
	pubI := clave.Public().(ed25519.PublicKey)
	var prueba []byte
	if conClave {
		ver, sal := r[64], r[65:65+LenSalClave]
		// primero quién es: la prueba solo se calcula (y se manda) al anfitrión de verdad
		if !s.Verificar(msgFirmaAW(nI, nA, ver, sal), r[65+LenSalClave:]) {
			return nil, nil, ErrImpostor
		}
		k, err := s.cred.claveParaSal(ver, sal, s.pub)
		if err != nil {
			return nil, nil, err
		}
		prueba = pruebaClave(k, s.pub, nI, nA, pubI)
	} else if !s.Verificar(msgFirmaAde(s.publica, nI, nA), r[64:]) {
		return nil, nil, ErrImpostor
	}
	p3 := append(mac(s.ClaveAuth(), "invitado", nA, nI), pubI...)
	if conClave {
		p3 = append(p3, ed25519.Sign(clave, msgFirmaIW(s.pub, nI, nA, pubI))...)
		p3 = append(p3, prueba...)
	} else {
		p3 = append(p3, ed25519.Sign(clave, msgFirmaIde(s.publica, s.pub, nI, nA, pubI))...)
	}
	if _, err := c.Write(p3); err != nil {
		return nil, nil, err
	}
	if conClave { // paso 4: dentro o rechazo (contraseña mala, demasiados intentos)
		r4 := make([]byte, len(magiaNo)+1)
		if _, err := io.ReadFull(c, r4); err != nil {
			return nil, nil, fmt.Errorf("el anfitrión cortó tras la contraseña: %w", err)
		}
		switch string(r4[:len(magiaNo)]) {
		case magiaNo:
			return nil, nil, &ErrRechazado{Motivo: r4[len(magiaNo)]}
		case magiaSi:
		default:
			return nil, nil, errors.New("respuesta desconocida del anfitrión tras la contraseña")
		}
	}
	if d, ok := c.(interface{ SetDeadline(time.Time) error }); ok {
		d.SetDeadline(time.Time{})
	}
	cr := &conRechazo{ReadWriteCloser: c}
	ses, err := yamux.Client(cr, confYamux(logw))
	return ses, cr, err
}

// AbrirPuerto abre un stream hacia el puerto lógico p del anfitrión.
func AbrirPuerto(ses *yamux.Session, p int) (net.Conn, error) {
	st, err := ses.OpenStream()
	if err != nil {
		return nil, err
	}
	var cab [2]byte
	binary.BigEndian.PutUint16(cab[:], uint16(p))
	if _, err := st.Write(cab[:]); err != nil {
		st.Close()
		return nil, err
	}
	return st, nil
}

// MedirEco: ida y vuelta de 32 bytes por un stream de eco (lo que nota el juego: incluye yamux y el transporte).
func MedirEco(ses *yamux.Session, n int) (min, media time.Duration, err error) {
	st, err := AbrirPuerto(ses, 0)
	if err != nil {
		return 0, 0, err
	}
	defer st.Close()
	buf := make([]byte, 32)
	var tot time.Duration
	for i := 0; i < n; i++ {
		t := time.Now()
		if _, err = st.Write(buf); err != nil {
			return
		}
		if _, err = io.ReadFull(st, buf); err != nil {
			return
		}
		d := time.Since(t)
		tot += d
		if i == 0 || d < min {
			min = d
		}
	}
	return min, tot / time.Duration(n), nil
}

// Unir copia en los dos sentidos con cierre a medias.
func Unir(a, b net.Conn) {
	type cw interface{ CloseWrite() error }
	var wg sync.WaitGroup
	cp := func(dst, src net.Conn) {
		defer wg.Done()
		io.Copy(dst, src)
		if x, ok := dst.(cw); ok {
			x.CloseWrite()
		} else {
			dst.Close()
		}
	}
	wg.Add(2)
	go cp(a, b)
	go cp(b, a)
	wg.Wait()
}
