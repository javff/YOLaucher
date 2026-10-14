package sala

// Autoactualización por el MISMO túnel (P2P o Tor; nada de servidores externos). Solo tras el handshake del túnel, en el
// que el anfitrión demuestra con su firma que es él (codigo.go) y el invitado que tiene el código.
//
// Puertos lógicos especiales del túnel (además de 0 = eco):
//   1 = manifiesto: el anfitrión escribe u32 BE longitud + JSON {"v":1,"ficheros":[{nombre,tipo,tam,sha256,firma}]} y cierra.
//   2 = descarga:   el invitado escribe u16 BE longitud + nombre (tal cual viene en el manifiesto); el anfitrión
//                   contesta u8 estado (0 = ok) y, si ok, el fichero comprimido con gzip; cierra al acabar.
// Un anfitrión viejo no conoce los puertos 1/2 («puerto no permitido») y cierra el stream: el invitado lo toma como
// «sin autoactualización» y sigue con lo que tiene.
//
// Qué se reparte (lo decide el anfitrión):
//   - "lanzador": el JoinServer.exe de launcher\dist + su JoinServer.exe.firma (firma de EDITOR, ver firma.go). Sin
//     firma válida el anfitrión no lo ofrece, y el invitado lo comprueba otra vez con la clave pública que lleva dentro.
//     Se re-lee en cada petición: recompilar y firmar basta, sin reiniciar OpenServer.
//   - "mazo": con -compartir-mazos <carpeta>, los .ydc sueltos de esa carpeta (sin subcarpetas). El invitado solo los
//     instala si lo activa él (-actualizar-mazos), en CADA perfil suyo: profiles\<perfil>\<mazo>.ydc. No van firmados
//     por el editor (son del anfitrión): solo datos, nunca ejecutables, nombre sin rutas y como mucho MaxMazo bytes.

import (
	"compress/gzip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"io"
	"net"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/hashicorp/yamux"
)

const (
	PuertoManifiesto = 1
	PuertoDescarga   = 2

	NombreLanzador = "JoinServer.exe"
	TipoLanzador   = "lanzador"
	// El lanzador con ventana se reparte aparte: un JoinServer solo mira TipoLanzador y la firma lleva este nombre, así
	// que ninguno de los dos puede «actualizarse» al otro (un anfitrión no puede colar uno por otro).
	// NombreLanzadorApp es la IDENTIDAD firmada (manifiesto y .firma), NO el nombre del fichero: se queda en
	// "YO2Launcher.exe" para siempre, porque los lanzadores ya repartidos solo aceptan una firma con ese nombre (si cambia,
	// ninguno viejo se podría actualizar). El fichero se llama ExeLanzadorApp (12-10): «YO2Launcher.exe» chocaba con el
	// lanzador de Konami «Yo2Launcher.exe» de la carpeta del cliente (Windows no distingue mayúsculas) y lo sobrescribía.
	// Quien se actualiza reemplaza SU propio exe (os.Executable), se llame como se llame.
	NombreLanzadorApp = "YO2Launcher.exe"
	ExeLanzadorApp    = "YO2OnlineLauncher.exe"
	ExeKonami         = "Yo2Launcher.exe" // el lanzador original del cliente (96 KB): nunca se pisa
	TipoLanzadorApp   = "lanzador-app"
	TipoMazo          = "mazo"
	MaxMazo           = 1 << 20 // un .ydc real ocupa unos cientos de bytes
	maxMazos          = 500

	maxManifiesto = 1 << 20
	// Un fichero recién escrito (p. ej. go build en marcha) no se ofrece hasta que lleve este tiempo quieto.
	reposo = 15 * time.Second
)

type Fichero struct {
	Nombre string         `json:"nombre"`
	Tipo   string         `json:"tipo"`
	Tam    int64          `json:"tam"`
	SHA256 string         `json:"sha256"`
	Firma  *FirmaLanzador `json:"firma,omitempty"` // solo el lanzador
}

type Manifiesto struct {
	V        int       `json:"v"`
	Ficheros []Fichero `json:"ficheros"`
}

func (m *Manifiesto) Lanzador() *Fichero { return m.PorTipo(TipoLanzador) }

// LanzadorApp: el lanzador con ventana que ofrece el anfitrión (nil = ninguno; un OpenServer antiguo nunca lo ofrece).
func (m *Manifiesto) LanzadorApp() *Fichero { return m.PorTipo(TipoLanzadorApp) }

// PorTipo: la primera entrada del manifiesto de ese tipo (nil = ninguna).
func (m *Manifiesto) PorTipo(tipo string) *Fichero {
	for i := range m.Ficheros {
		if m.Ficheros[i].Tipo == tipo {
			return &m.Ficheros[i]
		}
	}
	return nil
}

// ChocaConKonami: ese fichero se llamaría como el lanzador de Konami de la carpeta del cliente (sin distinguir
// mayúsculas, como Windows) y lo sobrescribiría.
func ChocaConKonami(ruta string) bool { return strings.EqualFold(filepath.Base(ruta), ExeKonami) }

// NombreMazoValido: solo un nombre de fichero .ydc (sin carpetas, sin "..", sin ":" de ADS, sin nombres reservados).
func NombreMazoValido(r string) bool {
	if r == "" || len(r) > 120 || strings.ContainsAny(r, `/\:*?"<>|`) || strings.HasPrefix(r, ".") ||
		strings.HasSuffix(r, " ") || strings.ContainsFunc(r, func(c rune) bool { return c < 32 }) {
		return false
	}
	base := strings.ToUpper(strings.TrimSuffix(r, path.Ext(r)))
	for _, x := range []string{"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "LPT1", "LPT2", "LPT3"} {
		if base == x {
			return false
		}
	}
	return strings.EqualFold(path.Ext(r), ".ydc")
}

// ---------------------------------------------------------------- anfitrión

type hashCache struct {
	tam int64
	mod time.Time
	sha string
}

type Catalogo struct {
	Lanzador string // ruta del JoinServer.exe que se reparte ("" = ninguno); su firma en Lanzador+".firma"
	// LanzadorApp: ruta del lanzador con ventana (el exe del anfitrión, se llame como se llame) que se reparte ("" = ninguno); su firma en LanzadorApp+".firma"
	LanzadorApp string
	Mazos       string // carpeta de mazos .ydc que se reparten ("" = ninguno)
	Logf        func(string, ...any)
	// ProblemaLanzador / ProblemaLanzadorApp: por qué no se ofrece ese exe ahora mismo ("" = se ofrece o no hay)
	ProblemaLanzador    string
	ProblemaLanzadorApp string

	mu    sync.Mutex
	cache map[string]hashCache
	rutas map[string]string // nombre del manifiesto -> ruta local (del último manifiesto)
}

func shaFichero(p string) (string, error) {
	f, err := os.Open(p)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// ShaFichero: sha256 en hexadecimal de un fichero.
func ShaFichero(p string) (string, error) { return shaFichero(p) }

func (c *Catalogo) entrada(nombre, tipo, ruta string) (Fichero, bool) {
	st, err := os.Stat(ruta)
	if err != nil || !st.Mode().IsRegular() {
		return Fichero{}, false
	}
	if time.Since(st.ModTime()) < reposo {
		c.Logf("[actualiza] %s recién modificado: se ofrecerá cuando lleve %v quieto", ruta, reposo)
		return Fichero{}, false
	}
	if c.cache == nil {
		c.cache = map[string]hashCache{}
	}
	hc, ok := c.cache[ruta]
	if !ok || hc.tam != st.Size() || !hc.mod.Equal(st.ModTime()) {
		sha, err := shaFichero(ruta)
		if err != nil {
			c.Logf("[actualiza] %s: %v", ruta, err)
			return Fichero{}, false
		}
		hc = hashCache{st.Size(), st.ModTime(), sha}
		c.cache[ruta] = hc
	}
	return Fichero{Nombre: nombre, Tipo: tipo, Tam: hc.tam, SHA256: hc.sha}, true
}

// ofrecer: el exe de ruta (con su .firma) como entrada del manifiesto. Solo se ofrece con una firma de editor válida
// PARA ESE NOMBRE y que sea de ESTE exe; si no, ok=false y prob dice por qué ("" = no hay exe o está recién escrito).
// Se llama con c.mu cogido.
func (c *Catalogo) ofrecer(nombre, tipo, ruta string) (f Fichero, ok bool, prob string) {
	if ruta == "" {
		return f, false, ""
	}
	f, ok = c.entrada(nombre, tipo, ruta)
	if !ok {
		return f, false, ""
	}
	fi, err := LeerFirma(ruta + ExtFirma)
	switch {
	case err != nil:
		return f, false, "falta la firma " + ruta + ExtFirma + " (fírmalo con firmar.exe o build.ps1)"
	case fi.VerificarPara(nombre) != nil:
		return f, false, "la firma " + ruta + ExtFirma + " no es válida: " + fi.VerificarPara(nombre).Error()
	case !strings.EqualFold(fi.SHA256, f.SHA256) || fi.Tam != f.Tam:
		return f, false, "la firma es de otro " + nombre + " (¿recompilado sin firmar?)"
	}
	f.Firma = fi
	return f, true, ""
}

// Manifiesto: lo que el anfitrión ofrece AHORA (se recalcula en cada petición; los hashes se cachean por tamaño+fecha).
func (c *Catalogo) Manifiesto() Manifiesto {
	c.mu.Lock()
	defer c.mu.Unlock()
	m := Manifiesto{V: 1}
	rutas := map[string]string{}
	c.ProblemaLanzador, c.ProblemaLanzadorApp = "", ""
	if f, ok, prob := c.ofrecer(NombreLanzador, TipoLanzador, c.Lanzador); ok {
		m.Ficheros = append(m.Ficheros, f)
		rutas[f.Nombre] = c.Lanzador
	} else {
		c.ProblemaLanzador = prob
	}
	if f, ok, prob := c.ofrecer(NombreLanzadorApp, TipoLanzadorApp, c.LanzadorApp); ok {
		m.Ficheros = append(m.Ficheros, f)
		rutas[f.Nombre] = c.LanzadorApp
	} else {
		c.ProblemaLanzadorApp = prob
	}
	if c.Mazos != "" {
		var mazos []Fichero
		ents, _ := os.ReadDir(c.Mazos)
		for _, d := range ents {
			if d.IsDir() || len(mazos) >= maxMazos || !NombreMazoValido(d.Name()) {
				continue
			}
			p := filepath.Join(c.Mazos, d.Name())
			if f, ok := c.entrada(d.Name(), TipoMazo, p); ok && f.Tam <= MaxMazo {
				mazos = append(mazos, f)
				rutas[d.Name()] = p
			}
		}
		sort.Slice(mazos, func(i, j int) bool { return mazos[i].Nombre < mazos[j].Nombre })
		m.Ficheros = append(m.Ficheros, mazos...)
	}
	c.rutas = rutas
	return m
}

// Servir atiende un stream de los puertos 1/2 (ya autenticado por el handshake del túnel).
func (c *Catalogo) Servir(st net.Conn, puerto int, quien string) {
	switch puerto {
	case PuertoManifiesto:
		b, _ := json.Marshal(c.Manifiesto())
		var l [4]byte
		binary.BigEndian.PutUint32(l[:], uint32(len(b)))
		st.SetWriteDeadline(time.Now().Add(30 * time.Second))
		st.Write(append(l[:], b...))
	case PuertoDescarga:
		st.SetReadDeadline(time.Now().Add(20 * time.Second))
		var l [2]byte
		if _, err := io.ReadFull(st, l[:]); err != nil {
			return
		}
		nb := make([]byte, binary.BigEndian.Uint16(l[:]))
		if _, err := io.ReadFull(st, nb); err != nil {
			return
		}
		st.SetReadDeadline(time.Time{})
		nombre := string(nb)
		c.mu.Lock()
		ruta, ok := c.rutas[nombre]
		c.mu.Unlock()
		if !ok {
			c.Logf("[actualiza] %s pide %q, que no está en el manifiesto", quien, nombre)
			st.Write([]byte{1})
			return
		}
		f, err := os.Open(ruta)
		if err != nil {
			c.Logf("[actualiza] %s: %v", ruta, err)
			st.Write([]byte{2})
			return
		}
		defer f.Close()
		t0 := time.Now()
		c.Logf("[actualiza] enviando %s a %s", nombre, quien)
		if _, err := st.Write([]byte{0}); err != nil {
			return
		}
		zw, _ := gzip.NewWriterLevel(st, gzip.BestSpeed)
		n, err := io.Copy(zw, f)
		if err == nil {
			err = zw.Close()
		}
		c.Logf("[actualiza] %s a %s: %d bytes en %v, err=%v", nombre, quien, n, time.Since(t0).Round(time.Millisecond), err)
	}
}

// ---------------------------------------------------------------- invitado

// ErrSinActualizacion: el anfitrión no ofrece autoactualización (OpenServer anterior a esta función).
var ErrSinActualizacion = errors.New("el anfitrión no ofrece autoactualización (OpenServer antiguo)")

func PedirManifiesto(ses *yamux.Session) (*Manifiesto, error) {
	st, err := AbrirPuerto(ses, PuertoManifiesto)
	if err != nil {
		return nil, err
	}
	defer st.Close()
	st.SetReadDeadline(time.Now().Add(30 * time.Second))
	var l [4]byte
	if _, err := io.ReadFull(st, l[:]); err != nil {
		if errors.Is(err, io.EOF) {
			return nil, ErrSinActualizacion
		}
		return nil, err
	}
	n := binary.BigEndian.Uint32(l[:])
	if n > maxManifiesto {
		return nil, fmt.Errorf("manifiesto demasiado grande (%d)", n)
	}
	b := make([]byte, n)
	if _, err := io.ReadFull(st, b); err != nil {
		return nil, err
	}
	var m Manifiesto
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, err
	}
	return &m, nil
}

// lectorInactivo: renueva el plazo de lectura en cada lectura (corta solo si el anfitrión deja de mandar).
type lectorInactivo struct {
	c     net.Conn
	plazo time.Duration
}

func (l lectorInactivo) Read(p []byte) (int, error) {
	l.c.SetReadDeadline(time.Now().Add(l.plazo))
	return l.c.Read(p)
}

type contador struct {
	w     io.Writer
	h     hash.Hash
	n     int64
	max   int64
	avisa func(int64)
}

func (c *contador) Write(p []byte) (int, error) {
	if c.n+int64(len(p)) > c.max {
		return 0, errors.New("el fichero recibido es más grande de lo anunciado")
	}
	n, err := c.w.Write(p)
	c.h.Write(p[:n])
	c.n += int64(n)
	if c.avisa != nil {
		c.avisa(c.n)
	}
	return n, err
}

// Descargar baja f a destino (lo crea/sobrescribe) y comprueba tamaño y sha256; si algo falla, borra destino.
func Descargar(ses *yamux.Session, f Fichero, destino string, progreso func(hecho, total int64)) (err error) {
	st, err := AbrirPuerto(ses, PuertoDescarga)
	if err != nil {
		return err
	}
	defer st.Close()
	if len(f.Nombre) > 1000 {
		return errors.New("nombre demasiado largo")
	}
	var l [2]byte
	binary.BigEndian.PutUint16(l[:], uint16(len(f.Nombre)))
	if _, err := st.Write(append(l[:], f.Nombre...)); err != nil {
		return err
	}
	r := lectorInactivo{st, 60 * time.Second}
	var e [1]byte
	if _, err := io.ReadFull(r, e[:]); err != nil {
		return fmt.Errorf("el anfitrión no contesta: %w", err)
	}
	if e[0] != 0 {
		return fmt.Errorf("el anfitrión no puede dar %s (estado %d)", f.Nombre, e[0])
	}
	zr, err := gzip.NewReader(r)
	if err != nil {
		return err
	}
	out, err := os.OpenFile(destino, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o700)
	if err != nil {
		return err
	}
	defer func() {
		out.Close()
		if err != nil {
			os.Remove(destino)
		}
	}()
	cw := &contador{w: out, h: sha256.New(), max: f.Tam}
	if progreso != nil {
		cw.avisa = func(n int64) { progreso(n, f.Tam) }
	}
	if _, err = io.Copy(cw, zr); err != nil {
		return err
	}
	if err = out.Sync(); err != nil {
		return err
	}
	if cw.n != f.Tam {
		return fmt.Errorf("descarga incompleta (%d de %d bytes)", cw.n, f.Tam)
	}
	if got := hex.EncodeToString(cw.h.Sum(nil)); got != f.SHA256 {
		return fmt.Errorf("el sha256 no coincide (%s… en vez de %s…)", got[:12], f.SHA256[:12])
	}
	return nil
}
