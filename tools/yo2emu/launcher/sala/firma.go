package sala

// Firma de EDITOR de las versiones de JoinServer.exe (versión mínima de TUF, ver research/descentralizado/RIESGOS.md §2.1).
//
// - La clave PRIVADA ed25519 del editor vive SOLO en el PC del owner: %LOCALAPPDATA%\YO2Online\firma\editor-ed25519.key
//   (la crea `firmar -generar`; nunca en el repo ni en launcher\).
// - La clave PÚBLICA va aquí abajo, incrustada en cada JoinServer.exe.
// - Cada build se firma (build.ps1 llama a firmar.exe) -> dist\JoinServer.exe.firma: JSON con nombre, versión, sha256 y
//   tamaño, firmado. El anfitrión lo reparte junto al exe; el invitado SOLO instala si la firma vale con esta clave
//   pública, el sha256 del fichero descargado coincide con el FIRMADO y la versión es MAYOR que la suya (anti-rollback).
//   El sha256 que manda el anfitrión en su manifiesto ya no basta: un anfitrión falso o un invitado no puede firmar.

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
)

// ClaveEditorHex: clave PÚBLICA del editor (generada el 2026-10-06 con `firmar -generar`).
const ClaveEditorHex = "92186ff14b00e8fc1b009639a5ab51ff06aa3a6633df86b287fec5f37dd8dde9"

const (
	ExtFirma      = ".firma"
	etqLanzador   = "yo2online-lanzador-v1"
	FirmaVersionV = 1
)

type FirmaLanzador struct {
	V       int    `json:"v"`
	Nombre  string `json:"nombre"`
	Version int64  `json:"version"`
	SHA256  string `json:"sha256"`
	Tam     int64  `json:"tam"`
	Firma   string `json:"firma"` // base64 de la firma ed25519 de Mensaje()
}

// Mensaje: lo que se firma (texto fijo, sin ambigüedad de JSON).
func (f *FirmaLanzador) Mensaje() []byte {
	return []byte(fmt.Sprintf("%s\nnombre=%s\nversion=%d\nsha256=%s\ntam=%d\n", etqLanzador, f.Nombre, f.Version, f.SHA256, f.Tam))
}

// claveEditorHex: la que se usa de verdad. Es ClaveEditorHex; solo las pruebas la cambian (por una clave de prueba
// generada en el momento: la privada del editor nunca está en el repo ni en las pruebas).
var claveEditorHex = ClaveEditorHex

func ClaveEditor() (ed25519.PublicKey, error) {
	b, err := hex.DecodeString(claveEditorHex)
	if err != nil || len(b) != ed25519.PublicKeySize {
		return nil, errors.New("este JoinServer no lleva clave pública de editor: no acepta actualizaciones")
	}
	return ed25519.PublicKey(b), nil
}

// Verificar: firma válida (de JoinServer.exe) con la clave pública incrustada y campos bien formados.
func (f *FirmaLanzador) Verificar() error { return f.VerificarPara(NombreLanzador) }

// VerificarPara: lo mismo para el exe de ese nombre (NombreLanzador o NombreLanzadorApp). La firma lleva el nombre
// dentro de lo firmado: la de un exe no sirve para el otro.
func (f *FirmaLanzador) VerificarPara(nombre string) error {
	if f == nil {
		return errors.New("sin firma")
	}
	if nombre != NombreLanzador && nombre != NombreLanzadorApp {
		return errors.New("nombre de exe desconocido")
	}
	if f.V != FirmaVersionV || f.Nombre != nombre || len(f.SHA256) != 64 || f.Tam <= 0 || f.Version <= 0 {
		return errors.New("firma con campos no válidos")
	}
	pub, err := ClaveEditor()
	if err != nil {
		return err
	}
	sig, err := base64.StdEncoding.DecodeString(f.Firma)
	if err != nil || len(sig) != ed25519.SignatureSize || !ed25519.Verify(pub, f.Mensaje(), sig) {
		return errors.New("la firma NO es la nuestra (no la ha hecho la clave del editor)")
	}
	return nil
}

// LeerFirma: el fichero <exe>.firma.
func LeerFirma(p string) (*FirmaLanzador, error) {
	b, err := os.ReadFile(p)
	if err != nil {
		return nil, err
	}
	var f FirmaLanzador
	if err := json.Unmarshal(b, &f); err != nil {
		return nil, err
	}
	return &f, nil
}
