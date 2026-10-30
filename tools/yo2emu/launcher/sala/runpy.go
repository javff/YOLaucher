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
	"runtime"
	"sort"
	"strconv"
	"strings"
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

// BuscarRunPy: la ruta dada, o run.py junto al exe y hasta ocho carpetas más arriba (el .app de macOS queda hondo).
// También vale tools/yo2emu/run.py si el exe está dentro del repositorio. "" = no está.
func BuscarRunPy(flagRuta, exeDir string) string {
	var cands []string
	if flagRuta != "" {
		cands = append(cands, flagRuta)
	}
	dir := exeDir
	for i := 0; i < 8 && dir != "" && dir != string(filepath.Separator); i++ {
		cands = append(cands, filepath.Join(dir, "run.py"), filepath.Join(dir, "tools", "yo2emu", "run.py"))
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	for _, c := range cands {
		if st, err := os.Stat(c); err == nil && !st.IsDir() {
			p, _ := filepath.Abs(c)
			return p
		}
	}
	return ""
}

func PythonExe() string {
	if p := buscarPython(); p != "" {
		return p
	}
	return "python"
}

// HayPython: hay un intérprete de verdad (python3, python o py). Una app de macOS abierta desde el Finder no hereda
// el PATH del shell, así que también se miran las rutas fijas de Homebrew y del framework.
func HayPython() bool { return buscarPython() != "" }

func aliasPythonTienda(p string) bool {
	s := strings.ToLower(strings.ReplaceAll(p, `\`, "/"))
	return strings.Contains(s, "/windowsapps/")
}

func buscarPython() string {
	var cands []string
	visto := map[string]bool{}
	add := func(p string) {
		if p == "" || visto[p] || aliasPythonTienda(p) {
			return
		}
		visto[p] = true
		cands = append(cands, p)
	}
	// Windows: el original usaba python y luego py. python3 suele ser el alias de Microsoft Store
	// (WindowsApps), que LookPath encuentra, arranca y se cierra sin abrir el 5730.
	nombres := []string{"python", "py", "python3"}
	if runtime.GOOS != "windows" {
		nombres = []string{"python3", "python", "py"}
	}
	for _, nombre := range nombres {
		if x, err := exec.LookPath(nombre); err == nil {
			add(x)
		}
	}
	if runtime.GOOS != "windows" {
		for _, p := range []string{
			"/opt/homebrew/bin/python3",
			"/usr/local/bin/python3",
			"/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
			"/usr/bin/python3",
		} {
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				add(p)
			}
		}
	}
	var primero, conVersion string
	for _, p := range cands {
		mayor, menor, ok := versionPython(p)
		if !ok {
			continue
		}
		if primero == "" {
			primero = p
		}
		if conVersion == "" {
			conVersion = p
		}
		// El servidor se desarrolla con 3.12. 3.9 de /usr/bin no vale si hay otro más nuevo.
		if mayor > 3 || (mayor == 3 && menor >= 12) {
			return p
		}
	}
	if conVersion != "" {
		return conVersion
	}
	return primero
}

// NodeExe: node para duelcore (≥ 18). Una app de macOS abierta desde el Finder no hereda el PATH
// del shell (nvm, Homebrew), así que también se miran esas rutas. "" = no está.
func NodeExe() string {
	if p := buscarNode(); p != "" {
		return p
	}
	return ""
}

// HayNode: hay un node ≥ 18 (duelcore / tsx). Misma salvedad de PATH que HayPython.
func HayNode() bool { return buscarNode() != "" }

func buscarNode() string {
	if x, err := exec.LookPath("node"); err == nil {
		if mayor, _, ok := versionNode(x); ok && mayor >= 18 {
			return x
		}
	}
	for _, p := range nodosNvm() {
		name := filepath.Base(filepath.Dir(filepath.Dir(p)))
		if mayor, _, _, ok := versionDeNombreNode(name); ok && mayor >= 18 {
			return p
		}
	}
	home, errHome := os.UserHomeDir()
	fijos := []string{"/opt/homebrew/bin/node", "/usr/local/bin/node"}
	if errHome == nil {
		fijos = append(fijos,
			filepath.Join(home, ".volta", "bin", "node"),
			filepath.Join(home, ".local", "share", "fnm", "aliases", "default", "bin", "node"),
			filepath.Join(home, "Library", "Application Support", "fnm", "aliases", "default", "bin", "node"),
		)
	}
	for _, p := range fijos {
		if st, err := os.Stat(p); err != nil || st.IsDir() {
			continue
		}
		if mayor, _, ok := versionNode(p); ok && mayor >= 18 {
			return p
		}
	}
	if x, err := exec.LookPath("node"); err == nil {
		return x
	}
	for _, p := range fijos {
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p
		}
	}
	if n := nodosNvm(); len(n) > 0 {
		return n[0]
	}
	return ""
}

func nodosNvm() []string {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil
	}
	root := filepath.Join(home, ".nvm", "versions", "node")
	ents, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	type nv struct {
		path        string
		maj, min, p int
	}
	var xs []nv
	for _, e := range ents {
		if !e.IsDir() {
			continue
		}
		maj, min, pat, ok := versionDeNombreNode(e.Name())
		if !ok {
			continue
		}
		p := filepath.Join(root, e.Name(), "bin", "node")
		if st, err := os.Stat(p); err != nil || st.IsDir() {
			continue
		}
		xs = append(xs, nv{p, maj, min, pat})
	}
	sort.Slice(xs, func(i, j int) bool {
		if xs[i].maj != xs[j].maj {
			return xs[i].maj > xs[j].maj
		}
		if xs[i].min != xs[j].min {
			return xs[i].min > xs[j].min
		}
		return xs[i].p > xs[j].p
	})
	out := make([]string, len(xs))
	for i, x := range xs {
		out[i] = x.path
	}
	return out
}

func versionDeNombreNode(name string) (mayor, menor, parche int, ok bool) {
	s := strings.TrimPrefix(name, "v")
	f := strings.Split(s, ".")
	if len(f) < 2 {
		return 0, 0, 0, false
	}
	var err error
	if mayor, err = strconv.Atoi(f[0]); err != nil {
		return 0, 0, 0, false
	}
	if menor, err = strconv.Atoi(f[1]); err != nil {
		return 0, 0, 0, false
	}
	if len(f) >= 3 {
		parche, _ = strconv.Atoi(f[2])
	}
	return mayor, menor, parche, true
}

func versionNode(exe string) (mayor, menor int, ok bool) {
	out, err := exec.Command(exe, "-v").Output()
	if err != nil {
		return 0, 0, false
	}
	s := strings.TrimPrefix(strings.TrimSpace(string(out)), "v")
	f := strings.Split(s, ".")
	if len(f) < 2 {
		return 0, 0, false
	}
	mayor, err1 := strconv.Atoi(f[0])
	menor, err2 := strconv.Atoi(f[1])
	if err1 != nil || err2 != nil {
		return 0, 0, false
	}
	return mayor, menor, true
}

// completarEntornoNode: YO2_NODE y el directorio de node al frente del PATH, para que run.py / tsx
// los encuentren cuando el lanzador es un .app de macOS (PATH del Finder sin nvm ni Homebrew).
// Si el entorno ya trae YO2_NODE (paquete), no se pisa.
func completarEntornoNode(env []string) []string {
	node := envValor(env, "YO2_NODE")
	if node == "" {
		node = NodeExe()
		if node != "" {
			env = append(env, "YO2_NODE="+node)
		}
	}
	if node == "" {
		return env
	}
	return withNodeOnPATH(env, node)
}

func envValor(env []string, clave string) string {
	for _, e := range env {
		k, v, ok := strings.Cut(e, "=")
		if ok && strings.EqualFold(k, clave) {
			return v
		}
	}
	return ""
}

func withNodeOnPATH(env []string, node string) []string {
	dir := filepath.Dir(node)
	key, val, idx := "PATH", "", -1
	for i, e := range env {
		k, v, ok := strings.Cut(e, "=")
		if ok && strings.EqualFold(k, "PATH") {
			key, val, idx = k, v, i
			break
		}
	}
	if idx < 0 {
		val = os.Getenv("PATH")
	}
	sep := string(os.PathListSeparator)
	for _, p := range strings.Split(val, sep) {
		if p != "" && filepath.Clean(p) == filepath.Clean(dir) {
			return env
		}
	}
	nuevo := dir + sep + val
	if idx >= 0 {
		env[idx] = key + "=" + nuevo
		return env
	}
	return append(env, key+"="+nuevo)
}

func versionPython(exe string) (mayor, menor int, ok bool) {
	out, err := exec.Command(exe, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])").Output()
	if err != nil {
		return 0, 0, false
	}
	f := strings.Fields(string(out))
	if len(f) != 2 {
		return 0, 0, false
	}
	mayor, err1 := strconv.Atoi(f[0])
	menor, err2 := strconv.Atoi(f[1])
	if err1 != nil || err2 != nil {
		return 0, 0, false
	}
	return mayor, menor, true
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
