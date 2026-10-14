//go:build prueba

package main

// SOLO PRUEBAS de extremo a extremo con el exe compilado (app\build.ps1 -Salida <carpeta> -Prueba). Con YO2_PRUEBA_CTL=<puerto>
// y YO2_LANZADOR_DATOS (datos de prueba), escucha en 127.0.0.1:<puerto> y ejecuta en la página el JavaScript que le manden
// (POST /js, el cuerpo es una expresión; puede ser una promesa) y devuelve {"ok": valor} o {"err": texto}. Así se maneja la
// ventana real (botones, formularios, métodos de Go) sin ratón: WebView2 no abre su puerto de depuración porque Wails le
// pasa sus propios argumentos. Este fichero NO existe en el exe que se reparte (sin la etiqueta «prueba» no se compila).

import (
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

var (
	pruebaRes sync.Map // id -> chan string
	pruebaN   atomic.Int64
)

func init() { arranquePrueba = iniciarPruebaCtl }

// PruebaResultado: la página devuelve aquí lo que dio el JavaScript de la prueba.
func (a *App) PruebaResultado(id, res string) {
	if ch, ok := pruebaRes.Load(id); ok {
		select {
		case ch.(chan string) <- res:
		default:
		}
	}
}

func iniciarPruebaCtl(a *App) {
	p, err := strconv.Atoi(os.Getenv("YO2_PRUEBA_CTL"))
	if err != nil || p <= 0 || os.Getenv("YO2_LANZADOR_DATOS") == "" {
		return
	}
	ln, err := net.Listen("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(p)))
	if err != nil {
		a.logf("[prueba] no puedo escuchar en %d: %v", p, err)
		return
	}
	a.logf("[prueba] control de pruebas en 127.0.0.1:%d", p)
	mux := http.NewServeMux()
	mux.HandleFunc("/js", func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		id := strconv.FormatInt(pruebaN.Add(1), 10)
		ch := make(chan string, 1)
		pruebaRes.Store(id, ch)
		defer pruebaRes.Delete(id)
		ids, _ := json.Marshal(id)
		js := `(async () => { let r; try { r = { ok: await (async () => (` + string(b) + `))() }; } catch (e) { r = { err: String((e && e.message) || e) }; }` +
			` window.go.main.App.PruebaResultado(` + string(ids) + `, JSON.stringify(r === undefined ? null : r)); })()`
		runtime.WindowExecJS(a.ctx, js)
		select {
		case res := <-ch:
			w.Header().Set("Content-Type", "application/json")
			io.WriteString(w, res)
		case <-time.After(5 * time.Minute):
			http.Error(w, "sin respuesta de la página", http.StatusGatewayTimeout)
		}
	})
	go http.Serve(ln, mux)
}
