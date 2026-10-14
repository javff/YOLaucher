package sala

// Nodos de arranque (DHT) y relays configurables y actualizables (NET, 12-10; NOTES-red.md §13).
//
// Antes la lista era la de fábrica de go-libp2p-kad-dht (los «bootstrap» de IPFS), fija en el código. Para que el
// proyecto sobreviva aunque esos nodos cambien o desaparezcan, la lista efectiva es la UNIÓN de tres fuentes, en este
// orden de prioridad (se prueban todas; el orden decide qué va primero y quién manda si un peer ID sale en dos):
//
//  1. la lista FIRMADA por el editor más nueva que conozca este lanzador: viaja dentro del manifiesto del paquete
//     (campo «red» de paquete.json, §10) y se guarda aparte en <datos>\red-firmada.json en cuanto se ve una más nueva
//     (aunque el paquete aún no se haya instalado). Firma ed25519 con la MISMA clave del editor (firma.go) sobre un texto
//     fijo (MensajeRed), así que se comprueba sola, sin el manifiesto entero;
//  2. la del usuario (avanzado, sin firma): <datos>\red-nodos.txt, una línea por nodo («arranque <multiaddr>» o
//     «relay <multiaddr>», # = comentario);
//  3. la de fábrica compilada (ArranqueFabrica: la de go-libp2p-kad-dht de la versión con que se compiló).
//
// Lo que NO se puede: que alguien que no sea el editor cambie la lista de otros (un anfitrión solo reparte paquetes
// firmados; la del usuario es solo suya). Una lista firmada vieja nunca pisa a una más nueva (versión = anti-rollback).

import (
	"bufio"
	"bytes"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	dht "github.com/libp2p/go-libp2p-kad-dht"
	"github.com/libp2p/go-libp2p/core/peer"
	ma "github.com/multiformats/go-multiaddr"
)

const (
	etqRed            = "yo2online-red-v1"
	NombreRedFirmada  = "red-firmada.json" // en <datos>: la última lista firmada vista
	NombreRedUsuario  = "red-nodos.txt"    // en <datos>: la del usuario (avanzado)
	maxNodosRed       = 64                 // por tipo (arranque / relay)
	maxDirNodoRed     = 300                // caracteres de una multiaddr
	maxFicheroRed     = 64 << 10
	RolArranque       = "arranque"
	RolRelay          = "relay"
	OrigenFabrica     = "fabrica"
	OrigenUsuario     = "usuario"
	OrigenFirmadaPaq  = "paquete"  // la del manifiesto del paquete instalado (o de la semilla)
	OrigenFirmadaCach = "guardada" // <datos>\red-firmada.json
)

// ListaRed: la lista firmada por el editor (va como «red» en el manifiesto del paquete y en red-firmada.json).
type ListaRed struct {
	V        int      `json:"v"`
	Version  int64    `json:"version"` // AAAAMMDDhhmmss: la más nueva manda
	Arranque []string `json:"arranque"`
	Relays   []string `json:"relays,omitempty"`
	Firma    string   `json:"firma"` // base64 de la firma ed25519 de MensajeRed()
}

// MensajeRed: lo que se firma (texto fijo, sin ambigüedad de JSON; el orden de las listas cuenta).
func (l *ListaRed) MensajeRed() []byte {
	var b strings.Builder
	fmt.Fprintf(&b, "%s\nversion=%d\n", etqRed, l.Version)
	for _, a := range l.Arranque {
		b.WriteString("arranque=" + a + "\n")
	}
	for _, a := range l.Relays {
		b.WriteString("relay=" + a + "\n")
	}
	return []byte(b.String())
}

// nodoRedValido: una multiaddr con /p2p/<peer ID> al final, sin relays de por medio (p2p-circuit) ni caracteres raros.
func nodoRedValido(s string) (*peer.AddrInfo, error) {
	if s == "" || len(s) > maxDirNodoRed || strings.ContainsFunc(s, func(r rune) bool { return r <= ' ' || r == 0x7f }) {
		return nil, fmt.Errorf("nodo de red no válido: %q", s)
	}
	a, err := ma.NewMultiaddr(s)
	if err != nil {
		return nil, fmt.Errorf("nodo de red no válido %q: %w", s, err)
	}
	if strings.Contains(a.String(), "p2p-circuit") {
		return nil, fmt.Errorf("nodo de red por relay (no vale como nodo fijo): %q", s)
	}
	ai, err := peer.AddrInfoFromP2pAddr(a)
	if err != nil {
		return nil, fmt.Errorf("al nodo %q le falta /p2p/<peer ID>: %w", s, err)
	}
	return ai, nil
}

// Validar: campos bien formados (no mira la firma).
func (l *ListaRed) Validar() error {
	if l == nil {
		return errors.New("sin lista de red")
	}
	if l.V != 1 || l.Version <= 0 {
		return errors.New("lista de red de una versión desconocida")
	}
	if len(l.Arranque) == 0 || len(l.Arranque) > maxNodosRed || len(l.Relays) > maxNodosRed {
		return fmt.Errorf("lista de red con %d nodos de arranque y %d relays (1-%d y 0-%d)", len(l.Arranque), len(l.Relays), maxNodosRed, maxNodosRed)
	}
	for _, s := range append(append([]string{}, l.Arranque...), l.Relays...) {
		if _, err := nodoRedValido(s); err != nil {
			return err
		}
	}
	return nil
}

// Verificar: bien formada y firmada por el editor (la clave pública incrustada, firma.go).
func (l *ListaRed) Verificar() error {
	if err := l.Validar(); err != nil {
		return err
	}
	pub, err := ClaveEditor()
	if err != nil {
		return err
	}
	sig, err := base64.StdEncoding.DecodeString(l.Firma)
	if err != nil || len(sig) != ed25519.SignatureSize || !ed25519.Verify(pub, l.MensajeRed(), sig) {
		return errors.New("la lista de red NO está firmada por el editor")
	}
	return nil
}

// FirmarListaRed: la firma (firmar.exe -red y las pruebas). Valida antes.
func FirmarListaRed(priv ed25519.PrivateKey, l ListaRed) (*ListaRed, error) {
	l.V = 1
	if l.Version <= 0 {
		l.Version, _ = strconv.ParseInt(time.Now().UTC().Format("20060102150405"), 10, 64)
	}
	l.Firma = ""
	if err := l.Validar(); err != nil {
		return nil, err
	}
	l.Firma = base64.StdEncoding.EncodeToString(ed25519.Sign(priv, l.MensajeRed()))
	return &l, nil
}

// LeerNodosTexto: el formato de texto (red-nodos.txt del usuario y lo que firma `firmar -red`): una línea por nodo,
// «arranque <multiaddr>» (o «bootstrap», como lo dice la ventana) o «relay <multiaddr>» (una multiaddr sola = arranque);
// # empieza un comentario. Devuelve los
// nodos válidos y, aparte, un error por cada línea que no vale (no paran la lectura: el resto se usa).
func LeerNodosTexto(b []byte) (arranque, relays []string, malas []error) {
	sc := bufio.NewScanner(bytes.NewReader(b))
	sc.Buffer(make([]byte, 4096), 4096)
	n := 0
	vistos := map[string]bool{}
	for sc.Scan() {
		n++
		l := sc.Text()
		if i := strings.IndexByte(l, '#'); i >= 0 {
			l = l[:i]
		}
		l = strings.TrimSpace(strings.TrimPrefix(l, "\uFEFF"))
		if l == "" {
			continue
		}
		rol, dir := RolArranque, l
		if c := strings.Fields(l); len(c) == 2 {
			rol, dir = strings.ToLower(c[0]), c[1]
		} else if len(c) != 1 {
			malas = append(malas, fmt.Errorf("línea %d: se esperaba «arranque|relay <multiaddr>»", n))
			continue
		}
		if rol == "bootstrap" {
			rol = RolArranque
		}
		if rol != RolArranque && rol != RolRelay {
			malas = append(malas, fmt.Errorf("línea %d: tipo %q desconocido (arranque o relay)", n, rol))
			continue
		}
		if _, err := nodoRedValido(dir); err != nil {
			malas = append(malas, fmt.Errorf("línea %d: %w", n, err))
			continue
		}
		if vistos[rol+dir] {
			continue
		}
		vistos[rol+dir] = true
		if rol == RolRelay {
			relays = append(relays, dir)
		} else {
			arranque = append(arranque, dir)
		}
	}
	if err := sc.Err(); err != nil {
		malas = append(malas, err)
	}
	return
}

// ArranqueFabrica: la lista compilada (la de go-libp2p-kad-dht), como texto.
func ArranqueFabrica() []string {
	var r []string
	for _, a := range dht.DefaultBootstrapPeers {
		r = append(r, a.String())
	}
	return r
}

// ---------------------------------------------------------------- guardada en <datos>

// LeerRedFirmada: <datos>\red-firmada.json comprobada (nil si no hay o no vale).
func LeerRedFirmada(datos string) *ListaRed {
	b, err := leerPequeno(filepath.Join(datos, NombreRedFirmada), maxFicheroRed)
	if err != nil {
		return nil
	}
	var l ListaRed
	if json.Unmarshal(b, &l) != nil || l.Verificar() != nil {
		return nil
	}
	return &l
}

// GuardarRedFirmada: guarda l en <datos>\red-firmada.json si está firmada y es MÁS NUEVA que la guardada. true = guardada.
func GuardarRedFirmada(datos string, l *ListaRed) (bool, error) {
	if l == nil {
		return false, nil
	}
	if err := l.Verificar(); err != nil {
		return false, err
	}
	if g := LeerRedFirmada(datos); g != nil && g.Version >= l.Version {
		return false, nil
	}
	b, err := json.MarshalIndent(l, "", " ")
	if err != nil {
		return false, err
	}
	if err := os.MkdirAll(datos, 0o700); err != nil {
		return false, err
	}
	return true, escribirAtomico(filepath.Join(datos, NombreRedFirmada), b)
}

func leerPequeno(p string, max int64) ([]byte, error) {
	st, err := os.Stat(p)
	if err != nil {
		return nil, err
	}
	if st.Size() > max {
		return nil, fmt.Errorf("%s: demasiado grande", p)
	}
	return os.ReadFile(p)
}

// ---------------------------------------------------------------- la unión

// FuenteRed: una lista firmada candidata y de dónde viene (para elegir la más nueva).
type FuenteRed struct {
	Lista  *ListaRed
	Origen string // OrigenFirmadaPaq | OrigenFirmadaCach
}

// RedEfectiva: la lista que se usa (unión) y de dónde sale cada parte (para Ajustes y el log).
type RedEfectiva struct {
	Arranque []peer.AddrInfo
	Relays   []peer.AddrInfo

	VersionFirmada int64  // 0 = ninguna lista firmada
	OrigenFirmada  string // OrigenFirmadaPaq | OrigenFirmadaCach
	NFirmada       int    // nodos (arranque + relays) de la firmada
	NUsuario       int    // nodos válidos de red-nodos.txt
	ErroresUsuario []string
	NFabrica       int
}

// unir: añade los nodos a la lista (por peer ID: el primero que lo trae manda en el orden; las direcciones se suman).
func unir(dst []peer.AddrInfo, idx map[peer.ID]int, dirs []string) []peer.AddrInfo {
	for _, s := range dirs {
		ai, err := nodoRedValido(s)
		if err != nil {
			continue
		}
		if i, ok := idx[ai.ID]; ok {
			for _, a := range ai.Addrs {
				if !contieneAddr(dst[i].Addrs, a) {
					dst[i].Addrs = append(dst[i].Addrs, a)
				}
			}
			continue
		}
		idx[ai.ID] = len(dst)
		dst = append(dst, *ai)
	}
	return dst
}

func contieneAddr(l []ma.Multiaddr, a ma.Multiaddr) bool {
	for _, x := range l {
		if x.Equal(a) {
			return true
		}
	}
	return false
}

// CalcularRed: la unión de la firmada más nueva de las candidatas (y de la guardada en <datos>), la del usuario y la de
// fábrica. Si la más nueva no es la guardada, se guarda (así sobrevive aunque el paquete cambie). datos = "" no lee ni
// guarda nada en disco (solo candidatas y fábrica).
func CalcularRed(datos string, candidatas []FuenteRed, logf func(string, ...any)) RedEfectiva {
	if logf == nil {
		logf = func(string, ...any) {}
	}
	var mejor *FuenteRed
	if datos != "" {
		if g := LeerRedFirmada(datos); g != nil {
			mejor = &FuenteRed{Lista: g, Origen: OrigenFirmadaCach}
		}
	}
	for i := range candidatas {
		c := candidatas[i]
		if c.Lista == nil {
			continue
		}
		if err := c.Lista.Verificar(); err != nil {
			logf("[red] lista de nodos de %s descartada: %v", c.Origen, err)
			continue
		}
		if mejor == nil || c.Lista.Version > mejor.Lista.Version {
			mejor = &c
		}
	}
	var r RedEfectiva
	idxA, idxR := map[peer.ID]int{}, map[peer.ID]int{}
	if mejor != nil {
		r.VersionFirmada, r.OrigenFirmada = mejor.Lista.Version, mejor.Origen
		r.NFirmada = len(mejor.Lista.Arranque) + len(mejor.Lista.Relays)
		r.Arranque = unir(r.Arranque, idxA, mejor.Lista.Arranque)
		r.Relays = unir(r.Relays, idxR, mejor.Lista.Relays)
		if datos != "" && mejor.Origen != OrigenFirmadaCach {
			if ok, err := GuardarRedFirmada(datos, mejor.Lista); err != nil {
				logf("[red] no pude guardar la lista firmada: %v", err)
			} else if ok {
				logf("[red] lista de nodos firmada %d guardada en %s", mejor.Lista.Version, NombreRedFirmada)
			}
		}
	}
	if datos != "" {
		if b, err := leerPequeno(filepath.Join(datos, NombreRedUsuario), maxFicheroRed); err == nil {
			a, rl, malas := LeerNodosTexto(b)
			if len(a) > maxNodosRed {
				a = a[:maxNodosRed]
			}
			if len(rl) > maxNodosRed {
				rl = rl[:maxNodosRed]
			}
			r.NUsuario = len(a) + len(rl)
			for _, e := range malas {
				r.ErroresUsuario = append(r.ErroresUsuario, e.Error())
				logf("[red] %s: %v", NombreRedUsuario, e)
			}
			r.Arranque = unir(r.Arranque, idxA, a)
			r.Relays = unir(r.Relays, idxR, rl)
		}
	}
	fab := ArranqueFabrica()
	r.NFabrica = len(fab)
	r.Arranque = unir(r.Arranque, idxA, fab)
	logf("[red] nodos de arranque: %d (firmada v%d de %q: %d, usuario: %d, fábrica: %d); relays fijos: %d",
		len(r.Arranque), r.VersionFirmada, r.OrigenFirmada, r.NFirmada, r.NUsuario, r.NFabrica, len(r.Relays))
	return r
}
