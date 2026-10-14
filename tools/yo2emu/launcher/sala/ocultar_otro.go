//go:build !windows

package sala

import "os/exec"

func OcultarVentana(*exec.Cmd) {}
