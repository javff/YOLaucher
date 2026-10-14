package sala

// Paquete del Discord (P5, 11-10; tools/yo2emu/paquete/empaquetar.py): YO2OnlineLauncher.exe con el servidor al lado, para
// que cualquiera pueda hospedar en un PC que solo tiene su cliente 2008, sin instalar Python ni Node:
//
//	YO2OnlineLauncher.exe
//	python\python.exe          Python 3.12 embeddable (python312._pth ya añade server\yo2emu y server)
//	node\node.exe              Node 22 (duelcore.mjs usa node:sqlite)
//	engine\duelcore.mjs        el motor de duelos en un solo fichero
//	vendor\                    cards.cdb, strings.conf, CardScripts\
//	server\yo2emu\run.py       el servidor (solo lo que carga; sin panel admin ni nada de Konami)
//
// Con el exe dentro del repo de desarrollo (tools\yo2emu\launcher\...) no hay server\ al lado: BuscarPaquete da nil y
// todo sigue como siempre (run.py del repo con el Python del sistema).
//
// En cada «Host a server» el lanzador ejecuta primer_arranque.py (extraer_juego.py + generar_tablas.py) con la carpeta
// del juego de Ajustes: saca del merge.dat del anfitrión los datos que lee el servidor (a <datos>\juego_datos) y rehace
// la tabla de cartas. Es idempotente y tarda ~1 s; si falla, el mensaje (en inglés) sale en la ventana.

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Paquete: rutas absolutas de las piezas del paquete.
type Paquete struct {
	Raiz     string // carpeta del lanzador
	RunPy    string // server\yo2emu\run.py
	Python   string // python\python.exe
	Node     string // node\node.exe
	Duelcore string // engine\duelcore.mjs
	Vendor   string // vendor
	Preparar string // server\yo2emu\paquete\primer_arranque.py
}

// BuscarPaquete: el paquete junto al exe, o nil si falta alguna pieza (p. ej. el exe del repo de desarrollo).
func BuscarPaquete(exeDir string) *Paquete {
	if exeDir == "" {
		return nil
	}
	r, err := filepath.Abs(exeDir)
	if err != nil {
		return nil
	}
	p := &Paquete{Raiz: r, RunPy: filepath.Join(r, "server", "yo2emu", "run.py"), Python: filepath.Join(r, "python", "python.exe"),
		Node: filepath.Join(r, "node", "node.exe"), Duelcore: filepath.Join(r, "engine", "duelcore.mjs"),
		Vendor: filepath.Join(r, "vendor"), Preparar: filepath.Join(r, "server", "yo2emu", "paquete", "primer_arranque.py")}
	for _, f := range []string{p.RunPy, p.Python, p.Node, p.Duelcore, p.Preparar, filepath.Join(p.Vendor, "cards.cdb")} {
		if st, err := os.Stat(f); err != nil || !st.Mode().IsRegular() {
			return nil
		}
	}
	return p
}

// RunPyPaquete: el run.py del paquete junto al exe, o "".
func RunPyPaquete(exeDir string) string {
	if p := BuscarPaquete(exeDir); p != nil {
		return p.RunPy
	}
	return ""
}

// DirJuegoDatos: dónde van los datos del juego extraídos (YO2_JUEGO_DATOS): <datos>\juego_datos.
func (p *Paquete) DirJuegoDatos(datos string) string { return filepath.Join(datos, "juego_datos") }

// DirServidor: estado y logs del servidor de ese puerto: <datos>\servidor\<5730+offset> (lo mismo que calcula
// yo2srv/rutas.py en modo paquete con %LOCALAPPDATA%\YO2Online; aquí sale de la carpeta de datos del lanzador, así
// YO2_LANZADOR_DATOS de las pruebas lo cambia todo junto).
func (p *Paquete) DirServidor(datos string, offset int) string {
	return filepath.Join(datos, "servidor", strconv.Itoa(5730+offset))
}

// ErrPreparar: primer_arranque.py falló. Texto = lo que imprimió (en inglés, para el jugador).
type ErrPreparar struct {
	Codigo int
	Texto  string
}

func (e *ErrPreparar) Error() string {
	return fmt.Sprintf("game data setup failed (%d): %s", e.Codigo, e.Texto)
}

// PrepararDatos ejecuta primer_arranque.py: saca de la carpeta del juego los datos que lee el servidor y rehace la
// tabla de cartas. Devuelve lo que imprimió. Un fallo con código (carpeta que no es Ver.080912, merge.dat dañado...) es
// *ErrPreparar con las líneas de ERROR del script.
func (p *Paquete) PrepararDatos(ctx context.Context, carpetaJuego, datos string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	c := exec.CommandContext(ctx, p.Python, p.Preparar, "--juego", carpetaJuego, "--salida", p.DirJuegoDatos(datos),
		"--vendor", p.Vendor)
	c.Dir = filepath.Dir(p.Preparar)
	c.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")
	var out bytes.Buffer
	c.Stdout, c.Stderr = &out, &out
	OcultarVentana(c)
	err := c.Run()
	texto := strings.TrimSpace(out.String())
	if err == nil {
		return texto, nil
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return texto, &ErrPreparar{Codigo: ee.ExitCode(), Texto: lineasError(texto)}
	}
	return texto, &ErrPreparar{Codigo: -1, Texto: err.Error()}
}

// lineasError: las líneas «ERROR: …» del script (o las últimas si no hay), para enseñarlas en la ventana.
func lineasError(t string) string {
	var r []string
	ls := strings.Split(strings.ReplaceAll(t, "\r", ""), "\n")
	for _, l := range ls {
		if strings.HasPrefix(l, "ERROR") {
			r = append(r, strings.TrimSpace(l))
		}
	}
	if len(r) == 0 {
		r = ls[max(0, len(ls)-3):]
	}
	s := strings.TrimSpace(strings.Join(r, " "))
	if len(s) > 600 {
		s = s[:600] + "…"
	}
	return s
}

// Entorno: las variables de run.py en modo paquete (yo2srv/rutas.py, paquete/RUTAS.md). Van DESPUÉS de las del
// formulario: si se repite una, manda la última.
func (p *Paquete) Entorno(datos string, offset int) []string {
	return p.entornoEn(datos, p.DirServidor(datos, offset))
}

// entornoEn: lo mismo con el estado y los logs en srv (la carpeta de un servidor de «My servers»).
func (p *Paquete) entornoEn(datos, srv string) []string {
	return []string{
		"YO2_PAQUETE=1",
		"YO2_NODE=" + p.Node,
		"YO2_DUELCORE=" + p.Duelcore,
		"YO2_VENDOR=" + p.Vendor,
		"YO2_JUEGO_DATOS=" + p.DirJuegoDatos(datos),
		"YO2_STATE=" + filepath.Join(srv, "state.json"),
		"YO2_LOGDIR=" + filepath.Join(srv, "logs"),
		"YO2_ADMIN_PANEL=0", // el panel admin es privado del owner: no va en el paquete
		"PYTHONIOENCODING=utf-8",
	}
}

// Ajustar: deja las opciones del servidor listas para arrancar el run.py del paquete con su Python. nil = nada. Con
// o.DirDatos (un servidor de «My servers») el estado y los logs van ahí; sin ella, a <datos>\servidor\<puerto> (el de
// siempre).
func (p *Paquete) Ajustar(o *OpcServidor, datos string) {
	if p == nil || o == nil {
		return
	}
	o.RunPy, o.Python = p.RunPy, p.Python
	srv := o.DirDatos
	if srv == "" {
		srv = p.DirServidor(datos, o.Offset)
	}
	o.Env = append(o.Env, p.entornoEn(datos, srv)...)
}

// pythonDe: el python.exe de las opciones (el del paquete) o el del sistema.
func pythonDe(o OpcServidor) string {
	if o.Python != "" {
		return o.Python
	}
	return PythonExe()
}
