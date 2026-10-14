package nucleo

// Actualización del PAQUETE entero (UPD, 12-10; sala/paquete_man.go, paquete_red.go, paquete_inst.go) desde la
// ventana. Sin servidores: la versión firmada llega de cualquier anfitrión que la tenga.
//
//   - Lista: si la ficha de un servidor anuncia una version_paquete mayor que la nuestra, se le pide por libp2p
//     (/yo2online/actualiza/1.0.0, sin código de sala) en segundo plano (verPaquete).
//   - Join: si el anfitrión reparte paquete por el túnel (puerto 3), se pide por ahí; entonces ya no se usa la
//     autoactualización vieja de solo el exe (paqueteAlEntrar). Un anfitrión antiguo no lo tiene: sigue lo de siempre.
//   - Lo descargado queda preparado en <carpeta del lanzador>\_update y la página enseña «Update ready»; se aplica al
//     arrancar (AplicarAlArrancar, desde main.go) o con el botón (ReiniciarParaActualizar). Nunca con el servidor propio
//     en marcha.
//
// Solo se actualiza un lanzador que está DENTRO de un paquete (el del Discord). El exe de desarrollo del repo no (ese
// sigue con la autoactualización vieja), pero sí reparte: lo que deja empaquetar.py --firmar en <datos>\semilla.
//
// Lo que ve la página va por EstadoActualizacion() y el evento «paquete» (sin textos: los pone actualiza.js en inglés).

import (
	"context"
	"errors"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"sync"
	"time"

	"yo2online/sala"
)

// EstadoPaquete: la actualización del paquete, para la página.
type EstadoPaquete struct {
	Actualizable bool   // este lanzador está en un paquete (se puede actualizar solo)
	Instalada    int64  // versión firmada instalada (0 = ninguna: zip de antes del 12-10)
	Fase         string // "" | "descargando" | "lista"
	Version      int64  // la que se descarga o está lista
	Hecho, Total int64  // bytes (descargando)
	CambiaExe    bool   // la versión lista trae otro lanzador
	Reparte      int64  // versión que reparte este lanzador cuando hospeda (0 = ninguna)
}

type actPaquete struct {
	inst *sala.Instalacion

	mu       sync.Mutex
	est      EstadoPaquete
	ocupado  bool
	fallos   map[string]time.Time // fuente -> último fallo (no se insiste en 10 min)
	ultEmit  time.Time
	enCurso  int64
	semillaD string
}

const esperaTrasFallo = 10 * time.Minute

// iniciarPaquete: lo llama Nuevo. Prepara lo que se reparte al hospedar y lo que ya estaba descargado.
func (n *Nucleo) iniciarPaquete() {
	inst := sala.NuevaInstalacion(n.cfg.Exe, n.cfg.Logf)
	p := &actPaquete{inst: inst, fallos: map[string]time.Time{}, semillaD: filepath.Join(n.cfg.Datos, "semilla")}
	n.paq = p
	if n.l.Paquete == nil {
		sp := &sala.ServidorPaquete{Exe: n.cfg.Exe, Semilla: p.semillaD, Logf: n.cfg.Logf}
		if inst != nil {
			sp.Raiz = inst.Raiz
		}
		n.l.Paquete = sp
	}
	p.est.Actualizable = inst != nil
	if inst != nil {
		_, p.est.Instalada = inst.Instalada()
		if pe := inst.Pendiente(); pe != nil {
			p.est.Fase, p.est.Version, p.est.CambiaExe = "lista", pe.Version, pe.Exe
		}
		n.logf("[paquete] instalación %s, versión %d", inst.Raiz, p.est.Instalada)
	}
}

// EstadoActualizacion: para la página (al abrirse y cuando quiera).
func (n *Nucleo) EstadoActualizacion() EstadoPaquete {
	if n.paq == nil {
		return EstadoPaquete{}
	}
	n.paq.mu.Lock()
	e := n.paq.est
	n.paq.mu.Unlock()
	e.Reparte = n.l.Paquete.Version()
	return e
}

func (n *Nucleo) emitPaquete(forzar bool) {
	n.paq.mu.Lock()
	if !forzar && time.Since(n.paq.ultEmit) < time.Second {
		n.paq.mu.Unlock()
		return
	}
	n.paq.ultEmit = time.Now()
	n.paq.mu.Unlock()
	n.emit("paquete", n.EstadoActualizacion())
}

// conocida: la versión más alta que ya tenemos, está lista o se está bajando.
func (p *actPaquete) conocida() int64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	return max(p.est.Instalada, p.est.Version, p.enCurso)
}

func (n *Nucleo) puedeActualizarPaquete() bool {
	return n.paq != nil && n.paq.inst != nil && n.aj.leer().Actualizar
}

// verPaquete: un servidor de la lista; si reparte una versión más nueva, se le pide en segundo plano.
func (n *Nucleo) verPaquete(sv sala.ServidorVisto) {
	if !n.puedeActualizarPaquete() || sv.Estado != sala.EstadoVivo || sv.Propio {
		return
	}
	v := sv.Ficha.VersionPaquete
	if v <= n.paq.conocida() || n.paq.inst.Fallida(v) {
		return
	}
	n.paq.mu.Lock()
	t, malo := n.paq.fallos[sv.ID]
	n.paq.mu.Unlock()
	if malo && time.Since(t) < esperaTrasFallo {
		return
	}
	cli, err := n.l.ClientePaqueteP2P(sv.ID)
	if err != nil {
		return
	}
	go n.descargarPaquete(cli, sv.ID, v)
}

// paqueteAlEntrar: tras abrir el túnel. true = el anfitrión reparte paquete (y entonces no se mira la autoactualización
// vieja de solo el exe); false = anfitrión antiguo o este lanzador no es un paquete: lo de siempre.
func (n *Nucleo) paqueteAlEntrar(c *sala.Conexion) bool {
	if n.paq == nil || n.paq.inst == nil || c == nil || c.Invitado() == nil {
		return false
	}
	cli := sala.ClientePaqueteTunel(c.Invitado(), "túnel ("+c.Via+")")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	v, err := cli.Version(ctx)
	switch {
	case errors.Is(err, sala.ErrOcupado):
		return true
	case err != nil:
		n.logf("[paquete] el anfitrión no reparte paquete (%v): autoactualización de solo el exe", err)
		return false
	}
	n.logf("[paquete] el anfitrión reparte la versión %d; la nuestra %d", v, n.paq.conocida())
	if n.aj.leer().Actualizar && v > n.paq.conocida() && !n.paq.inst.Fallida(v) {
		go n.descargarPaquete(cli, "túnel", v)
	}
	return true
}

// descargarPaquete: manifiesto + lo que falte, a _update. Solo una a la vez.
func (n *Nucleo) descargarPaquete(cli sala.ClientePaquete, fuente string, anunciada int64) {
	p := n.paq
	p.mu.Lock()
	if p.ocupado {
		p.mu.Unlock()
		return
	}
	p.ocupado, p.enCurso = true, anunciada
	p.mu.Unlock()
	defer func() {
		p.mu.Lock()
		p.ocupado, p.enCurso = false, 0
		p.mu.Unlock()
		n.emitPaquete(true)
	}()
	fallo := func(err error) {
		n.logf("[paquete] %s: %v", cli.Quien, err)
		p.mu.Lock()
		p.fallos[fuente] = time.Now()
		if p.est.Fase == "descargando" {
			p.est.Fase, p.est.Version, p.est.Hecho, p.est.Total = "", 0, 0, 0
			if pe := p.inst.Pendiente(); pe != nil {
				p.est.Fase, p.est.Version, p.est.CambiaExe = "lista", pe.Version, pe.Exe
			}
		}
		p.mu.Unlock()
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Hour)
	defer cancel()
	cm, cancelM := context.WithTimeout(ctx, 3*time.Minute)
	pv, err := cli.Manifiesto(cm)
	cancelM()
	if err != nil {
		fallo(err)
		return
	}
	n.verRedFirmada(pv) // red.go: una lista de nodos firmada más nueva se guarda ya
	v := pv.Man.Version
	_, inst := p.inst.Instalada()
	if v <= inst {
		return
	}
	if pe := p.inst.Pendiente(); pe != nil && pe.Version >= v {
		return
	}
	n.logf("[paquete] %s ofrece la versión %d (firmada; lanzador %d): preparándola", cli.Quien, v, pv.Man.VersionLanzador)
	p.mu.Lock()
	p.est.Fase, p.est.Version, p.est.Hecho, p.est.Total = "descargando", v, 0, 0
	p.mu.Unlock()
	n.emitPaquete(true)
	pe, err := p.inst.Preparar(ctx, cli, pv, func(hecho, total int64) {
		p.mu.Lock()
		p.est.Hecho, p.est.Total = hecho, total
		p.mu.Unlock()
		n.emitPaquete(false)
	})
	if err != nil && !errors.Is(err, sala.ErrNoMasNueva) {
		fallo(err)
		return
	}
	if pe != nil {
		p.mu.Lock()
		p.est.Fase, p.est.Version, p.est.CambiaExe, p.est.Hecho, p.est.Total = "lista", pe.Version, pe.Exe, 0, 0
		p.mu.Unlock()
	}
}

// ResultadoReinicio: lo que contesta ReiniciarParaActualizar (Motivo: "" = se reinicia; "servidor" = para antes tu
// servidor; "nada" = no hay nada listo; "error" = no se pudo relanzar).
type ResultadoReinicio struct {
	OK     bool
	Motivo string
}

// ReiniciarParaActualizar: el botón «Restart to apply». Relanza este exe (que espera a que este proceso se cierre y
// aplica lo preparado al arrancar) y cierra la ventana.
func (n *Nucleo) ReiniciarParaActualizar() ResultadoReinicio {
	if n.paq == nil || n.paq.inst == nil || n.paq.inst.Pendiente() == nil {
		return ResultadoReinicio{Motivo: "nada"}
	}
	if n.l.EstadoServidor().EnMarcha {
		return ResultadoReinicio{Motivo: "servidor"}
	}
	cmd := exec.Command(n.cfg.Exe)
	cmd.Env = append(os.Environ(), EnvPadrePID+"="+strconv.Itoa(os.Getpid()))
	if err := cmd.Start(); err != nil {
		n.logf("[paquete] no puedo relanzar %s: %v", n.cfg.Exe, err)
		return ResultadoReinicio{Motivo: "error"}
	}
	n.logf("[paquete] reiniciando para aplicar la versión %d (pid nuevo %d)", n.paq.inst.Pendiente().Version, cmd.Process.Pid)
	n.mu.Lock()
	n.cerrando = true
	n.mu.Unlock()
	go func() {
		time.Sleep(300 * time.Millisecond) // que la página reciba la respuesta
		if n.cfg.Salir != nil {
			n.cfg.Salir()
		}
	}()
	return ResultadoReinicio{OK: true}
}

// AplicarAlArrancar: lo llama main.go al arrancar, antes de abrir la ventana. Deshace una aplicación cortada, aplica
// lo preparado (si el servidor propio no está escuchando) y limpia. relanzar = el exe ya es el nuevo: hay que
// arrancarlo y salir.
func AplicarAlArrancar(exe string, offset int, logf func(string, ...any)) (relanzar bool) {
	inst := sala.NuevaInstalacion(exe, logf)
	if inst == nil {
		return false
	}
	gate := net.JoinHostPort("127.0.0.1", strconv.Itoa(5730+offset))
	r, err := inst.Aplicar(func() bool { return sala.Escucha(gate) })
	go inst.Limpiar() // lo viejo se borra sin retrasar el arranque (si este proceso sale antes, lo acaba el siguiente)
	if err != nil {
		logf("[paquete] no se aplica la actualización: %v", err)
		return false
	}
	if r == nil {
		return false
	}
	logf("[paquete] actualizado a la versión %d", r.Version)
	return r.ExeCambiado
}
