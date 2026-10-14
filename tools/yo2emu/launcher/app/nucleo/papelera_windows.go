//go:build windows

package nucleo

// Delete de «My servers»: la carpeta del servidor va a la PAPELERA de Windows (se puede recuperar), nunca se borra del todo.

import (
	"errors"
	"fmt"
	"path/filepath"
	"syscall"
	"unsafe"
)

var procSHFileOperationW = syscall.NewLazyDLL("shell32.dll").NewProc("SHFileOperationW")

// SHFILEOPSTRUCTW (x64: alineación natural, la misma que Go)
type shFileOp struct {
	hwnd                  uintptr
	wFunc                 uint32
	pFrom                 *uint16
	pTo                   *uint16
	fFlags                uint16
	fAnyOperationsAborted int32
	hNameMappings         uintptr
	lpszProgressTitle     *uint16
}

const (
	foDelete           = 0x3
	fofSilent          = 0x4
	fofNoConfirmation  = 0x10
	fofAllowUndo       = 0x40 // a la papelera
	fofNoErrorUI       = 0x400
	fofWantNukeWarning = 0x4000 // si no cabe en la papelera, Windows avisa en vez de borrar del todo sin preguntar
)

func moverAPapelera(dir string) error {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	u, err := syscall.UTF16FromString(abs)
	if err != nil {
		return err
	}
	u = append(u, 0) // la lista de rutas acaba en doble cero
	op := shFileOp{wFunc: foDelete, pFrom: &u[0],
		fFlags: fofSilent | fofNoConfirmation | fofAllowUndo | fofNoErrorUI | fofWantNukeWarning}
	r, _, _ := procSHFileOperationW.Call(uintptr(unsafe.Pointer(&op)))
	if r != 0 {
		return fmt.Errorf("SHFileOperation: código %#x", r)
	}
	if op.fAnyOperationsAborted != 0 {
		return errors.New("cancelado")
	}
	return nil
}
