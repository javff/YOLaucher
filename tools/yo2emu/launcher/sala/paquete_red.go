package sala

// Propagación P2P del paquete firmado (UPD, 12-10), SIN servidores: cualquier lanzador que tenga una versión firmada
// la reparte mientras hospeda. Dos caminos, el mismo protocolo:
//
//   - libp2p /yo2online/actualiza/1.0.0 en el nodo del anfitrión (sin código de sala: el paquete es público y va
//     firmado). Lo usa la lista: la ficha anuncia version_paquete y un lanzador con una versión menor lo pide.
//   - el túnel de siempre, puerto lógico 3 (PuertoPaquete), al hacer Join (también por Tor). Un anfitrión antiguo no
//     conoce el puerto 3 y cierra el stream: el invitado lo toma como «sin paquete» y sigue con lo de siempre (puertos
//     1/2 de actualiza.go: solo el exe).
//
// Cada petición es un stream: "YU1" + op.
//
//	'V'  versión: u8 estado + i64 BE versión (lo que dice el anfitrión; no es de fiar hasta ver el manifiesto)
//	'M'  manifiesto: u8 estado + gzip(paquete.json tal cual, firmado por el editor)
//	'B'  lote: u16 BE n + n × (u16 BE longitud + ruta del manifiesto) -> u8 estado + gzip(contenido de los n ficheros
//	     seguidos, en ese orden). El invitado sabe cuánto ocupa cada uno por el manifiesto FIRMADO y comprueba el
//	     sha256 de cada uno; un byte de más al final = lote rechazado.
//
// El anfitrión solo da ficheros de SU manifiesto firmado y solo si el fichero del disco tiene el sha256 firmado (si
// alguien lo ha tocado, no se reparte). Límites para que repartir no ahogue al anfitrión: subida total
// tasaSubidaPaquete, como mucho maxLotesSimultaneos lotes a la vez (maxLotesPorPar por par), peticiones por par con
// cubo de fichas y un tope de bytes por par y día.

import (
	"compress/gzip"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/hashicorp/yamux"
	"github.com/libp2p/go-libp2p/core/network"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/libp2p/go-libp2p/core/protocol"
)

const (
	ProtoActualiza = protocol.ID("/yo2online/actualiza/1.0.0")
	PuertoPaquete  = 3

	pedidoPaquete = "YU1"
	opVersion     = 'V'
	opManifiesto  = 'M'
	opLote        = 'B'

	estOK           = 0
	estNoEsta       = 1 // una ruta que no está en el manifiesto (o lote mal formado)
	estOcupado      = 2 // límites del anfitrión: probar más tarde
	estSinPaquete   = 3 // este anfitrión no tiene paquete firmado
	estNoDisponible = 4 // el fichero del disco no es el firmado (o no se puede leer)

	maxLoteFicheros = 4096
	maxLoteBytes    = 160 << 20 // o un solo fichero más grande (el exe)
)

var (
	tasaSubidaPaquete    int64 = 1 << 20 // bytes/s, entre todos: que repartir no ahogue la subida de un ADSL mientras se juega
	maxLotesSimultaneos        = 4
	maxLotesPorPar             = 2
	maxBytesParDia       int64 = 3 << 30
	intervaloPeticionPaq       = time.Second
	rafagaPeticionPaq          = 30.0
)

// ErrSinPaquete: el anfitrión no reparte el paquete (antiguo, o sin versión firmada).
var ErrSinPaquete = errors.New("el anfitrión no reparte el paquete")

// ErrOcupado: el anfitrión ha llegado a sus límites; se reintenta más tarde.
var ErrOcupado = errors.New("el anfitrión está ocupado repartiendo el paquete")

// flujoPaquete: un stream (yamux o libp2p).
type flujoPaquete interface {
	io.ReadWriteCloser
	SetDeadline(time.Time) error
}

// ---------------------------------------------------------------- anfitrión

// ServidorPaquete: lo que reparte este lanzador. Mira en cada petición (barato: solo un Stat si no ha cambiado) la
// carpeta del lanzador (si es un paquete instalado) y la semilla del owner (<datos>\semilla, que deja empaquetar.py
// --firmar en su PC: su lanzador de desarrollo no es un paquete) y reparte la versión firmada más nueva.
type ServidorPaquete struct {
	Raiz    string // carpeta del lanzador (paquete instalado); "" = no
	Exe     string // el exe en marcha: el YO2OnlineLauncher.exe del manifiesto de Raiz es este (se llame como se llame)
	Semilla string // otra carpeta con un paquete firmado completo; "" = no
	Logf    func(string, ...any)

	mu      sync.Mutex
	fuentes map[string]*fuenteLocal
	hashes  map[string]hashCache
	pares   map[string]*estadoPar
	activos int
	cubo    limitador
	avisos  map[string]bool
}

type fuenteLocal struct {
	raiz   string
	inst   bool // es la carpeta del lanzador (el exe se sirve desde Exe)
	tam    int64
	mod    time.Time
	pv     *PaqueteVerificado
	err    error
	comprb time.Time
}

type estadoPar struct {
	fichas  float64
	ultimo  time.Time
	activos int
	bytes   int64
	dia     time.Time
}

func (sp *ServidorPaquete) logf(f string, a ...any) {
	if sp.Logf != nil {
		sp.Logf(f, a...)
	}
}

// avisarUnaVez: lo mismo no se repite en el log en cada petición.
func (sp *ServidorPaquete) avisarUnaVez(clave, f string, a ...any) {
	if sp.avisos == nil {
		sp.avisos = map[string]bool{}
	}
	if !sp.avisos[clave] {
		sp.avisos[clave] = true
		sp.logf(f, a...)
	}
}

// leerFuente: paquete.json de esa carpeta (caché por tamaño y fecha). Se llama con sp.mu cogido.
func (sp *ServidorPaquete) leerFuente(raiz string, inst bool) *fuenteLocal {
	if raiz == "" {
		return nil
	}
	if sp.fuentes == nil {
		sp.fuentes = map[string]*fuenteLocal{}
	}
	p := filepath.Join(raiz, NombrePaquete)
	f := sp.fuentes[raiz]
	if f != nil && time.Since(f.comprb) < 2*time.Second {
		return f
	}
	st, err := os.Stat(p)
	if err != nil {
		delete(sp.fuentes, raiz)
		return nil
	}
	if f != nil && f.tam == st.Size() && f.mod.Equal(st.ModTime()) {
		f.comprb = time.Now()
		return f
	}
	f = &fuenteLocal{raiz: raiz, inst: inst, tam: st.Size(), mod: st.ModTime(), comprb: time.Now()}
	f.pv, f.err = LeerPaquete(p)
	if f.err != nil {
		sp.avisarUnaVez("mal:"+p+st.ModTime().String(), "[paquete] %s no vale (no se reparte): %v", p, f.err)
	}
	sp.fuentes[raiz] = f
	return f
}

// actual: la fuente con la versión firmada más nueva (nil = ninguna). Se llama con sp.mu cogido.
func (sp *ServidorPaquete) actual() *fuenteLocal {
	var mejor *fuenteLocal
	for _, f := range []*fuenteLocal{sp.leerFuente(sp.Raiz, true), sp.leerFuente(sp.Semilla, false)} {
		if f != nil && f.pv != nil && (mejor == nil || f.pv.Man.Version > mejor.pv.Man.Version) {
			mejor = f
		}
	}
	return mejor
}

// Version: la versión firmada que reparte este lanzador (0 = ninguna). Va en la ficha.
func (sp *ServidorPaquete) Version() int64 {
	if sp == nil {
		return 0
	}
	sp.mu.Lock()
	defer sp.mu.Unlock()
	if f := sp.actual(); f != nil {
		return f.pv.Man.Version
	}
	return 0
}

func (f *fuenteLocal) rutaLocal(r, exe string) string {
	if f.inst && exe != "" {
		switch r {
		case ExeLanzadorApp:
			return exe
		case ExeLanzadorApp + ExtFirma:
			return exe + ExtFirma
		}
	}
	return filepath.Join(f.raiz, filepath.FromSlash(r))
}

// comprobado: el fichero del disco tiene el sha256 firmado (caché por tamaño y fecha). Se llama SIN sp.mu.
func (sp *ServidorPaquete) comprobado(local string, a *ArchivoPaquete) bool {
	st, err := os.Stat(local)
	if err != nil || !st.Mode().IsRegular() || st.Size() != a.Tam {
		return false
	}
	sp.mu.Lock()
	if sp.hashes == nil {
		sp.hashes = map[string]hashCache{}
	}
	hc, ok := sp.hashes[local]
	sp.mu.Unlock()
	if !ok || hc.tam != st.Size() || !hc.mod.Equal(st.ModTime()) {
		sha, err := shaFichero(local)
		if err != nil {
			return false
		}
		hc = hashCache{st.Size(), st.ModTime(), sha}
		sp.mu.Lock()
		if len(sp.hashes) > 2*maxFicherosPaquete {
			sp.hashes = map[string]hashCache{}
		}
		sp.hashes[local] = hc
		sp.mu.Unlock()
	}
	return hc.sha == a.SHA256
}

// permitir: cubo de peticiones del par. Se llama con sp.mu cogido.
func (sp *ServidorPaquete) par(quien string) *estadoPar {
	if sp.pares == nil || len(sp.pares) > 4096 {
		sp.pares = map[string]*estadoPar{}
	}
	p := sp.pares[quien]
	if p == nil {
		p = &estadoPar{fichas: rafagaPeticionPaq, ultimo: time.Now(), dia: time.Now()}
		sp.pares[quien] = p
	}
	return p
}

func (sp *ServidorPaquete) permitir(quien string) bool {
	sp.mu.Lock()
	defer sp.mu.Unlock()
	p := sp.par(quien)
	ahora := time.Now()
	p.fichas = min(rafagaPeticionPaq, p.fichas+ahora.Sub(p.ultimo).Seconds()/intervaloPeticionPaq.Seconds())
	p.ultimo = ahora
	if p.fichas < 1 {
		return false
	}
	p.fichas--
	return true
}

// Atender: un stream de ProtoActualiza o del puerto 3 del túnel. quien = el par (para los límites y el log).
func (sp *ServidorPaquete) Atender(st flujoPaquete, quien string) {
	defer st.Close()
	st.SetDeadline(time.Now().Add(20 * time.Second))
	var cab [4]byte
	if _, err := io.ReadFull(st, cab[:]); err != nil || string(cab[:3]) != pedidoPaquete {
		return
	}
	if !sp.permitir(quien) {
		st.Write([]byte{estOcupado})
		return
	}
	sp.mu.Lock()
	f := sp.actual()
	sp.mu.Unlock()
	if f == nil {
		st.Write([]byte{estSinPaquete})
		return
	}
	switch cab[3] {
	case opVersion:
		st.Write(binary.BigEndian.AppendUint64([]byte{estOK}, uint64(f.pv.Man.Version)))
	case opManifiesto:
		st.SetDeadline(time.Now().Add(5 * time.Minute))
		if _, err := st.Write([]byte{estOK}); err != nil {
			return
		}
		zw, _ := gzip.NewWriterLevel(&escritorLimitado{w: st, l: &sp.cubo}, gzip.BestSpeed)
		if _, err := zw.Write(f.pv.Bytes); err == nil {
			zw.Close()
		}
	case opLote:
		sp.lote(st, f, quien)
	}
}

func (sp *ServidorPaquete) lote(st flujoPaquete, f *fuenteLocal, quien string) {
	var n [2]byte
	if _, err := io.ReadFull(st, n[:]); err != nil {
		return
	}
	cuantos := int(binary.BigEndian.Uint16(n[:]))
	if cuantos == 0 || cuantos > maxLoteFicheros {
		st.Write([]byte{estNoEsta})
		return
	}
	var lista []*ArchivoPaquete
	var total int64
	for range cuantos {
		var l [2]byte
		if _, err := io.ReadFull(st, l[:]); err != nil {
			return
		}
		nb := make([]byte, binary.BigEndian.Uint16(l[:]))
		if len(nb) > maxRutaPaquete {
			st.Write([]byte{estNoEsta})
			return
		}
		if _, err := io.ReadFull(st, nb); err != nil {
			return
		}
		a := f.pv.Fichero(string(nb))
		if a == nil || a.Ruta != string(nb) {
			sp.logf("[paquete] %s pide %q, que no está en el manifiesto firmado", quien, nb)
			st.Write([]byte{estNoEsta})
			return
		}
		lista = append(lista, a)
		total += a.Tam
	}
	if len(lista) > 1 && total > maxLoteBytes {
		st.Write([]byte{estNoEsta})
		return
	}
	// límites: lotes a la vez (todos y por par) y bytes por par y día
	sp.mu.Lock()
	p := sp.par(quien)
	if time.Since(p.dia) > 24*time.Hour {
		p.dia, p.bytes = time.Now(), 0
	}
	if sp.activos >= maxLotesSimultaneos || p.activos >= maxLotesPorPar || p.bytes+total > maxBytesParDia {
		sp.mu.Unlock()
		st.Write([]byte{estOcupado})
		return
	}
	sp.activos++
	p.activos++
	p.bytes += total
	sp.mu.Unlock()
	defer func() {
		sp.mu.Lock()
		sp.activos--
		p.activos--
		sp.mu.Unlock()
	}()
	// solo lo que está tal cual se firmó
	locales := make([]string, len(lista))
	for i, a := range lista {
		locales[i] = f.rutaLocal(a.Ruta, sp.Exe)
		if !sp.comprobado(locales[i], a) {
			sp.avisarUnaVez("dist:"+locales[i], "[paquete] %s no es el firmado (¿tocado a mano?): no se reparte", locales[i])
			st.Write([]byte{estNoDisponible})
			return
		}
	}
	st.SetDeadline(time.Time{})
	if _, err := st.Write([]byte{estOK}); err != nil {
		return
	}
	t0 := time.Now()
	ew := &escritorLimitado{w: st, l: &sp.cubo, plazo: 2 * time.Minute}
	zw, _ := gzip.NewWriterLevel(ew, gzip.BestSpeed)
	var enviado int64
	for i, a := range lista {
		fh, err := os.Open(locales[i])
		if err != nil {
			st.Close()
			return
		}
		m, err := io.Copy(zw, io.LimitReader(fh, a.Tam))
		fh.Close()
		enviado += m
		if err != nil || m != a.Tam {
			sp.logf("[paquete] lote a %s cortado: %v", quien, err)
			return
		}
	}
	if err := zw.Close(); err != nil {
		return
	}
	if total > 1<<20 {
		sp.logf("[paquete] %d fichero(s), %d MB a %s en %v", len(lista), enviado>>20, quien, time.Since(t0).Round(time.Second))
	}
}

// AtenderLibp2p: el manejador de ProtoActualiza (n.H.SetStreamHandler).
func (sp *ServidorPaquete) AtenderLibp2p(st network.Stream) {
	sp.Atender(st, st.Conn().RemotePeer().String())
}

// limitador: cubo de bytes compartido (tasaSubidaPaquete por segundo, ráfaga de un segundo).
type limitador struct {
	mu     sync.Mutex
	fichas float64
	ultimo time.Time
}

func (l *limitador) esperar(n int) {
	tasa := float64(tasaSubidaPaquete)
	if tasa <= 0 {
		return
	}
	l.mu.Lock()
	ahora := time.Now()
	if l.ultimo.IsZero() {
		l.fichas = tasa
	} else {
		l.fichas = min(tasa, l.fichas+ahora.Sub(l.ultimo).Seconds()*tasa)
	}
	l.ultimo = ahora
	l.fichas -= float64(n)
	falta := l.fichas
	l.mu.Unlock()
	if falta < 0 {
		time.Sleep(time.Duration(-falta / tasa * float64(time.Second)))
	}
}

// escritorLimitado: escribe a trozos respetando el cubo y renovando el plazo de escritura en cada trozo.
type escritorLimitado struct {
	w     flujoPaquete
	l     *limitador
	plazo time.Duration
}

func (e *escritorLimitado) Write(p []byte) (int, error) {
	n := 0
	for len(p) > 0 {
		c := min(len(p), 32<<10)
		e.l.esperar(c)
		if e.plazo > 0 {
			e.w.SetDeadline(time.Now().Add(e.plazo))
		}
		m, err := e.w.Write(p[:c])
		n += m
		if err != nil {
			return n, err
		}
		p = p[c:]
	}
	return n, nil
}

// ---------------------------------------------------------------- invitado

// ClientePaquete: pide el paquete a un anfitrión por un camino (túnel o libp2p).
type ClientePaquete struct {
	Abrir func(ctx context.Context) (flujoPaquete, error)
	Quien string // para el log
}

func (c ClientePaquete) pedir(ctx context.Context, op byte, extra []byte) (flujoPaquete, error) {
	st, err := c.Abrir(ctx)
	if err != nil {
		return nil, err
	}
	if d, ok := ctx.Deadline(); ok {
		st.SetDeadline(d)
	} else {
		st.SetDeadline(time.Now().Add(60 * time.Second))
	}
	if _, err := st.Write(append([]byte{'Y', 'U', '1', op}, extra...)); err != nil {
		st.Close()
		return nil, err
	}
	var e [1]byte
	if _, err := io.ReadFull(st, e[:]); err != nil {
		st.Close()
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return nil, ErrSinPaquete // un anfitrión antiguo cierra el puerto 3 sin contestar
		}
		return nil, err
	}
	switch e[0] {
	case estOK:
		return st, nil
	case estSinPaquete:
		st.Close()
		return nil, ErrSinPaquete
	case estOcupado:
		st.Close()
		return nil, ErrOcupado
	}
	st.Close()
	return nil, fmt.Errorf("el anfitrión rechaza la petición (estado %d)", e[0])
}

// Version: la versión que dice tener el anfitrión (sin comprobar: para decidir si vale la pena pedir el manifiesto).
func (c ClientePaquete) Version(ctx context.Context) (int64, error) {
	st, err := c.pedir(ctx, opVersion, nil)
	if err != nil {
		return 0, err
	}
	defer st.Close()
	var v [8]byte
	if _, err := io.ReadFull(st, v[:]); err != nil {
		return 0, err
	}
	return int64(binary.BigEndian.Uint64(v[:])), nil
}

// Manifiesto: paquete.json del anfitrión, con la firma del editor ya comprobada.
func (c ClientePaquete) Manifiesto(ctx context.Context) (*PaqueteVerificado, error) {
	st, err := c.pedir(ctx, opManifiesto, nil)
	if err != nil {
		return nil, err
	}
	defer st.Close()
	st.SetDeadline(time.Time{})
	zr, err := gzip.NewReader(lectorConPlazo{st, 60 * time.Second})
	if err != nil {
		return nil, err
	}
	b, err := io.ReadAll(io.LimitReader(zr, MaxManifiestoPaquete+1))
	if err != nil {
		return nil, err
	}
	return VerificarPaquete(b)
}

// lectorConPlazo: renueva el plazo en cada lectura (corta si el anfitrión deja de mandar).
type lectorConPlazo struct {
	st    flujoPaquete
	plazo time.Duration
}

func (l lectorConPlazo) Read(p []byte) (int, error) {
	l.st.SetDeadline(time.Now().Add(l.plazo))
	return l.st.Read(p)
}

// Lote: pide esos ficheros (del manifiesto ya verificado) y llama a recibir con el contenido de cada uno, limitado a su
// tamaño firmado; recibir tiene que leerlo entero y comprobar el sha256. Si llega algo de más, error.
func (c ClientePaquete) Lote(ctx context.Context, fs []ArchivoPaquete, recibir func(ArchivoPaquete, io.Reader) error) error {
	if len(fs) == 0 || len(fs) > maxLoteFicheros {
		return errors.New("lote vacío o demasiado grande")
	}
	req := binary.BigEndian.AppendUint16(nil, uint16(len(fs)))
	for _, f := range fs {
		req = binary.BigEndian.AppendUint16(req, uint16(len(f.Ruta)))
		req = append(req, f.Ruta...)
	}
	st, err := c.pedir(ctx, opLote, req)
	if err != nil {
		return err
	}
	defer st.Close()
	parar := context.AfterFunc(ctx, func() { st.Close() })
	defer parar()
	st.SetDeadline(time.Time{})
	zr, err := gzip.NewReader(lectorConPlazo{st, 90 * time.Second})
	if err != nil {
		return err
	}
	for _, f := range fs {
		lr := &io.LimitedReader{R: zr, N: f.Tam}
		if err := recibir(f, lr); err != nil {
			return err
		}
		if lr.N != 0 {
			return fmt.Errorf("%s: llegó incompleto", f.Ruta)
		}
	}
	var x [1]byte
	if n, _ := zr.Read(x[:]); n > 0 {
		return errors.New("el anfitrión manda más datos de los firmados")
	}
	return nil
}

// ClientePaqueteTunel: el camino del túnel (puerto 3), sobre la sesión del invitado (reconecta si se cayó).
func ClientePaqueteTunel(inv *Invitado, quien string) ClientePaquete {
	return ClientePaquete{Quien: quien, Abrir: func(ctx context.Context) (flujoPaquete, error) {
		ses, err := inv.Sesion(ctx)
		if err != nil {
			return nil, err
		}
		return abrirPaqueteYamux(ses)
	}}
}

func abrirPaqueteYamux(ses *yamux.Session) (flujoPaquete, error) {
	return AbrirPuerto(ses, PuertoPaquete)
}

// ClientePaqueteP2P: el camino libp2p hacia el anfitrión id (un servidor de la lista), desde el nodo de la lista.
func (l *Lanzador) ClientePaqueteP2P(id string) (ClientePaquete, error) {
	pid, err := peer.Decode(id)
	if err != nil {
		return ClientePaquete{}, err
	}
	n, err := l.nodoLista()
	if err != nil {
		return ClientePaquete{}, err
	}
	return ClientePaquete{Quien: "P2P " + shortID(pid), Abrir: func(ctx context.Context) (flujoPaquete, error) {
		if err := n.H.Connect(ctx, peer.AddrInfo{ID: pid}); err != nil {
			return nil, fmt.Errorf("no conecta: %w", err)
		}
		st, err := n.H.NewStream(ctx, pid, ProtoActualiza)
		if err != nil {
			if strings.Contains(err.Error(), "protocols not supported") {
				return nil, ErrSinPaquete
			}
			return nil, err
		}
		return st, nil
	}}, nil
}

func shortID(p peer.ID) string {
	s := p.String()
	if len(s) > 16 {
		return s[len(s)-8:]
	}
	return s
}
