package sala

// Versión FIRMADA del paquete del Discord (UPD, 12-10). El paquete entero (lanzador + server\ + engine\ + vendor\ +
// python\ + node\ + los .txt de la raíz) es una versión con un manifiesto firmado por el EDITOR (la misma clave que
// firma el lanzador, firma.go; la privada nunca sale del PC del owner):
//
//	paquete.json = {"v":1,"firma":"<base64 ed25519>","manifiesto":<JSON del manifiesto, bytes EXACTOS que se firman>}
//	firma sobre  "yo2online-paquete-v1" 0x00 <bytes del manifiesto>
//	manifiesto   {"v":1,"producto":"yo2online-paquete","version":AAAAMMDDhhmmss,"version_lanzador":N,
//	              "ficheros":[{"ruta":"server/yo2emu/run.py","parte":"server","sha256":"…","tam":123}, …]}
//
// Lo escribe tools/yo2emu/paquete/empaquetar.py (paquete.manifiesto.json, sin firmar) y lo firma `firmar -paquete`.
// Las rutas son relativas a la carpeta del lanzador, con «/», y se comprueban con RutaPaqueteValida: nada de «..»,
// rutas absolutas, unidades, «:» (ADS), nombres reservados de Windows ni dos rutas que solo difieran en mayúsculas.
// La parte de cada fichero sale de su ruta (no del campo «parte», que solo se comprueba): lanzador (el exe y su
// .firma), raiz (.txt/.md sueltos), server, engine, vendor, python, node. Ningún otro ejecutable en la raíz.
// La propagación (red) está en paquete_red.go y la instalación en paquete_inst.go.

import (
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"strings"
)

const (
	NombrePaquete       = "paquete.json"            // el manifiesto firmado, en la carpeta del lanzador
	NombrePaqueteSinFir = "paquete.manifiesto.json" // lo que deja empaquetar.py para que lo firme `firmar -paquete`
	DirActualiza        = "_update"                 // staging de las actualizaciones, DENTRO de la carpeta del lanzador
	ProductoPaquete     = "yo2online-paquete"
	etqPaquete          = "yo2online-paquete-v1"

	ParteLanzador = "lanzador"
	ParteRaiz     = "raiz"

	// Límites del manifiesto (el de hoy: ~23 000 ficheros, ~230 MB, 3,6 MB de JSON).
	MaxManifiestoPaquete = 32 << 20
	maxFicherosPaquete   = 200000
	MaxFicheroPaquete    = 512 << 20
	MaxTotalPaquete      = 2 << 30
	maxRutaPaquete       = 400
)

// PartesDir: las carpetas del paquete que se reemplazan enteras (renombrar) cuando cambia algo dentro.
var PartesDir = []string{"server", "engine", "vendor", "python", "node"}

func esParteDir(p string) bool {
	for _, x := range PartesDir {
		if x == p {
			return true
		}
	}
	return false
}

type ArchivoPaquete struct {
	Ruta   string `json:"ruta"`
	Parte  string `json:"parte"`
	SHA256 string `json:"sha256"`
	Tam    int64  `json:"tam"`
}

type ManifiestoPaquete struct {
	V               int              `json:"v"`
	Producto        string           `json:"producto"`
	Version         int64            `json:"version"`
	VersionLanzador int64            `json:"version_lanzador"`
	Creado          string           `json:"creado,omitempty"`
	Ficheros        []ArchivoPaquete `json:"ficheros"`
	// Red: la lista de nodos de arranque y relays firmada por el editor (red_nodos.go; NET, 12-10). Opcional: un
	// lanzador anterior la ignora. Lleva su propia firma para poder guardarla aparte sin el manifiesto entero.
	Red *ListaRed `json:"red,omitempty"`
}

// PaqueteFirmado: paquete.json tal cual (Manifiesto = los bytes firmados, sin tocar).
type PaqueteFirmado struct {
	V          int             `json:"v"`
	Firma      string          `json:"firma"`
	Manifiesto json.RawMessage `json:"manifiesto"`
}

// PaqueteVerificado: un paquete.json con la firma del editor comprobada y el manifiesto validado.
type PaqueteVerificado struct {
	Man    *ManifiestoPaquete
	Bytes  []byte // paquete.json entero, tal cual (es lo que se reparte y lo que se instala)
	porRut map[string]*ArchivoPaquete
}

// Fichero: la entrada de esa ruta (nil = no está).
func (p *PaqueteVerificado) Fichero(ruta string) *ArchivoPaquete {
	if p == nil {
		return nil
	}
	return p.porRut[strings.ToLower(ruta)]
}

func msgPaquete(raw []byte) []byte {
	return append([]byte(etqPaquete+"\x00"), raw...)
}

var reservadosWin = map[string]bool{"CON": true, "PRN": true, "AUX": true, "NUL": true, "CONIN$": true, "CONOUT$": true}

func init() {
	for i := 0; i <= 9; i++ {
		reservadosWin[fmt.Sprintf("COM%d", i)] = true
		reservadosWin[fmt.Sprintf("LPT%d", i)] = true
	}
}

// segmentoValido: un nombre de fichero o carpeta que Windows trata tal cual (sin sorpresas).
func segmentoValido(s string) bool {
	if s == "" || s == "." || s == ".." || len(s) > 255 || strings.ContainsAny(s, `<>:"|?*\/`) ||
		strings.HasSuffix(s, ".") || strings.HasSuffix(s, " ") || strings.HasPrefix(s, " ") ||
		strings.ContainsFunc(s, func(c rune) bool { return c < 32 || c == 0x7f }) {
		return false
	}
	base := strings.ToUpper(s)
	if i := strings.IndexByte(base, '.'); i >= 0 {
		base = base[:i]
	}
	return !reservadosWin[strings.TrimSpace(base)]
}

// ParteDeRuta: la parte del paquete a la que pertenece la ruta, o error si la ruta no vale.
func ParteDeRuta(r string) (string, error) {
	if r == "" || len(r) > maxRutaPaquete || strings.HasPrefix(r, "/") || strings.Contains(r, `\`) || path.Clean(r) != r {
		return "", fmt.Errorf("ruta no válida: %q", r)
	}
	segs := strings.Split(r, "/")
	for _, s := range segs {
		if !segmentoValido(s) {
			return "", fmt.Errorf("ruta no válida: %q", r)
		}
	}
	if len(segs) == 1 {
		switch {
		case r == ExeLanzadorApp || r == ExeLanzadorApp+ExtFirma:
			return ParteLanzador, nil
		case strings.EqualFold(r, NombrePaquete) || strings.EqualFold(r, NombrePaqueteSinFir):
			return "", fmt.Errorf("ruta reservada: %q", r)
		case strings.EqualFold(path.Ext(r), ".txt") || strings.EqualFold(path.Ext(r), ".md"):
			return ParteRaiz, nil
		}
		return "", fmt.Errorf("fichero no permitido en la raíz del paquete: %q", r)
	}
	if !esParteDir(segs[0]) {
		return "", fmt.Errorf("carpeta no permitida en el paquete: %q", r)
	}
	return segs[0], nil
}

// RutaPaqueteValida: la ruta se puede instalar (dentro de la carpeta del lanzador, en una parte conocida).
func RutaPaqueteValida(r string) bool {
	_, err := ParteDeRuta(r)
	return err == nil
}

func hexMinusculas(s string, n int) bool {
	if len(s) != n {
		return false
	}
	for _, c := range s {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return false
		}
	}
	return true
}

// Validar: el manifiesto cumple todas las reglas (no dice nada de la firma).
func (m *ManifiestoPaquete) Validar() error {
	if m.V != 1 || m.Producto != ProductoPaquete {
		return errors.New("manifiesto de paquete desconocido")
	}
	if m.Version <= 0 || m.VersionLanzador < 0 {
		return errors.New("versión del paquete no válida")
	}
	if len(m.Ficheros) == 0 || len(m.Ficheros) > maxFicherosPaquete {
		return fmt.Errorf("número de ficheros fuera de rango (%d)", len(m.Ficheros))
	}
	vistos := map[string]bool{}
	var total int64
	exe, firma := false, false
	for _, f := range m.Ficheros {
		p, err := ParteDeRuta(f.Ruta)
		if err != nil {
			return err
		}
		if f.Parte != p {
			return fmt.Errorf("%s: parte %q, debería ser %q", f.Ruta, f.Parte, p)
		}
		k := strings.ToLower(f.Ruta)
		if vistos[k] {
			return fmt.Errorf("ruta repetida: %q", f.Ruta)
		}
		vistos[k] = true
		if !hexMinusculas(f.SHA256, 64) || f.Tam < 0 || f.Tam > MaxFicheroPaquete {
			return fmt.Errorf("%s: sha256 o tamaño no válidos", f.Ruta)
		}
		total += f.Tam
		exe = exe || f.Ruta == ExeLanzadorApp
		firma = firma || f.Ruta == ExeLanzadorApp+ExtFirma
	}
	// una carpeta y un fichero con el mismo nombre (server/x y server/x/y) no se pueden instalar a la vez
	for k := range vistos {
		for i := range len(k) {
			if k[i] == '/' && vistos[k[:i]] {
				return fmt.Errorf("%q es a la vez fichero y carpeta", k[:i])
			}
		}
	}
	if total > MaxTotalPaquete {
		return fmt.Errorf("el paquete ocupa demasiado (%d bytes)", total)
	}
	if !exe || !firma {
		return errors.New("el manifiesto no lleva el lanzador y su firma")
	}
	if m.Red != nil {
		if err := m.Red.Verificar(); err != nil {
			return fmt.Errorf("lista de nodos de red del paquete: %w", err)
		}
	}
	return nil
}

// VerificarPaquete: comprueba la firma del editor de un paquete.json y valida su manifiesto.
func VerificarPaquete(b []byte) (*PaqueteVerificado, error) {
	if len(b) > MaxManifiestoPaquete {
		return nil, errors.New("paquete.json demasiado grande")
	}
	var pf PaqueteFirmado
	if err := json.Unmarshal(b, &pf); err != nil {
		return nil, fmt.Errorf("paquete.json mal formado: %w", err)
	}
	if pf.V != 1 || len(pf.Manifiesto) == 0 {
		return nil, errors.New("paquete.json de una versión desconocida")
	}
	pub, err := ClaveEditor()
	if err != nil {
		return nil, err
	}
	sig, err := base64.StdEncoding.DecodeString(pf.Firma)
	if err != nil || len(sig) != ed25519.SignatureSize || !ed25519.Verify(pub, msgPaquete(pf.Manifiesto), sig) {
		return nil, errors.New("la firma del paquete NO es la del editor")
	}
	var m ManifiestoPaquete
	if err := json.Unmarshal(pf.Manifiesto, &m); err != nil {
		return nil, fmt.Errorf("manifiesto mal formado: %w", err)
	}
	if err := m.Validar(); err != nil {
		return nil, err
	}
	pv := &PaqueteVerificado{Man: &m, Bytes: append([]byte{}, b...), porRut: map[string]*ArchivoPaquete{}}
	for i := range m.Ficheros {
		pv.porRut[strings.ToLower(m.Ficheros[i].Ruta)] = &m.Ficheros[i]
	}
	return pv, nil
}

// LeerPaquete: paquete.json de una carpeta, verificado (error si no está o no vale).
func LeerPaquete(p string) (*PaqueteVerificado, error) {
	st, err := os.Stat(p)
	if err != nil {
		return nil, err
	}
	if st.Size() > MaxManifiestoPaquete {
		return nil, errors.New("paquete.json demasiado grande")
	}
	b, err := os.ReadFile(p)
	if err != nil {
		return nil, err
	}
	return VerificarPaquete(b)
}

// FirmarManifiesto: paquete.json a partir de los bytes del manifiesto (firmar.exe y las pruebas). Valida antes.
func FirmarManifiesto(priv ed25519.PrivateKey, raw []byte) ([]byte, error) {
	raw = bytes.TrimSpace(raw) // lo que se firma es justo el valor JSON (sin el salto de línea del final)
	var m ManifiestoPaquete
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, fmt.Errorf("manifiesto mal formado: %w", err)
	}
	if err := m.Validar(); err != nil {
		return nil, err
	}
	sig := base64.StdEncoding.EncodeToString(ed25519.Sign(priv, msgPaquete(raw)))
	out := []byte(`{"v":1,"firma":"` + sig + `","manifiesto":`)
	out = append(out, raw...)
	return append(out, '}'), nil
}

// ComprobarArbol: la carpeta raiz tiene EXACTAMENTE los ficheros del manifiesto (aparte de paquete.json y
// paquete.manifiesto.json) con su tamaño y sha256, y la .firma del lanzador es la de su exe. Lo usa `firmar -paquete`
// antes de firmar: nunca se firma un manifiesto que no describe lo que va en el zip.
func ComprobarArbol(raiz string, m *ManifiestoPaquete) error {
	por := map[string]ArchivoPaquete{}
	for _, f := range m.Ficheros {
		por[f.Ruta] = f
	}
	vistos := 0
	err := filepath.WalkDir(raiz, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(raiz, p)
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			if rel != "." && (d.Name() == "__pycache__" || d.Name() == DirActualiza) {
				return fmt.Errorf("carpeta que no debe ir en el paquete: %s", rel)
			}
			return nil
		}
		if rel == NombrePaquete || rel == NombrePaqueteSinFir {
			return nil
		}
		f, ok := por[rel]
		if !ok {
			return fmt.Errorf("fichero que no está en el manifiesto: %s", rel)
		}
		st, err := d.Info()
		if err != nil {
			return err
		}
		if st.Size() != f.Tam {
			return fmt.Errorf("%s: tamaño %d, el manifiesto dice %d", rel, st.Size(), f.Tam)
		}
		if sha, err := shaFichero(p); err != nil || sha != f.SHA256 {
			return fmt.Errorf("%s: el sha256 no es el del manifiesto", rel)
		}
		vistos++
		return nil
	})
	if err != nil {
		return err
	}
	if vistos != len(m.Ficheros) {
		return fmt.Errorf("faltan ficheros del manifiesto en la carpeta (%d de %d)", vistos, len(m.Ficheros))
	}
	fi, err := LeerFirma(filepath.Join(raiz, ExeLanzadorApp+ExtFirma))
	if err != nil {
		return fmt.Errorf("firma del lanzador: %w", err)
	}
	if err := fi.VerificarPara(NombreLanzadorApp); err != nil {
		return fmt.Errorf("firma del lanzador: %w", err)
	}
	fe := por[ExeLanzadorApp]
	if fi.SHA256 != fe.SHA256 || fi.Tam != fe.Tam {
		return errors.New("la .firma del lanzador es de otro exe")
	}
	if fi.Version != m.VersionLanzador {
		return fmt.Errorf("version_lanzador %d no es la firmada del exe (%d)", m.VersionLanzador, fi.Version)
	}
	return nil
}

// ManifiestoDeArbol: el manifiesto (JSON compacto, sin firmar) de una carpeta de paquete, como lo escribe
// empaquetar.py. Lo usan las pruebas (y sirve para cotejar a mano con el de Python).
func ManifiestoDeArbol(raiz string, version, versionLanzador int64) ([]byte, error) {
	m := ManifiestoPaquete{V: 1, Producto: ProductoPaquete, Version: version, VersionLanzador: versionLanzador}
	err := filepath.WalkDir(raiz, func(p string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return err
		}
		rel, _ := filepath.Rel(raiz, p)
		rel = filepath.ToSlash(rel)
		if rel == NombrePaquete || rel == NombrePaqueteSinFir {
			return nil
		}
		parte, err := ParteDeRuta(rel)
		if err != nil {
			return err
		}
		st, err := d.Info()
		if err != nil {
			return err
		}
		sha, err := shaFichero(p)
		if err != nil {
			return err
		}
		m.Ficheros = append(m.Ficheros, ArchivoPaquete{Ruta: rel, Parte: parte, SHA256: sha, Tam: st.Size()})
		return nil
	})
	if err != nil {
		return nil, err
	}
	if err := m.Validar(); err != nil {
		return nil, err
	}
	return json.Marshal(m)
}

// UsarClaveEditorDePrueba: SOLO PRUEBAS. Cambia la clave pública del editor por una de prueba generada en el momento
// (la privada de verdad nunca está en el repo); devuelve con qué dejarla como estaba.
func UsarClaveEditorDePrueba(pub ed25519.PublicKey) (restaurar func()) {
	antes := claveEditorHex
	claveEditorHex = hex.EncodeToString(pub)
	return func() { claveEditorHex = antes }
}
