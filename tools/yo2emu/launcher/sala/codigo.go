// Package sala: el «código de sala» de YO2-Online y todo lo que se deriva de él.
//
// Formato v2 (desde el 2026-10-06; el v1 derivaba la clave PRIVADA del anfitrión del código y cualquier invitado podía
// suplantarlo, ver research/descentralizado/RIESGOS.md §0):
//
//	código = base32( clave PÚBLICA ed25519 del anfitrión (32) + secreto de entrada (12) + control (1) )
//	       = «YO2-» + 72 caracteres en 12 grupos de 6.
//
// La clave PRIVADA del anfitrión es aleatoria y vive solo en su PC (%LOCALAPPDATA%\YO2Online\anfitrion\identidad.key).
// Con esa misma clave el anfitrión es:
//   - su identidad libp2p (peer ID = clave pública): Noise/TLS de libp2p garantiza que el invitado habla con ella;
//   - su servicio .onion v3 (la dirección .onion ES la clave pública): Tor garantiza lo mismo;
//   - y firma el saludo del túnel (tunel.go), así que la comprobación no depende del transporte.
//
// Del secreto de entrada (que tienen todos los invitados) salen, con HKDF-SHA256: el punto de encuentro en la DHT, el
// nombre mDNS y la clave HMAC que el invitado tiene que demostrar conocer para entrar. Un invitado puede ENTRAR, pero ya
// no puede hacerse pasar por el anfitrión: le falta la clave privada.
package sala

import (
	"crypto/ed25519"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"crypto/sha3"
	"crypto/sha512"
	"encoding/base32"
	"errors"
	"os"
	"path/filepath"
	"strings"

	"github.com/ipfs/go-cid"
	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/core/peer"
	"github.com/multiformats/go-multihash"
)

const (
	lenPub     = ed25519.PublicKeySize // 32
	lenSecreto = 12
	lenV1      = 15 // 14 de secreto + 1 de control (formato antiguo, ya no se acepta)
	lenV2      = lenPub + lenSecreto + 1
)

var b32 = base32.StdEncoding.WithPadding(base32.NoPadding)

// ErrCodigoV1: el código es del formato antiguo (inseguro).
var ErrCodigoV1 = errors.New("ese código de sala es del formato ANTIGUO (inseguro, anterior al 06-10): " +
	"pide al anfitrión el código nuevo (lo muestra su OpenServer actualizado)")

type Sala struct {
	pub     ed25519.PublicKey  // clave pública del anfitrión (va en el código)
	secreto [lenSecreto]byte   // secreto de entrada (va en el código)
	priv    ed25519.PrivateKey // solo en el anfitrión (nil en los invitados)
	// publica: sala PÚBLICA (lista pública, ver ficha.go): su secreto de entrada no es secreto (va en la ficha firmada)
	// y el saludo usa otra magia («YO2P3»): un anfitrión privado la rechaza siempre. Nunca tiene código que enseñar.
	publica bool
	// cred: contraseña del servidor (solo en la copia del invitado de una sala pública con contraseña; ver contrasena.go).
	cred *Credencial
}

// ---------------------------------------------------------------- sala pública (lista pública, NOTES-red.md §8)

// Publica: la sala pública de este anfitrión (solo anfitrión). Misma clave (mismo peer ID y .onion), pero OTRO
// secreto de entrada, sacado de su clave privada con HKDF: es estable entre arranques, no hace falta guardarlo y
// publicarlo no dice nada del secreto del código privado ni de la clave.
func (s *Sala) Publica() *Sala {
	b, err := hkdf.Key(sha256.New, s.priv.Seed(), []byte("yo2online-sala-publica-v1"), "secreto de entrada publico", lenSecreto)
	if err != nil {
		panic(err)
	}
	p := &Sala{pub: s.pub, priv: s.priv, publica: true}
	copy(p.secreto[:], b)
	return p
}

// SalaPublica: la sala pública de un anfitrión de la lista (invitado): su clave pública y el secreto de la ficha.
func SalaPublica(pub ed25519.PublicKey, secreto []byte) (*Sala, error) {
	if len(pub) != lenPub || len(secreto) != lenSecreto {
		return nil, errors.New("datos de la sala pública mal formados")
	}
	s := &Sala{pub: append(ed25519.PublicKey{}, pub...), publica: true}
	copy(s.secreto[:], secreto)
	return s, nil
}

// EsPublica: sala de la lista pública (sin código).
func (s *Sala) EsPublica() bool { return s.publica }

// SecretoPublico: el secreto de entrada en base32 para la ficha (solo salas públicas: el de una privada es el código).
func (s *Sala) SecretoPublico() string {
	if !s.publica {
		panic("SecretoPublico de una sala privada")
	}
	return b32.EncodeToString(s.secreto[:])
}

// Pub: la clave pública del anfitrión.
func (s *Sala) Pub() ed25519.PublicKey { return s.pub }

func control(b []byte) byte {
	h := sha256.Sum256(append([]byte("yo2-codigo-v2"), b...))
	return h[0]
}

// Codigo: «YO2-» + 72 caracteres base32 en grupos de 6. ES SECRETO: no escribirlo en logs.
func (s *Sala) Codigo() string {
	if s.publica {
		return "" // una sala pública no tiene código (su secreto va en la ficha; nunca se enseña como código)
	}
	raw := append(append([]byte{}, s.pub...), s.secreto[:]...)
	raw = append(raw, control(raw))
	e := b32.EncodeToString(raw) // 45 bytes -> 72 caracteres
	var g []string
	for i := 0; i < len(e); i += 6 {
		g = append(g, e[i:i+6])
	}
	return "YO2-" + strings.Join(g, "-")
}

// Huella: 8 caracteres que identifican al anfitrión (salen de su clave PÚBLICA): se pueden enseñar y escribir en el
// log; no dan acceso a la sala. Sirve para comprobar de palabra que dos personas hablan de la misma sala.
func (s *Sala) Huella() string {
	h := sha256.Sum256(append([]byte("yo2-huella"), s.pub...))
	e := b32.EncodeToString(h[:5])
	return e[:4] + "-" + e[4:8]
}

// Errores de Parse (la ventana los reconoce con errors.Is y enseña un mensaje traducido).
var (
	ErrCodigoFormato = errors.New("el código de sala no tiene el formato YO2-XXXXXX-XXXXXX-... (12 grupos de 6)")
	ErrCodigoLetra   = errors.New("el código de sala tiene alguna letra mal copiada")
)

// Parse acepta el código con o sin «YO2-», guiones, espacios y en minúsculas; la O y la 0 / la I y el 1 se corrigen.
func Parse(c string) (*Sala, error) {
	c = strings.ToUpper(strings.TrimSpace(strings.TrimPrefix(c, string(rune(0xFEFF)))))
	c = strings.TrimPrefix(c, "YO2")
	r := strings.NewReplacer("-", "", " ", "", "\t", "", "\r", "", "\n", "", "0", "O", "1", "I", "8", "B")
	c = r.Replace(c)
	raw, err := b32.DecodeString(c)
	if err == nil && len(raw) == lenV1 {
		return nil, ErrCodigoV1
	}
	if err != nil || len(raw) != lenV2 {
		return nil, ErrCodigoFormato
	}
	if control(raw[:lenV2-1]) != raw[lenV2-1] {
		return nil, ErrCodigoLetra
	}
	s := &Sala{pub: ed25519.PublicKey(append([]byte{}, raw[:lenPub]...))}
	copy(s.secreto[:], raw[lenPub:])
	return s, nil
}

// ---------------------------------------------------------------- anfitrión

// CargarAnfitrion lee (o crea) la identidad del anfitrión y su sala:
//   - dir\identidad.key: semilla ed25519 de 32 bytes, aleatoria, que NUNCA sale de este PC;
//   - dir\sala.txt: el código (clave pública + secreto de entrada).
//
// nuevoSecreto: cambia solo el secreto de entrada (para echar a quien tenga el código viejo); nuevaIdentidad: cambia
// también la clave (si se sospecha que la han copiado). Devuelve además si el código ha cambiado (para avisar).
func CargarAnfitrion(dir string, nuevoSecreto, nuevaIdentidad bool) (s *Sala, cambiado bool, err error) {
	kp := filepath.Join(dir, "identidad.key")
	var seed []byte
	if b, e := os.ReadFile(kp); e == nil && len(b) == ed25519.SeedSize && !nuevaIdentidad {
		seed = b
	} else {
		if e == nil && !nuevaIdentidad {
			return nil, false, errors.New(kp + " está dañado (no tiene 32 bytes): muévelo a otro sitio para crear uno nuevo")
		}
		if existeF(kp) {
			os.Rename(kp, kp+".antes") // por si se cambió sin querer: se puede volver atrás
		}
		seed = make([]byte, ed25519.SeedSize)
		if _, err := rand.Read(seed); err != nil {
			return nil, false, err
		}
		if err := os.WriteFile(kp, seed, 0o600); err != nil {
			return nil, false, err
		}
		cambiado = true
	}
	priv := ed25519.NewKeyFromSeed(seed)
	s = &Sala{pub: priv.Public().(ed25519.PublicKey), priv: priv}

	sp := filepath.Join(dir, "sala.txt")
	reusar := false
	if b, e := os.ReadFile(sp); e == nil && !nuevoSecreto {
		if v, e := Parse(string(b)); e == nil && v.pub.Equal(s.pub) {
			s.secreto = v.secreto
			reusar = true
		} else if errors.Is(e, ErrCodigoV1) {
			os.Rename(sp, sp+".v1-antiguo") // el código viejo ya no sirve; se guarda por si hay que comparar
		}
	}
	if !reusar {
		if _, err := rand.Read(s.secreto[:]); err != nil {
			return nil, false, err
		}
		if err := os.WriteFile(sp, []byte(s.Codigo()+"\r\n"), 0o600); err != nil {
			return nil, false, err
		}
		cambiado = true
	}
	return s, cambiado, nil
}

func existeF(p string) bool { _, err := os.Stat(p); return err == nil }

// EsAnfitrion: tiene la clave privada.
func (s *Sala) EsAnfitrion() bool { return s.priv != nil }

// Firmar: firma ed25519 con la clave del anfitrión (solo anfitrión).
func (s *Sala) Firmar(m []byte) []byte { return ed25519.Sign(s.priv, m) }

// Verificar: comprueba una firma del anfitrión con la clave pública del código.
func (s *Sala) Verificar(m, firma []byte) bool {
	return len(firma) == ed25519.SignatureSize && ed25519.Verify(s.pub, m, firma)
}

// ---------------------------------------------------------------- derivaciones

func (s *Sala) derivar(etiqueta string, n int) []byte {
	ikm := append(append([]byte{}, s.secreto[:]...), s.pub...)
	b, err := hkdf.Key(sha256.New, ikm, []byte("yo2online-sala-v2"), etiqueta, n)
	if err != nil {
		panic(err)
	}
	return b
}

// ClaveP2P: identidad libp2p del anfitrión (solo anfitrión): la misma clave ed25519.
func (s *Sala) ClaveP2P() crypto.PrivKey {
	k, err := crypto.UnmarshalEd25519PrivateKey(s.priv)
	if err != nil {
		panic(err)
	}
	return k
}

// PeerID: el peer ID libp2p del anfitrión, sacado de la clave pública del código.
func (s *Sala) PeerID() peer.ID {
	k, err := crypto.UnmarshalEd25519PublicKey(s.pub)
	if err != nil {
		panic(err)
	}
	id, err := peer.IDFromPublicKey(k)
	if err != nil {
		panic(err)
	}
	return id
}

// Encuentro: el CID que el anfitrión anuncia (Provide) en la DHT pública de libp2p/IPFS.
func (s *Sala) Encuentro() cid.Cid {
	mh, err := multihash.Sum(s.derivar("encuentro-dht", 32), multihash.SHA2_256, -1)
	if err != nil {
		panic(err)
	}
	return cid.NewCidV1(cid.Raw, mh)
}

// NombreMDNS: servicio mDNS para encontrarse en la misma red local sin Internet.
func (s *Sala) NombreMDNS() string {
	return "yo2-" + strings.ToLower(b32.EncodeToString(s.derivar("mdns", 10)))
}

func (s *Sala) ClaveAuth() []byte { return s.derivar("autenticacion", 32) }

// Onion: la dirección v3 (sin «.onion»): es la clave pública del anfitrión.
func (s *Sala) Onion() string {
	ver := byte(3)
	ck := sha3.Sum256(append(append([]byte(".onion checksum"), s.pub...), ver))
	raw := append(append(append([]byte{}, s.pub...), ck[:2]...), ver)
	return strings.ToLower(b32.EncodeToString(raw))
}

// OnionSecreto: la clave secreta expandida de 64 bytes que Tor guarda en hs_ed25519_secret_key (solo anfitrión).
func (s *Sala) OnionSecreto() []byte {
	h := sha512.Sum512(s.priv.Seed())
	h[0] &= 248
	h[31] &= 127
	h[31] |= 64
	return h[:]
}
