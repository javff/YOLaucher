//go:build windows

package sala

import "golang.org/x/sys/windows"

const (
	enableQuickEditMode = 0x0040 // ENABLE_QUICK_EDIT_MODE
	enableExtendedFlags = 0x0080 // ENABLE_EXTENDED_FLAGS: sin él, Windows ignora el cambio de QuickEdit
)

// DesactivarQuickEdit quita el modo «Edición rápida» de la consola de este proceso: con él, un clic en la ventana la
// pone en «Seleccionar» y CONGELA todo lo que se escribe en ella (ver consola.go). Devuelve cómo dejarla como estaba
// (el modo es de la consola, no del proceso: si se abrió desde un cmd, el cmd lo heredaría).
//
// Sin consola (redirigido, servicio, pruebas) no hace nada y no falla: devuelve una función vacía y el error, que solo
// es informativo.
func DesactivarQuickEdit() (restaurar func(), err error) {
	nada := func() {}
	var modo uint32
	h, err := windows.GetStdHandle(windows.STD_INPUT_HANDLE)
	if err != nil || h == 0 || h == windows.InvalidHandle || windows.GetConsoleMode(h, &modo) != nil {
		// stdin redirigido: se prueba con la consola directamente (si no hay consola, CONIN$ no se abre)
		h, err = windows.CreateFile(windows.StringToUTF16Ptr("CONIN$"), windows.GENERIC_READ|windows.GENERIC_WRITE,
			windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE, nil, windows.OPEN_EXISTING, 0, 0)
		if err != nil {
			return nada, err
		}
		if err := windows.GetConsoleMode(h, &modo); err != nil {
			windows.CloseHandle(h)
			return nada, err
		}
	}
	nuevo := (modo | enableExtendedFlags) &^ enableQuickEditMode
	if nuevo == modo {
		return nada, nil
	}
	if err := windows.SetConsoleMode(h, nuevo); err != nil {
		return nada, err
	}
	return func() { windows.SetConsoleMode(h, modo) }, nil
}
