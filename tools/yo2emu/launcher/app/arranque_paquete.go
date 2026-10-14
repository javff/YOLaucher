package main

// Al arrancar, antes de abrir la ventana: si hay una versión del paquete preparada (app/nucleo/paquete_act.go), se
// aplica aquí, cuando todavía no está abierto nada. Si cambia el exe, se arranca el nuevo y este proceso sale.

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
	"yo2online/app/nucleo"
)

// argSoloAplicar: «YO2OnlineLauncher.exe -aplicar-actualizacion» aplica lo preparado y sale, sin ventana y sin relanzar
// (para comprobar a mano una actualización atascada, y para la prueba con exes de verdad).
const argSoloAplicar = "-aplicar-actualizacion"

// aplicarPaqueteAlArrancar: true = ya está en marcha el exe nuevo y este proceso tiene que salir.
func aplicarPaqueteAlArrancar(exe string, offset int, logf func(string, ...any)) bool {
	if n := otrasInstancias(exe); n > 0 {
		// otra ventana de este mismo lanzador abierta: ahora no se cambia nada (se aplicará en el próximo arranque)
		logf("[paquete] hay %d proceso(s) más de %s: la actualización se aplicará en otro arranque", n, filepath.Base(exe))
		return false
	}
	if !nucleo.AplicarAlArrancar(exe, offset, logf) || (len(os.Args) > 1 && os.Args[1] == argSoloAplicar) {
		return false
	}
	cmd := exec.Command(exe, os.Args[1:]...)
	cmd.Env = append(os.Environ(), nucleo.EnvPadrePID+"="+strconv.Itoa(os.Getpid()))
	if err := cmd.Start(); err != nil {
		logf("[paquete] no puedo arrancar el lanzador nuevo (%v): sigo con este proceso", err)
		return false
	}
	logf("[paquete] lanzador nuevo arrancado (pid %d); este proceso sale", cmd.Process.Pid)
	return true
}

// otrasInstancias: procesos con el mismo nombre de exe que este, sin contar este ni el que lo arrancó (el lanzador
// viejo que se acaba de cerrar para reiniciar puede tardar un momento en desaparecer de la lista).
func otrasInstancias(exe string) int {
	snap, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return 0
	}
	defer windows.CloseHandle(snap)
	type proc struct {
		pid, padre uint32
		nombre     string
	}
	var ps []proc
	var pe windows.ProcessEntry32
	pe.Size = uint32(unsafe.Sizeof(pe))
	for err := windows.Process32First(snap, &pe); err == nil; err = windows.Process32Next(snap, &pe) {
		ps = append(ps, proc{pe.ProcessID, pe.ParentProcessID, windows.UTF16ToString(pe.ExeFile[:])})
	}
	yo, padre := uint32(os.Getpid()), uint32(0)
	for _, p := range ps {
		if p.pid == yo {
			padre = p.padre
		}
	}
	n := 0
	for _, p := range ps {
		if p.pid != yo && p.pid != padre && strings.EqualFold(p.nombre, filepath.Base(exe)) {
			n++
		}
	}
	return n
}
