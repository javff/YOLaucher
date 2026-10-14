package sala

// Lista pública de servidores SIN infraestructura propia (NOTES-red.md §8).
//
//   - Punto de encuentro: CIDLista(), un CID fijo y versionado (sha256 de "yo2online-lista-publica-v1"). Cada anfitrión
//     PÚBLICO lo anuncia (Provide) en la DHT pública de IPFS, igual que el CID de su sala, y lo re-anuncia con él. Un
//     anfitrión privado no lo anuncia nunca. El lanzador hace FindProvidersAsync(CIDLista()) y a cada proveedor le pide
//     su ficha.
//   - Ficha: protocolo libp2p ProtoFicha. El lanzador escribe "YF1" + nonce(16); el anfitrión contesta
//     u16 BE longitud + JSON de la Ficha (como mucho MaxFicha bytes) + FIRMA ed25519 (64) sobre
//     ("yo2online-ficha-v1" 0x00 | peer ID del anfitrión | nonce | JSON) y cierra.
//     La firma es de la MISMA clave que el peer ID (identidad.key): libp2p ya garantiza con quién se habla, y la firma
//     lo vuelve a comprobar sin depender del transporte (como el saludo del túnel). Copiar la ficha de otro no sirve: va
//     ligada a SU peer ID y al nonce de esta petición.
//   - La ficha lleva el secreto de entrada de la sala PÚBLICA de ese anfitrión (Sala.Publica()): con él y su clave
//     pública se entra sin código (saludo «YO2P3», tunel.go). El código privado no sale nunca en la ficha.

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/ipfs/go-cid"
	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/core/host"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/core/protocol"
	"github.com/multiformats/go-multihash"
)

const (
	ProtoFicha = protocol.ID("/yo2online/ficha/1.0.0")

	etqLista      = "yo2online-lista-publica-v1"
	etqFicha      = "yo2online-ficha-v1"
	pedidoFicha   = "YF1"
	lenNonceFicha = 16

	// MaxFicha: tamaño máximo del JSON de una ficha (una normal ocupa ~400 bytes).
	MaxFicha = 2048
	// VersionJuegoCliente: la única versión del cliente que soportan los servidores (yo2.exe Ver.080912).
	VersionJuegoCliente = "080912"

	ReglasVanilla  = "vanilla"
	ReglasMejorado = "mejorado"
	CuentasLibre   = "libre"    // la primera vez que alguien entra se le crea la cuenta (lo de siempre)
	CuentasCerrada = "cerradas" // solo las que crea el anfitrión (necesita una bandera en run.py: ver NOTES-red.md §8.5)

	maxNombre      = 40
	maxDescripcion = 200
	maxIdioma      = 8
	maxVersion     = 16
	maxSalas       = 16
	maxSala        = 16
	MaxJugadores   = 1000
)

// CIDLista: el punto de encuentro de la lista pública en la DHT. Cambiar la etiqueta (v2) = otra lista.
func CIDLista() cid.Cid {
	mh, err := multihash.Sum([]byte(etqLista), multihash.SHA2_256, -1)
	if err != nil {
		panic(err)
	}
	return cid.NewCidV1(cid.Raw, mh)
}

// Ficha: lo que un servidor público cuenta de sí mismo en la lista (todo lo pone el anfitrión: no es de fiar salvo
// QUIÉN la firma). Los números los cuenta el anfitrión: un anfitrión puede mentir sobre ellos.
type Ficha struct {
	V               int      `json:"v"`
	Nombre          string   `json:"nombre"`
	Descripcion     string   `json:"descripcion,omitempty"`
	Idioma          string   `json:"idioma"`            // "es", "en", "ja"... o "multi"
	Reglas          string   `json:"reglas"`            // ReglasVanilla | ReglasMejorado
	Cuentas         string   `json:"cuentas,omitempty"` // CuentasLibre | CuentasCerrada
	Jugadores       int      `json:"jugadores"`
	Max             int      `json:"max"`
	Duelos          int      `json:"duelos"` // -1 = no se sabe (run.py aún no lo cuenta)
	VersionJuego    string   `json:"version_juego"`
	Salas           []string `json:"salas,omitempty"`
	VersionLanzador int64    `json:"version_lanzador,omitempty"`
	Secreto         string   `json:"secreto"` // secreto de entrada de la sala PÚBLICA (base32, 12 bytes)
	Emitida         int64    `json:"emitida"` // unix, hora del anfitrión

	// VersionPaquete: la versión FIRMADA del paquete que reparte este anfitrión (paquete_red.go; 0 = ninguna). Un
	// lanzador con una menor se la pide por ProtoActualiza; la firma del editor se comprueba allí, no aquí.
	VersionPaquete int64 `json:"version_paquete,omitempty"`

	// Contrasena: el servidor pide contraseña para entrar desde la lista (saludo «YO2W3», contrasena.go). Va dentro de lo
	// firmado. Un lanzador viejo no lo conoce y no puede entrar sin ella (el anfitrión rechaza su saludo «YO2P3»).
	Contrasena bool `json:"password,omitempty"`
}

// prohibida: caracteres de control, de formato (marcas bidi que dan la vuelta al texto, invisibles...) y U+FFFD.
// Se deja U+200D (unión de emojis).
func prohibida(r rune) bool {
	return unicode.IsControl(r) || (unicode.Is(unicode.Cf, r) && r != 0x200D) || r == utf8.RuneError ||
		unicode.Is(unicode.Co, r)
}

func textoValido(campo, s string, max int, vacio bool) error {
	if !utf8.ValidString(s) {
		return fmt.Errorf("%s: no es UTF-8", campo)
	}
	if n := utf8.RuneCountInString(s); n > max || (!vacio && strings.TrimSpace(s) == "") {
		return fmt.Errorf("%s: longitud %d (máx. %d)", campo, n, max)
	}
	if strings.IndexFunc(s, prohibida) >= 0 {
		return fmt.Errorf("%s: caracteres no permitidos", campo)
	}
	return nil
}

func limpiarTexto(s string, max int) string {
	s = strings.Map(func(r rune) rune {
		if r == '\n' || r == '\r' || r == '\t' {
			return ' '
		}
		if prohibida(r) {
			return -1
		}
		return r
	}, strings.ToValidUTF8(s, ""))
	s = strings.Join(strings.Fields(s), " ")
	if r := []rune(s); len(r) > max {
		s = strings.TrimSpace(string(r[:max]))
	}
	return s
}

// Normalizar: el anfitrión deja la ficha dentro de los límites antes de firmarla (recorta, quita lo prohibido).
func (f *Ficha) Normalizar() {
	f.V = 1
	f.Nombre = limpiarTexto(f.Nombre, maxNombre)
	if f.Nombre == "" {
		f.Nombre = "Servidor sin nombre"
	}
	f.Descripcion = limpiarTexto(f.Descripcion, maxDescripcion)
	f.Idioma = strings.ToLower(limpiarTexto(f.Idioma, maxIdioma))
	if f.Idioma == "" {
		f.Idioma = "multi"
	}
	if f.Reglas != ReglasMejorado {
		f.Reglas = ReglasVanilla
	}
	if f.Cuentas != CuentasCerrada {
		f.Cuentas = CuentasLibre
	}
	f.Max = min(max(f.Max, 1), MaxJugadores)
	f.Jugadores = min(max(f.Jugadores, 0), f.Max)
	f.Duelos = min(max(f.Duelos, -1), MaxJugadores)
	if f.VersionJuego = limpiarTexto(f.VersionJuego, maxVersion); f.VersionJuego == "" {
		f.VersionJuego = VersionJuegoCliente
	}
	var salas []string
	for _, x := range f.Salas {
		if x = limpiarTexto(x, maxSala); x != "" && len(salas) < maxSalas {
			salas = append(salas, x)
		}
	}
	f.Salas = salas
}

// Validar: lo que comprueba el lanzador en cada ficha recibida (una que no cumple se descarta entera).
func (f *Ficha) Validar() error {
	if f.V != 1 {
		return fmt.Errorf("versión de ficha %d desconocida", f.V)
	}
	for _, c := range []struct {
		campo, s string
		max      int
		vacio    bool
	}{{"nombre", f.Nombre, maxNombre, false}, {"descripción", f.Descripcion, maxDescripcion, true},
		{"idioma", f.Idioma, maxIdioma, false}, {"versión del juego", f.VersionJuego, maxVersion, false}} {
		if err := textoValido(c.campo, c.s, c.max, c.vacio); err != nil {
			return err
		}
	}
	if f.Reglas != ReglasVanilla && f.Reglas != ReglasMejorado {
		return errors.New("reglas desconocidas")
	}
	if f.Cuentas != "" && f.Cuentas != CuentasLibre && f.Cuentas != CuentasCerrada {
		return errors.New("modo de cuentas desconocido")
	}
	if f.Max < 1 || f.Max > MaxJugadores || f.Jugadores < 0 || f.Jugadores > f.Max || f.Duelos < -1 || f.Duelos > MaxJugadores {
		return errors.New("números fuera de rango")
	}
	if len(f.Salas) > maxSalas {
		return errors.New("demasiadas salas")
	}
	for _, s := range f.Salas {
		if err := textoValido("sala", s, maxSala, false); err != nil {
			return err
		}
	}
	if b, err := b32.DecodeString(f.Secreto); err != nil || len(b) != lenSecreto {
		return errors.New("secreto de entrada mal formado")
	}
	return nil
}

// FichaFirmada: una ficha tal cual llegó (bytes firmados) y ya comprobada.
type FichaFirmada struct {
	ID    peer.ID
	Nonce []byte
	Datos []byte // el JSON firmado, tal cual
	Firma []byte
	Ficha Ficha
	pub   ed25519.PublicKey
}

func msgFicha(id peer.ID, nonce, datos []byte) []byte {
	m := append([]byte(etqFicha), 0)
	m = append(m, []byte(id)...)
	m = append(m, nonce...)
	return append(m, datos...)
}

// PubDePeer: la clave ed25519 de un peer ID (los de los anfitriones llevan la clave dentro).
func PubDePeer(id peer.ID) (ed25519.PublicKey, error) {
	k, err := id.ExtractPublicKey()
	if err != nil {
		return nil, fmt.Errorf("peer ID sin clave: %w", err)
	}
	if k.Type() != crypto.Ed25519 {
		return nil, errors.New("el peer ID no es de una clave ed25519")
	}
	raw, err := k.Raw()
	if err != nil || len(raw) != ed25519.PublicKeySize {
		return nil, errors.New("clave del peer ID mal formada")
	}
	return ed25519.PublicKey(raw), nil
}

// FirmarFicha (anfitrión): s = su sala pública (con la clave privada).
func FirmarFicha(s *Sala, nonce []byte, f Ficha) (*FichaFirmada, error) {
	if !s.EsAnfitrion() || !s.publica {
		return nil, errors.New("solo se firma la ficha de una sala pública propia")
	}
	f.Normalizar()
	f.Secreto = s.SecretoPublico()
	if f.Emitida == 0 {
		f.Emitida = time.Now().Unix()
	}
	datos, err := json.Marshal(f)
	if err != nil {
		return nil, err
	}
	if len(datos) > MaxFicha {
		return nil, fmt.Errorf("la ficha ocupa %d bytes (máx. %d)", len(datos), MaxFicha)
	}
	id := s.PeerID()
	return &FichaFirmada{ID: id, Nonce: nonce, Datos: datos, Firma: s.Firmar(msgFicha(id, nonce, datos)), Ficha: f, pub: s.pub}, nil
}

// VerificarFicha (lanzador): la firma tiene que ser del peer ID del que llegó y la ficha cumplir los límites.
func VerificarFicha(id peer.ID, nonce, datos, firma []byte) (*FichaFirmada, error) {
	if len(datos) > MaxFicha {
		return nil, errors.New("ficha demasiado grande")
	}
	pub, err := PubDePeer(id)
	if err != nil {
		return nil, err
	}
	if len(firma) != ed25519.SignatureSize || !ed25519.Verify(pub, msgFicha(id, nonce, datos), firma) {
		return nil, errors.New("la firma de la ficha no es de ese servidor (¿suplantación?)")
	}
	var f Ficha
	if err := json.Unmarshal(datos, &f); err != nil {
		return nil, fmt.Errorf("ficha mal formada: %w", err)
	}
	if err := f.Validar(); err != nil {
		return nil, fmt.Errorf("ficha no válida: %w", err)
	}
	return &FichaFirmada{ID: id, Nonce: append([]byte{}, nonce...), Datos: append([]byte{}, datos...),
		Firma: append([]byte{}, firma...), Ficha: f, pub: pub}, nil
}

// Sala: la sala pública de ese servidor, para entrar sin código.
func (ff *FichaFirmada) Sala() (*Sala, error) {
	sec, err := b32.DecodeString(ff.Ficha.Secreto)
	if err != nil {
		return nil, err
	}
	return SalaPublica(ff.pub, sec)
}

// ---------------------------------------------------------------- anfitrión

// ServidorFichas atiende ProtoFicha. Límites contra quien quiera agotar al anfitrión pidiendo fichas: como mucho
// maxSimultaneas a la vez (las demás se cortan), cada par como mucho una petición cada intervaloPar (con ráfaga de
// rafagaPar) y 5 s por petición.
type ServidorFichas struct {
	Sala  *Sala        // la sala PÚBLICA del anfitrión (Sala.Publica())
	Ficha func() Ficha // la ficha de ahora (sin firmar); se llama en cada petición: tiene que ser barata
	Logf  func(string, ...any)

	once               sync.Once
	sem                chan struct{}
	mu                 sync.Mutex
	cubos              map[peer.ID]*cubo
	cortadas, servidas int64
}

type cubo struct {
	fichas float64
	ultimo time.Time
}

var (
	maxSimultaneas = 16
	intervaloPar   = 2 * time.Second
	rafagaPar      = 4.0
	maxCubos       = 4096
)

func (sf *ServidorFichas) permitir(id peer.ID) bool {
	sf.mu.Lock()
	defer sf.mu.Unlock()
	ahora := time.Now()
	if len(sf.cubos) > maxCubos { // muchos pares distintos: se olvida todo (el límite global sigue)
		sf.cubos = map[peer.ID]*cubo{}
	}
	c := sf.cubos[id]
	if c == nil {
		c = &cubo{fichas: rafagaPar, ultimo: ahora}
		sf.cubos[id] = c
	}
	c.fichas = min(rafagaPar, c.fichas+ahora.Sub(c.ultimo).Seconds()/intervaloPar.Seconds())
	c.ultimo = ahora
	if c.fichas < 1 {
		return false
	}
	c.fichas--
	return true
}

// Atender: el manejador del stream (n.H.SetStreamHandler(ProtoFicha, sf.Atender)).
func (sf *ServidorFichas) Atender(st network.Stream) {
	sf.once.Do(func() {
		sf.sem = make(chan struct{}, maxSimultaneas)
		sf.cubos = map[peer.ID]*cubo{}
	})
	select {
	case sf.sem <- struct{}{}:
		defer func() { <-sf.sem }()
	default:
		sf.cortar(st, "demasiadas a la vez")
		return
	}
	if !sf.permitir(st.Conn().RemotePeer()) {
		sf.cortar(st, "demasiadas de "+st.Conn().RemotePeer().String())
		return
	}
	st.SetDeadline(time.Now().Add(5 * time.Second))
	req := make([]byte, len(pedidoFicha)+lenNonceFicha)
	if _, err := io.ReadFull(st, req); err != nil || string(req[:len(pedidoFicha)]) != pedidoFicha {
		st.Reset()
		return
	}
	ff, err := FirmarFicha(sf.Sala, req[len(pedidoFicha):], sf.Ficha())
	if err != nil {
		if sf.Logf != nil {
			sf.Logf("[lista] no se pudo firmar la ficha: %v", err)
		}
		st.Reset()
		return
	}
	resp := binary.BigEndian.AppendUint16(nil, uint16(len(ff.Datos)))
	resp = append(append(resp, ff.Datos...), ff.Firma...)
	if _, err := st.Write(resp); err != nil {
		st.Reset()
		return
	}
	st.Close()
	sf.mu.Lock()
	sf.servidas++
	sf.mu.Unlock()
}

func (sf *ServidorFichas) cortar(st network.Stream, por string) {
	st.Reset()
	sf.mu.Lock()
	sf.cortadas++
	n := sf.cortadas
	sf.mu.Unlock()
	if n&(n-1) == 0 && sf.Logf != nil { // 1, 2, 4, 8...: que no llene el log
		sf.Logf("[lista] petición de ficha cortada (%s); %d cortadas en total", por, n)
	}
}

// ---------------------------------------------------------------- lanzador

// ErrNoPublico: el servidor contesta pero ya no atiende fichas, o sea que ya no está en la lista pública (pasó a privado).
var ErrNoPublico = errors.New("el servidor ya no es público")

// PedirFicha pide la ficha a id (conectando si hace falta, también por relay) en como mucho espera. Devuelve la ficha
// comprobada, el tiempo de ida y vuelta de la petición y si llegó por un relay (entonces el ping es orientativo).
func PedirFicha(ctx context.Context, h host.Host, id peer.ID, espera time.Duration) (*FichaFirmada, time.Duration, bool, error) {
	ctx, cancel := context.WithTimeout(ctx, espera)
	defer cancel()
	if _, err := PubDePeer(id); err != nil { // ni se intenta con un peer ID que no puede firmar fichas
		return nil, 0, false, err
	}
	if err := h.Connect(ctx, peer.AddrInfo{ID: id}); err != nil {
		return nil, 0, false, fmt.Errorf("no conecta: %w", err)
	}
	st, err := h.NewStream(network.WithAllowLimitedConn(ctx, "ficha"), id, ProtoFicha)
	if err != nil {
		// El anfitrión está ahí pero ya no atiende fichas: dejó de ser público (un servidor privado no registra el protocolo).
		if strings.Contains(err.Error(), "protocols not supported") {
			return nil, 0, false, fmt.Errorf("%w: %v", ErrNoPublico, err)
		}
		return nil, 0, false, fmt.Errorf("sin protocolo de ficha: %w", err)
	}
	defer st.Close()
	if d, ok := ctx.Deadline(); ok {
		st.SetDeadline(d)
	}
	// por si ctx se cancela desde fuera (el plazo ya lo cubre SetDeadline)
	parar := context.AfterFunc(ctx, func() { st.Reset() })
	defer parar()
	nonce := make([]byte, lenNonceFicha)
	rand.Read(nonce)
	t0 := time.Now()
	if _, err := st.Write(append([]byte(pedidoFicha), nonce...)); err != nil {
		return nil, 0, false, err
	}
	var cab [2]byte
	if _, err := io.ReadFull(st, cab[:]); err != nil {
		return nil, 0, false, fmt.Errorf("no contesta a tiempo: %w", err)
	}
	rtt := time.Since(t0)
	if rtt <= 0 { // el reloj monótono de Go en Windows avanza a saltos (~0,5 ms): en la misma máquina sale 0
		rtt = time.Microsecond
	}
	n := int(binary.BigEndian.Uint16(cab[:]))
	if n > MaxFicha {
		st.Reset()
		return nil, 0, false, fmt.Errorf("ficha demasiado grande (%d bytes)", n)
	}
	buf := make([]byte, n+ed25519.SignatureSize)
	if _, err := io.ReadFull(st, buf); err != nil {
		return nil, 0, false, fmt.Errorf("ficha incompleta: %w", err)
	}
	ff, err := VerificarFicha(id, nonce, buf[:n], buf[n:])
	if err != nil {
		return nil, 0, false, err
	}
	return ff, rtt, st.Conn().Stat().Limited, nil
}
