package nucleo

// Contraseñas de servidor (modo «Password», sala/contrasena.go). Nunca se guarda una contraseña en claro:
//
//   - Anfitrión: al escribirla en el formulario se deriva K (Argon2id con sal nueva) y en lanzador.json solo queda
//     {kdf, sal, K protegida con DPAPI, peer ID}. Para cambiarla se vuelve a escribir; vacía = la de antes.
//   - Invitado, «Remember password»: <datos>\lista\claves.json con {peer ID -> kdf, sal, K protegida con DPAPI}. K solo
//     vale para ESE servidor y esa sal (si el anfitrión cambia la contraseña, deja de valer y se vuelve a pedir).

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"sync"

	"yo2online/sala"
)

// ClaveGuardada: una clave derivada en disco (K va protegida con DPAPI).
type ClaveGuardada struct {
	KDF   byte   `json:"kdf"`
	Sal   string `json:"sal"`   // base64
	Clave string `json:"clave"` // base64 de DPAPI(K)
	Peer  string `json:"peer,omitempty"`
}

var b64 = base64.StdEncoding

func guardarClave(ver byte, sal, k []byte, peer string) (*ClaveGuardada, error) {
	p, err := proteger(k)
	if err != nil {
		return nil, err
	}
	return &ClaveGuardada{KDF: ver, Sal: b64.EncodeToString(sal), Clave: b64.EncodeToString(p), Peer: peer}, nil
}

// abrir: (versión, sal, K) o ok=false si no se puede leer (otro usuario u otro PC, fichero tocado...).
func (c *ClaveGuardada) abrir() (ver byte, sal, k []byte, ok bool) {
	if c == nil {
		return 0, nil, nil, false
	}
	sal, err1 := b64.DecodeString(c.Sal)
	p, err2 := b64.DecodeString(c.Clave)
	if err1 != nil || err2 != nil || len(sal) != sala.LenSalClave {
		return 0, nil, nil, false
	}
	k, err := desproteger(p)
	if err != nil || len(k) != sala.LenClave {
		return 0, nil, nil, false
	}
	return c.KDF, sal, k, true
}

// claveServidor: la del anfitrión, si está guardada para ESTA identidad (peer) y se puede leer.
func (c *ClaveGuardada) claveServidor(peer string) *sala.ClaveServidor {
	if c == nil || c.Peer != peer {
		return nil
	}
	ver, sal, k, ok := c.abrir()
	if !ok {
		return nil
	}
	cs := &sala.ClaveServidor{Ver: ver, Sal: sal, Clave: k}
	if !cs.Valida() {
		return nil
	}
	return cs
}

// ---------------------------------------------------------------- invitado: «Remember password»

const ficheroClaves = "claves.json"

type almacenClaves struct {
	mu   sync.Mutex
	ruta string
	m    map[string]*ClaveGuardada
}

func abrirClaves(datos string) *almacenClaves {
	ac := &almacenClaves{ruta: filepath.Join(datos, "lista", ficheroClaves), m: map[string]*ClaveGuardada{}}
	if b, err := os.ReadFile(ac.ruta); err == nil {
		var f struct {
			V      int                       `json:"v"`
			Claves map[string]*ClaveGuardada `json:"claves"`
		}
		if json.Unmarshal(b, &f) == nil && f.V == 1 && f.Claves != nil {
			ac.m = f.Claves
		}
	}
	return ac
}

func (ac *almacenClaves) guardarLocked() error {
	b, err := json.MarshalIndent(struct {
		V      int                       `json:"v"`
		Claves map[string]*ClaveGuardada `json:"claves"`
	}{1, ac.m}, "", " ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(ac.ruta), 0o700); err != nil {
		return err
	}
	tmp := ac.ruta + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, ac.ruta)
}

// tiene: hay una clave guardada para ese servidor.
func (ac *almacenClaves) tiene(id string) bool {
	ac.mu.Lock()
	defer ac.mu.Unlock()
	return ac.m[id] != nil
}

// credencial: la guardada para ese servidor (nil si no hay o no se puede leer).
func (ac *almacenClaves) credencial(id string) *sala.Credencial {
	ac.mu.Lock()
	c := ac.m[id]
	ac.mu.Unlock()
	ver, sal, k, ok := c.abrir()
	if !ok {
		return nil
	}
	return sala.CredencialGuardada(ver, sal, k)
}

// recordar guarda la clave derivada de una entrada que salió bien.
func (ac *almacenClaves) recordar(id string, cr *sala.Credencial) error {
	ver, sal, k, ok := cr.Derivada()
	if !ok {
		return nil
	}
	g, err := guardarClave(ver, sal, k, "")
	if err != nil {
		return err
	}
	ac.mu.Lock()
	defer ac.mu.Unlock()
	ac.m[id] = g
	return ac.guardarLocked()
}

// olvidar borra la de ese servidor (si había).
func (ac *almacenClaves) olvidar(id string) error {
	ac.mu.Lock()
	defer ac.mu.Unlock()
	if ac.m[id] == nil {
		return nil
	}
	delete(ac.m, id)
	return ac.guardarLocked()
}
