//go:build windows

package nucleo

// DPAPI (CryptProtectData del usuario de Windows): lo que se guarda en disco solo lo puede leer este usuario en este PC.
// Se usa para la clave derivada de la contraseña del servidor propio y para las de «Remember password» (claves.go).

import (
	"errors"
	"unsafe"

	"golang.org/x/sys/windows"
)

// entropiaDPAPI: liga los datos a este programa (otro programa del mismo usuario necesita también esto para leerlos).
var entropiaDPAPI = []byte("yo2online-lanzador-dpapi-v1")

func blob(b []byte) *windows.DataBlob {
	if len(b) == 0 {
		return &windows.DataBlob{}
	}
	return &windows.DataBlob{Size: uint32(len(b)), Data: &b[0]}
}

func proteger(datos []byte) ([]byte, error) {
	var out windows.DataBlob
	if err := windows.CryptProtectData(blob(datos), nil, blob(entropiaDPAPI), 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &out); err != nil {
		return nil, err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	return append([]byte{}, unsafe.Slice(out.Data, out.Size)...), nil
}

func desproteger(datos []byte) ([]byte, error) {
	if len(datos) == 0 {
		return nil, errors.New("dpapi: vacío")
	}
	var out windows.DataBlob
	if err := windows.CryptUnprotectData(blob(datos), nil, blob(entropiaDPAPI), 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &out); err != nil {
		return nil, err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	return append([]byte{}, unsafe.Slice(out.Data, out.Size)...), nil
}
