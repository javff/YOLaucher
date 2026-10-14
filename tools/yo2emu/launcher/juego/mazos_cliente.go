package juego

// Los 8 Structure Decks oficiales de los mazos premontados (mazos.go) se sacan del CLIENTE de cada jugador, no van
// dentro del lanzador (PUB, 12-10: ni el exe ni el código abierto llevan datos del juego). Cada uno es un .ydc del
// merge.dat del cliente 2008 (data\cardpack\<id>.ydc: 7xxx Structure Deck o 100NN receta de NPC) con, en 4 de ellos,
// las cartas prohibidas en la lista de 2008 quitadas (y en Blue-Eyes una añadida para llegar a 40): nada inventado salvo
// esa carta.
//
// Robusto: se comprueba el sha256 del .ydc del cliente y el del resultado. Si el cliente no es el esperado (otra
// versión, modificado) o no tiene merge.dat, ese mazo no se instala (ni se reintenta); si merge.dat no se puede leer
// (bloqueado, disco), se reintenta en la siguiente pasada.

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

// recetaOficial: <Nombre>.ydc = data\cardpack\<Fuente>.ydc del cliente, sin las cartas de Quita (por orden, la
// primera vez que aparece cada una en el main) y con Pone al final del main; side y extra, tal cual.
type recetaOficial struct {
	Nombre    string
	Fuente    int
	Quita     []uint16
	Pone      []uint16
	SHAFuente string // sha256 del .ydc del cliente Ver.080912
	SHA       string // sha256 del mazo resultante (el mismo que se copiaba incrustado hasta el 12-10)
}

var recetasOficiales = []recetaOficial{
	{"Blue-Eyes Dragon", 10055, []uint16{285, 1414, 1414, 707}, []uint16{1295},
		"1e2719327df8e91a3d127a9ccb218d41fcdf0e04a02d0f5d61146b1af750a8b1",
		"c747ddf87a2e4bca9f37175f1ea8dafbb18b70c18b427492f976adee1001ec01"},
	{"Crystal Beasts", 7011, nil, nil,
		"3370a3fa45f063871eecc0e398c0d7c3f705776cb2b7e56073cde5b21ccb7829",
		"3370a3fa45f063871eecc0e398c0d7c3f705776cb2b7e56073cde5b21ccb7829"},
	{"Dark Magician", 10052, []uint16{1710}, nil,
		"4cf3ea6c009f82a783604e89f0509ca6180f7994df7c0d3576af2e9c063825b1",
		"ee6a4cd383f3c8dc66f62fddb8e48f32b703de30999d3bee4d519300ab99de6e"},
	{"Destiny Heroes", 10086, nil, nil,
		"5c51d25da28b91eec96cbe9daa1394f7176c397050f4410b3ca7d9c670ebdf52",
		"5c51d25da28b91eec96cbe9daa1394f7176c397050f4410b3ca7d9c670ebdf52"},
	{"Elemental Heroes", 10060, nil, nil,
		"2721c7a67bfaa794bebc4729ea38538d09fa626aa5e59c21ac9c8bceea772eb2",
		"2721c7a67bfaa794bebc4729ea38538d09fa626aa5e59c21ac9c8bceea772eb2"},
	{"Normal Monsters", 10012, nil, nil,
		"c76d80be4da0cca5bbf664f55f2815ce63b1ad1a42d2687117b1482f852df379",
		"c76d80be4da0cca5bbf664f55f2815ce63b1ad1a42d2687117b1482f852df379"},
	{"Red-Eyes Dragon", 10069, []uint16{612}, nil,
		"41e0a89c8912680f4a870a84df178d91dbea4cc159950594b7a9cb8e8749d11a",
		"d1e4611814ab7abd9656aa30f53272c506c130738446981ce098a1dcd316f118"},
	{"Warriors", 10071, []uint16{1736}, nil,
		"9b521f56f5522c2bd373c7d822b8b2c9264f4951df202b31c8c8c4e0f32e58c5",
		"08b11fc40c3bdb79a7a5f514844aa70657ff959d57188557e0551a2ba16454c5"},
}

// RecetasOficiales: los nombres (sin .ydc) de los Structure Decks que salen del cliente.
func RecetasOficiales() []string {
	r := make([]string, len(recetasOficiales))
	for i, x := range recetasOficiales {
		r[i] = x.Nombre
	}
	return r
}

const tamYDC = 250 // 125 × u16: main [0..79], side [80..94], extra [95..124]

// Aplicar: el mazo de la receta a partir del .ydc del cliente (comprueba los dos sha256).
func (r recetaOficial) Aplicar(fuente []byte) ([]byte, error) {
	if len(fuente) != tamYDC || hexSHA(fuente) != r.SHAFuente {
		return nil, fmt.Errorf("cardpack\\%d.ydc no es el del cliente Ver.080912", r.Fuente)
	}
	v := make([]uint16, tamYDC/2)
	binary.Read(bytes.NewReader(fuente), binary.LittleEndian, v)
	quitar := map[uint16]int{}
	for _, c := range r.Quita {
		quitar[c]++
	}
	var main []uint16
	for _, c := range v[:80] {
		if c == 0 {
			continue
		}
		if quitar[c] > 0 {
			quitar[c]--
			continue
		}
		main = append(main, c)
	}
	main = append(main, r.Pone...)
	if len(main) > 80 {
		return nil, fmt.Errorf("%s: %d cartas en el main", r.Nombre, len(main))
	}
	out := make([]uint16, tamYDC/2)
	copy(out, main)
	copy(out[80:], v[80:])
	var b bytes.Buffer
	binary.Write(&b, binary.LittleEndian, out)
	if hexSHA(b.Bytes()) != r.SHA {
		return nil, fmt.Errorf("%s: el mazo resultante no es el esperado", r.Nombre)
	}
	return b.Bytes(), nil
}

func hexSHA(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

// ---------------------------------------------------------------------------------------------- merge.dat (KCEJYUGI)
// Formato (tools/kcej_dat.py): "KCEJYUGI", u32 nº de entradas, entradas de 268 B: nombre[256] con los nibbles de cada
// byte intercambiados, u32 offset, u32 tamaño original, u32 tamaño guardado (si difiere: LZSS de Okumura, anillo 4096,
// r inicial 0xFEE, flags LSB primero con 1 = literal, referencia de 2 B: off = b1 | (b2&0xF0)<<4, len = (b2&0x0F)+3).

const (
	kcejMagia = "KCEJYUGI"
	kcejReg   = 268
)

type entradaKCEJ struct {
	off, tam, guardado uint32
}

// leerKCEJ devuelve el contenido de las entradas pedidas (nombres como en el TOC, sin distinguir mayúsculas, con «\»).
// Las que no estén, no salen en el mapa.
func leerKCEJ(ruta string, nombres []string) (map[string][]byte, error) {
	f, err := os.Open(ruta)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	cab := make([]byte, 12)
	if _, err := io.ReadFull(f, cab); err != nil {
		return nil, err
	}
	if string(cab[:8]) != kcejMagia {
		return nil, errNoKCEJ
	}
	n := binary.LittleEndian.Uint32(cab[8:])
	if n == 0 || n > 1<<20 {
		return nil, errNoKCEJ
	}
	toc := make([]byte, int(n)*kcejReg)
	if _, err := io.ReadFull(f, toc); err != nil {
		return nil, err
	}
	quiero := map[string]bool{}
	for _, x := range nombres {
		quiero[strings.ToLower(x)] = true
	}
	ents := map[string]entradaKCEJ{}
	for i := 0; i < int(n); i++ {
		r := toc[i*kcejReg : (i+1)*kcejReg]
		nom := make([]byte, 0, 64)
		for _, c := range r[:256] {
			c = c<<4 | c>>4
			if c == 0 {
				break
			}
			nom = append(nom, c)
		}
		k := strings.ToLower(string(nom))
		if quiero[k] {
			ents[k] = entradaKCEJ{binary.LittleEndian.Uint32(r[256:]), binary.LittleEndian.Uint32(r[260:]),
				binary.LittleEndian.Uint32(r[264:])}
		}
	}
	out := map[string][]byte{}
	for k, e := range ents {
		if e.guardado > 1<<24 || e.tam > 1<<24 {
			return nil, errNoKCEJ
		}
		datos := make([]byte, e.guardado)
		if _, err := f.ReadAt(datos, int64(e.off)); err != nil {
			return nil, err
		}
		if e.tam != e.guardado {
			datos = lzssOkumura(datos, int(e.tam))
		}
		out[k] = datos
	}
	return out, nil
}

var errNoKCEJ = errors.New("merge.dat no es un contenedor KCEJYUGI válido")

func lzssOkumura(src []byte, tam int) []byte {
	var anillo [4096]byte
	r := 0xFEE
	out := make([]byte, 0, tam)
	i := 0
	for i < len(src) && len(out) < tam {
		flags := src[i]
		i++
		for bit := 0; bit < 8; bit++ {
			if i >= len(src) || len(out) >= tam {
				break
			}
			if flags&(1<<bit) != 0 {
				c := src[i]
				i++
				out = append(out, c)
				anillo[r] = c
				r = (r + 1) & 0xFFF
				continue
			}
			if i+1 >= len(src) {
				i = len(src)
				break
			}
			b1, b2 := int(src[i]), int(src[i+1])
			i += 2
			off := b1 | (b2&0xF0)<<4
			for k := 0; k < (b2&0x0F)+3; k++ {
				c := anillo[(off+k)&0xFFF]
				out = append(out, c)
				anillo[r] = c
				r = (r + 1) & 0xFFF
			}
		}
	}
	if len(out) > tam {
		out = out[:tam]
	}
	return out
}

// --------------------------------------------------------------------------------------------------------- caché
// Una lectura de merge.dat por carpeta del juego y sesión del lanzador (InstalarMazos corre cada 5 s).

type resultadoOficiales struct {
	mazos   map[string][]byte // <Nombre>.ydc -> contenido
	avisos  []string          // recetas que el cliente no tiene como se esperaba (no se reintentan)
	avisado bool              // los avisos ya salieron en el log
}

var (
	cacheOficialesMu sync.Mutex
	cacheOficiales   = map[string]*resultadoOficiales{}
)

// MazosOficialesDelCliente: los Structure Decks de recetasOficiales sacados del merge.dat de la carpeta del juego.
// error = no se pudo leer (se reintenta en la próxima llamada); los mazos que no cuadran van en avisos y no salen.
func MazosOficialesDelCliente(juego string) (map[string][]byte, []string, error) {
	r, err := oficiales(juego)
	if err != nil {
		return nil, nil, err
	}
	return r.mazos, r.avisos, nil
}

func oficiales(juego string) (*resultadoOficiales, error) {
	clave := strings.ToLower(filepath.Clean(juego))
	cacheOficialesMu.Lock()
	defer cacheOficialesMu.Unlock()
	if r, ok := cacheOficiales[clave]; ok {
		return r, nil
	}
	merge := filepath.Join(juego, "merge.dat")
	nombres := make([]string, len(recetasOficiales))
	for i, x := range recetasOficiales {
		nombres[i] = fmt.Sprintf(`data\cardpack\%d.ydc`, x.Fuente)
	}
	datos, err := leerKCEJ(merge, nombres)
	res := &resultadoOficiales{mazos: map[string][]byte{}}
	switch {
	case errors.Is(err, errNoKCEJ), errors.Is(err, os.ErrNotExist): // sin cliente válido: no hay oficiales (definitivo)
		res.avisos = append(res.avisos, err.Error())
	case err != nil:
		return nil, err // bloqueado, error de disco...: se reintenta
	}
	for i, x := range recetasOficiales {
		f, ok := datos[strings.ToLower(nombres[i])]
		if !ok {
			if err == nil {
				res.avisos = append(res.avisos, fmt.Sprintf("%s: falta cardpack\\%d.ydc en merge.dat", x.Nombre, x.Fuente))
			}
			continue
		}
		m, e := x.Aplicar(f)
		if e != nil {
			res.avisos = append(res.avisos, e.Error())
			continue
		}
		res.mazos[x.Nombre+".ydc"] = m
	}
	cacheOficiales[clave] = res
	return res, nil
}
