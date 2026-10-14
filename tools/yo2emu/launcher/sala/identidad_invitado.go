package sala

// T2 (research/seguridad_duelo/PROPUESTA.md §5): identidad del invitado con clave.
//
// Antes el id del invitado eran 16 bytes aleatorios (id-invitado.bin) que el invitado mandaba tal cual: cualquiera que
// lo viera (sale entero en el log del anfitrión) podía entrar con el mismo id y, con él, con la misma IP 127.0.0.X
// en run.py. Ahora cada JoinServer tiene su propia clave ed25519 (invitado-ed25519.key en %LOCALAPPDATA%\YO2Online,
// nunca sale de ese equipo), su id es un resumen de la clave PÚBLICA y en el saludo FIRMA los dos nonces: el anfitrión
// calcula el id él mismo a partir de la pública y comprueba la firma. Saber el id ya no sirve para nada.

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"errors"
	"os"
	"path/filepath"
)

// NombreClaveInvitado: fichero con la semilla ed25519 (32 bytes) del invitado, en su carpeta de datos.
const NombreClaveInvitado = "invitado-ed25519.key"

// CargarClaveInvitado lee (o crea, la primera vez) la clave del invitado en dir. Si el fichero existe pero está dañado,
// NO lo pisa (se perdería la identidad): devuelve error para que se mire.
func CargarClaveInvitado(dir string) (ed25519.PrivateKey, bool, error) {
	p := filepath.Join(dir, NombreClaveInvitado)
	if b, err := os.ReadFile(p); err == nil {
		if len(b) != ed25519.SeedSize {
			return nil, false, errors.New(p + " está dañado (no tiene 32 bytes): muévelo a otro sitio para crear uno nuevo")
		}
		return ed25519.NewKeyFromSeed(b), false, nil
	} else if !os.IsNotExist(err) {
		return nil, false, err
	}
	seed := make([]byte, ed25519.SeedSize)
	if _, err := rand.Read(seed); err != nil {
		return nil, false, err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, false, err
	}
	// O_EXCL: si dos JoinServer arrancan a la vez, el segundo usa la del primero en vez de pisarla
	f, err := os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		if os.IsExist(err) {
			return CargarClaveInvitado(dir)
		}
		return nil, false, err
	}
	_, err = f.Write(seed)
	if e := f.Close(); err == nil {
		err = e
	}
	if err != nil {
		os.Remove(p)
		return nil, false, err
	}
	return ed25519.NewKeyFromSeed(seed), true, nil
}

// IDInvitado: el id con el que el anfitrión conoce a un invitado = resumen de su clave pública (16 bytes, como antes,
// así el log y la consola del anfitrión no cambian de forma).
func IDInvitado(pub ed25519.PublicKey) [16]byte {
	h := sha256.Sum256(append([]byte("yo2online-id-invitado-v3"), pub...))
	var id [16]byte
	copy(id[:], h[:16])
	return id
}
