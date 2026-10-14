//go:build windows

package juego

import (
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
)

// ProcesoActivo: ¿hay algún proceso con ese nombre de exe (p. ej. «yo2.exe»)? Sin lanzar tasklist: eso abriría una
// ventana negra desde el lanzador con ventana.
func ProcesoActivo(nombre string) bool {
	snap, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return false
	}
	defer windows.CloseHandle(snap)
	var pe windows.ProcessEntry32
	pe.Size = uint32(unsafe.Sizeof(pe))
	if windows.Process32First(snap, &pe) != nil {
		return false
	}
	for {
		if strings.EqualFold(windows.UTF16ToString(pe.ExeFile[:]), nombre) {
			return true
		}
		if windows.Process32Next(snap, &pe) != nil {
			return false
		}
	}
}
