//go:build windows

package sala

import (
	"os/exec"
	"syscall"
)

const createNoWindow = 0x08000000 // CREATE_NO_WINDOW

// OcultarVentana: que el proceso hijo (python.exe, una aplicación de consola) no abra su ventana negra cuando quien
// lo arranca es una ventana (el lanzador). Sigue heredando las salidas que se le den.
func OcultarVentana(c *exec.Cmd) {
	c.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: createNoWindow}
}
