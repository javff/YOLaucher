package sala

// run.py (el servidor del juego) visto desde el anfitrión: ¿escucha?, ¿dónde está?, ¿cuántos juegan?
// (Escucha/BuscarRunPy/PythonExe vivían en anfitrion/main.go; ahora los usan OpenServer y el lanzador nuevo.)

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"time"
)

// Escucha: ¿hay algo aceptando conexiones TCP en addr? (1 s como mucho)
func Escucha(addr string) bool {
	c, err := net.DialTimeout("tcp", addr, time.Second)
	if err != nil {
		return false
	}
	c.Close()
	return true
}

// BuscarRunPy: la ruta dada, o run.py junto al exe o una o dos carpetas más arriba ("" = no está).
func BuscarRunPy(flagRuta, exeDir string) string {
	for _, c := range []string{flagRuta, filepath.Join(exeDir, "run.py"), filepath.Join(exeDir, "..", "run.py"),
		filepath.Join(exeDir, "..", "..", "run.py")} {
		if c == "" {
			continue
		}
		if _, err := os.Stat(c); err == nil {
			p, _ := filepath.Abs(c)
			return p
		}
	}
	return ""
}

func PythonExe() string {
	for _, p := range []string{"python", "py"} {
		if x, err := exec.LookPath(p); err == nil {
			return x
		}
	}
	return "python"
}

// ---------------------------------------------------------------- estado del juego para la ficha

// DatosJuego: lo que run.py cuenta de sí mismo para la ficha pública (solo números y textos públicos).
type DatosJuego struct {
	Jugadores    int      `json:"jugadores"`               // conectados ahora (también el owner si juega en local)
	Duelos       int      `json:"duelos"`                  // duelos en curso; -1 = no se sabe
	Perfil       string   `json:"perfil,omitempty"`        // "vanilla" | "enhanced" (YO2_PERFIL con que arrancó)
	VersionJuego string   `json:"version_juego,omitempty"` // "080912"
	Salas        []string `json:"salas,omitempty"`         // p. ej. ["Mundial","ES","EN"] con salas por idioma
	Cuentas      string   `json:"cuentas,omitempty"`       // "libre" | "cerradas"
}

// FuenteEstado: de dónde saca el anfitrión los jugadores y duelos de ahora.
type FuenteEstado interface {
	Leer(ctx context.Context) (DatosJuego, error)
}

// EstadoPanel: GET http://127.0.0.1:<puerto>/publico, la ruta de solo lectura y SIN token que run.py da con lo poco que
// es público (jugadores, duelos, perfil...). Hay dos sitios que la sirven: la API de gestión de yo2srv
// (yo2srv/admin/gestion.py, 8791 + offset: va en todos los paquetes) y el panel privado del owner (8790 + offset). Se
// prueba primero la de gestión (Antes) y luego el panel (URL): así sirve también con un run.py anterior a la API de gestión.
type EstadoPanel struct {
	URL    string   // el panel (8790 + offset): el último recurso
	Antes  []string // lo que se prueba antes de URL (la API de gestión)
	Client *http.Client
}

func puertoEntorno(nombre string, defecto int) int {
	if v, err := strconv.Atoi(os.Getenv(nombre)); err == nil && v > 0 {
		return v
	}
	return defecto
}

// NuevoEstadoPanel: la instancia de run.py con ese YO2_PORT_OFFSET (YO2_GESTION_PORT y YO2_ADMIN_PANEL_PORT cambian los puertos).
func NuevoEstadoPanel(offset int) *EstadoPanel {
	return &EstadoPanel{
		URL:   "http://127.0.0.1:" + strconv.Itoa(puertoEntorno("YO2_ADMIN_PANEL_PORT", 8790+offset)) + "/publico",
		Antes: []string{"http://127.0.0.1:" + strconv.Itoa(puertoEntorno("YO2_GESTION_PORT", 8791+offset)) + "/publico"},
	}
}

func (e *EstadoPanel) Leer(ctx context.Context) (DatosJuego, error) {
	var d DatosJuego
	var err error
	for _, u := range append(append([]string{}, e.Antes...), e.URL) {
		if u == "" {
			continue
		}
		if d, err = e.leerDe(ctx, u); err == nil {
			return d, nil
		}
	}
	if err == nil {
		err = errors.New("sin dirección del estado de run.py")
	}
	return d, err
}

func (e *EstadoPanel) leerDe(ctx context.Context, url string) (DatosJuego, error) {
	var d DatosJuego
	ctx, c := context.WithTimeout(ctx, 2*time.Second)
	defer c()
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return d, err
	}
	cl := e.Client
	if cl == nil {
		cl = &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	}
	r, err := cl.Do(req)
	if err != nil {
		return d, err
	}
	defer r.Body.Close()
	if r.StatusCode != 200 {
		return d, fmt.Errorf("run.py contesta %d en %s (¿sin la ruta /publico todavía?)", r.StatusCode, url)
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 8<<10)).Decode(&d); err != nil {
		return d, fmt.Errorf("estado de run.py mal formado: %w", err)
	}
	if d.Jugadores < 0 || d.Jugadores > MaxJugadores || d.Duelos < -1 || d.Duelos > MaxJugadores {
		return d, errors.New("estado de run.py con números fuera de rango")
	}
	return d, nil
}

// EstadoFijo: para pruebas (o para quien no tenga panel): siempre lo mismo.
type EstadoFijo struct {
	D   DatosJuego
	Err error
}

func (e EstadoFijo) Leer(context.Context) (DatosJuego, error) { return e.D, e.Err }
