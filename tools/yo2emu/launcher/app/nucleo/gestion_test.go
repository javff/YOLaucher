package nucleo

// Pruebas del cliente de la API de gestión (gestion.go) contra un servidor HTTP falso: token (dónde se busca y que se relee
// tras un 401), cuerpos y cabeceras, errores con clave, que ni el token ni las contraseñas lleguen al log y qué pasa sin
// servidor o sin respuesta.

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"yo2online/internal/pruebas"
)

type apiFalsa struct {
	t          *testing.T
	srv        *httptest.Server
	mu         sync.Mutex
	token      string
	llamadas   []string          // «METODO ruta cuerpo»
	respuestas map[string]any    // ruta -> cuerpo 200
	fallos     map[string][2]any // ruta -> {status, clave}
}

func nuevaApiFalsa(t *testing.T, token string) *apiFalsa {
	a := &apiFalsa{t: t, token: token, respuestas: map[string]any{}, fallos: map[string][2]any{}}
	a.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		a.mu.Lock()
		defer a.mu.Unlock()
		b, _ := io.ReadAll(r.Body)
		a.llamadas = append(a.llamadas, r.Method+" "+r.URL.Path+" "+string(b))
		w.Header().Set("Content-Type", "application/json")
		if r.Header.Get("Authorization") != "Bearer "+a.token {
			w.WriteHeader(401)
			io.WriteString(w, `{"error":"sin_token","mensaje":"token incorrecto"}`)
			return
		}
		if f, ok := a.fallos[r.URL.Path]; ok {
			w.WriteHeader(f[0].(int))
			json.NewEncoder(w).Encode(map[string]any{"error": f[1], "mensaje": "mensaje en español"})
			return
		}
		if v, ok := a.respuestas[r.URL.Path]; ok {
			json.NewEncoder(w).Encode(v)
			return
		}
		io.WriteString(w, `{"ok":true}`)
	}))
	t.Cleanup(a.srv.Close)
	return a
}

func (a *apiFalsa) puerto() int {
	i := strings.LastIndex(a.srv.URL, ":")
	p, _ := strconv.Atoi(a.srv.URL[i+1:])
	return p
}

// cliente: con el token escrito donde lo deja run.py (local) y un log que recoge todo lo que se le pasa.
func (a *apiFalsa) cliente(t *testing.T, log *strings.Builder) (*Gestion, string) {
	local := pruebas.TempDir(t)
	os.WriteFile(filepath.Join(local, "gestion_"+strconv.Itoa(a.puerto())+".txt"), []byte(a.token+"\n"), 0o600)
	var mu sync.Mutex
	g := &Gestion{Base: a.srv.URL, Local: local, Datos: pruebas.TempDir(t), Logf: func(f string, x ...any) {
		mu.Lock()
		defer mu.Unlock()
		log.WriteString(fmt.Sprintf(f, x...) + "\n")
	}}
	return g, local
}

func claveDe(err error) string { return MsgDe(err).K }

func TestGestionTokenYCabeceras(t *testing.T) {
	a := nuevaApiFalsa(t, "tok-secreto-1234567890")
	a.respuestas["/gestion/jugadores"] = map[string]any{"jugadores": []map[string]any{
		{"corto": "abc123", "nombre": "Zeta", "dueno": false, "estado": "mapa", "mundo": "Domino", "zona": "Pier", "desde_s": 42, "vetado": false},
		{"corto": "def456", "nombre": "Eta", "dueno": true, "estado": "menu", "mundo": nil, "zona": nil, "desde_s": nil, "vetado": true}}}
	var log strings.Builder
	g, _ := a.cliente(t, &log)
	js, err := g.Jugadores()
	if err != nil || len(js) != 2 {
		t.Fatalf("%v %v", js, err)
	}
	if js[0].Nombre != "Zeta" || js[0].DesdeS != 42 || js[0].Zona != "Pier" || js[0].Estado != "mapa" {
		t.Fatalf("%+v", js[0])
	}
	if js[1].DesdeS != -1 || js[1].Mundo != "" || !js[1].Dueno || !js[1].Vetado {
		t.Fatalf("lo que no se sabe (null) debe quedar como -1/vacío: %+v", js[1])
	}
	// sin token en ninguna carpeta: error con clave, sin tocar la red
	g2 := &Gestion{Base: a.srv.URL, Local: pruebas.TempDir(t), Datos: pruebas.TempDir(t)}
	if _, err := g2.Jugadores(); claveDe(err) != "msg.gestion.sin_token" {
		t.Fatalf("sin token: %v", err)
	}
	// el token también vale en la carpeta de datos del lanzador (YO2_LANZADOR_DATOS)
	datos := pruebas.TempDir(t)
	os.WriteFile(filepath.Join(datos, "gestion_"+strconv.Itoa(a.puerto())+".txt"), []byte(a.token), 0o600)
	g3 := &Gestion{Base: a.srv.URL, Local: pruebas.TempDir(t), Datos: datos}
	if _, err := g3.Jugadores(); err != nil {
		t.Fatalf("token en Datos: %v", err)
	}
	// un token con espacios en medio no es un token
	os.WriteFile(filepath.Join(datos, "gestion_"+strconv.Itoa(a.puerto())+".txt"), []byte("dos palabras"), 0o600)
	g4 := &Gestion{Base: a.srv.URL, Local: pruebas.TempDir(t), Datos: datos}
	if _, err := g4.Jugadores(); claveDe(err) != "msg.gestion.sin_token" {
		t.Fatalf("token raro aceptado: %v", err)
	}
}

func TestGestionRelelElTokenTrasUn401(t *testing.T) {
	a := nuevaApiFalsa(t, "viejo-viejo-viejo")
	var log strings.Builder
	g, local := a.cliente(t, &log)
	if _, err := g.Jugadores(); err != nil {
		t.Fatal(err)
	}
	a.mu.Lock()
	a.token = "nuevo-nuevo-nuevo" // run.py se reinició
	a.mu.Unlock()
	os.WriteFile(filepath.Join(local, "gestion_"+strconv.Itoa(a.puerto())+".txt"), []byte("nuevo-nuevo-nuevo"), 0o600)
	if _, err := g.Jugadores(); err != nil {
		t.Fatalf("debería releer el token una vez: %v", err)
	}
	// y si el token del fichero también es malo: error con clave, sin bucle
	a.mu.Lock()
	a.token = "otro-distinto-otro"
	n := len(a.llamadas)
	a.mu.Unlock()
	if _, err := g.Jugadores(); claveDe(err) != "msg.gestion.token_malo" {
		t.Fatalf("token malo: %v", err)
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if len(a.llamadas)-n != 2 {
		t.Fatalf("un 401 se reintenta UNA vez, no más: %d llamadas", len(a.llamadas)-n)
	}
}

func TestGestionAccionesYCuerpos(t *testing.T) {
	a := nuevaApiFalsa(t, "tok-secreto-1234567890")
	a.respuestas["/gestion/aviso"] = map[string]any{"ok": true, "enviado_a": 3}
	a.respuestas["/gestion/copia"] = map[string]any{"ok": true, "fichero": "state.json.bak-admin-20261011-120000"}
	a.respuestas["/gestion/copias"] = map[string]any{"copias": []map[string]any{{"fichero": "x", "kb": 12, "fecha": "2026-10-11 12:00:00"}}}
	a.respuestas["/gestion/vetados"] = map[string]any{"vetados": []map[string]any{{"corto": "abc123", "nombres": []string{"Zeta"}, "motivo": "spam", "fecha": "2026-10-11 12:00"}}}
	a.respuestas["/gestion/cuentas"] = map[string]any{"estricto": true, "max": 16, "cuentas": []map[string]any{
		{"corto": "aaaaaa", "usuario": nil, "dueno": true, "personajes": []string{"Dueno"}, "en_linea": true, "vetado": false}}}
	var log strings.Builder
	g, _ := a.cliente(t, &log)
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(g.Expulsar("abc123"))
	must(g.Vetar("abc123", "spam"))
	must(g.QuitarVeto("abc123"))
	must(g.CrearCuenta("ana", "pa$$secreta"))
	must(g.CambiarClave("abc123", "otra-secreta", ""))
	must(g.CambiarClave("abc123", "otra-secreta", "ana"))
	must(g.BorrarCuenta("abc123"))
	if n, err := g.Aviso("hola"); err != nil || n != 3 {
		t.Fatalf("%d %v", n, err)
	}
	if f, err := g.Copia(); err != nil || !strings.HasPrefix(f, "state.json.bak-admin-") {
		t.Fatalf("%q %v", f, err)
	}
	if c, err := g.Copias(); err != nil || len(c) != 1 || c[0].KB != 12 {
		t.Fatalf("%v %v", c, err)
	}
	if v, err := g.Vetados(); err != nil || len(v) != 1 || v[0].Nombres[0] != "Zeta" || v[0].Motivo != "spam" {
		t.Fatalf("%v %v", v, err)
	}
	if c, err := g.Cuentas(); err != nil || !c.Estricto || c.Max != 16 || len(c.Cuentas) != 1 || c.Cuentas[0].Usuario != "" || !c.Cuentas[0].Dueno {
		t.Fatalf("%+v %v", c, err)
	}
	a.mu.Lock()
	ll := strings.Join(a.llamadas, "\n")
	a.mu.Unlock()
	for _, quiero := range []string{
		`POST /gestion/expulsar {"acct":"abc123"}`,
		`POST /gestion/vetar {"acct":"abc123","motivo":"spam"}`,
		`POST /gestion/quitar_veto {"acct":"abc123"}`,
		`POST /gestion/crear {"clave":"pa$$secreta","usuario":"ana"}`,
		`POST /gestion/cambiar_clave {"acct":"abc123","clave":"otra-secreta"}`,
		`POST /gestion/cambiar_clave {"acct":"abc123","clave":"otra-secreta","usuario":"ana"}`,
		`POST /gestion/borrar {"acct":"abc123","confirmar":true}`,
		`POST /gestion/aviso {"texto":"hola"}`,
		`POST /gestion/copia {}`,
	} {
		if !strings.Contains(ll, quiero) {
			t.Errorf("falta la llamada %s\n%s", quiero, ll)
		}
	}
	// ni el token ni las contraseñas en el log
	for _, secreto := range []string{"tok-secreto-1234567890", "pa$$secreta", "otra-secreta"} {
		if strings.Contains(log.String(), secreto) {
			t.Errorf("el log lleva un secreto (%s): %s", secreto, log.String())
		}
	}
	if !strings.Contains(log.String(), "/gestion/crear") {
		t.Errorf("las acciones deberían dejar una línea en el log: %q", log.String())
	}
}

// 12-10: puntos DUELPASS y MP (POST /gestion/dp): null = no tocar; los campos dp/mp de jugadores y cuentas, -1 si no vienen.
func TestGestionPuntos(t *testing.T) {
	a := nuevaApiFalsa(t, "tok-secreto-1234567890")
	a.respuestas["/gestion/dp"] = map[string]any{"ok": true, "antes": map[string]any{"dp": 10}, "ahora": map[string]any{"dp": 500, "mp": 300},
		"al_momento": true, "en_linea": true}
	a.respuestas["/gestion/jugadores"] = map[string]any{"jugadores": []map[string]any{
		{"corto": "abc123", "nombre": "Zeta", "estado": "mapa", "hueco": 0, "nivel": 2, "dp": 10, "mp": 300},
		{"corto": "aaa111", "nombre": "Viejo", "estado": "sala"}}}
	a.respuestas["/gestion/cuentas"] = map[string]any{"estricto": false, "max": 16, "cuentas": []map[string]any{
		{"corto": "abc123", "personajes": []string{"Uno"}, "fichas": []map[string]any{{"hueco": 0, "nombre": "Uno", "nivel": 3, "dp": 7, "mp": nil}}}}}
	var log strings.Builder
	g, _ := a.cliente(t, &log)
	dp, mp := 500, -25
	r, err := g.Puntos("abc123", 0, &dp, nil, "poner")
	if err != nil || r.Antes["dp"] != 10 || r.Ahora["dp"] != 500 || r.Ahora["mp"] != 300 || !r.AlMomento {
		t.Fatalf("%+v %v", r, err)
	}
	if _, err := g.Puntos("abc123", -1, nil, &mp, "sumar"); err != nil {
		t.Fatal(err)
	}
	a.mu.Lock()
	ll := strings.Join(a.llamadas, "\n")
	a.mu.Unlock()
	for _, quiero := range []string{`POST /gestion/dp {"acct":"abc123","dp":500,"hueco":0,"modo":"poner"}`,
		`POST /gestion/dp {"acct":"abc123","modo":"sumar","mp":-25}`} {
		if !strings.Contains(ll, quiero) {
			t.Errorf("falta la llamada %s\n%s", quiero, ll)
		}
	}
	js, err := g.Jugadores()
	if err != nil || js[0].DP != 10 || js[0].MP != 300 || js[1].DP != -1 || js[1].MP != -1 {
		t.Fatalf("jugadores: %+v %v", js, err)
	}
	cs, err := g.Cuentas()
	if err != nil || cs.Cuentas[0].Fichas[0].DP != 7 || cs.Cuentas[0].Fichas[0].MP != -1 {
		t.Fatalf("cuentas: %+v %v", cs, err)
	}
	a.mu.Lock()
	a.fallos["/gestion/dp"] = [2]any{400, "puntos"}
	a.mu.Unlock()
	if _, err := g.Puntos("abc123", 0, &dp, nil, "poner"); claveDe(err) != "msg.gestion.puntos" {
		t.Errorf("puntos: %v", err)
	}
}

// 12-10: nivel de un personaje (POST /gestion/nivel) y los campos nuevos de jugadores y cuentas; con un run.py anterior
// (sin esos campos o con null) salen como -1 / sin fichas.
func TestGestionNivel(t *testing.T) {
	a := nuevaApiFalsa(t, "tok-secreto-1234567890")
	a.respuestas["/gestion/nivel"] = map[string]any{"ok": true, "antes": 1, "ahora": 5, "al_momento": true, "en_linea": true}
	a.respuestas["/gestion/jugadores"] = map[string]any{"jugadores": []map[string]any{
		{"corto": "abc123", "nombre": "Zeta", "estado": "mapa", "desde_s": 10, "hueco": 2, "nivel": 7},
		{"corto": "def456", "nombre": "", "estado": "menu", "desde_s": nil, "hueco": nil, "nivel": nil},
		{"corto": "aaa111", "nombre": "Viejo", "estado": "sala"}}}
	a.respuestas["/gestion/cuentas"] = map[string]any{"estricto": false, "max": 16, "cuentas": []map[string]any{
		{"corto": "abc123", "personajes": []string{"Uno", "Dos"}, "fichas": []map[string]any{
			{"hueco": 0, "nombre": "Uno", "nivel": 3}, {"hueco": 2, "nombre": "Dos", "nivel": nil}}},
		{"corto": "def456", "personajes": []string{"Viejo"}}}}
	var log strings.Builder
	g, _ := a.cliente(t, &log)
	r, err := g.Nivel("abc123", 2, 5)
	if err != nil || r.Antes != 1 || r.Ahora != 5 || !r.AlMomento || !r.EnLinea {
		t.Fatalf("%+v %v", r, err)
	}
	if _, err := g.Nivel("abc123", -1, 9); err != nil {
		t.Fatal(err)
	}
	a.mu.Lock()
	ll := strings.Join(a.llamadas, "\n")
	a.mu.Unlock()
	for _, quiero := range []string{`POST /gestion/nivel {"acct":"abc123","hueco":2,"nivel":5}`, `POST /gestion/nivel {"acct":"abc123","nivel":9}`} {
		if !strings.Contains(ll, quiero) {
			t.Errorf("falta la llamada %s\n%s", quiero, ll)
		}
	}
	js, err := g.Jugadores()
	if err != nil || len(js) != 3 {
		t.Fatalf("%v %v", js, err)
	}
	if js[0].Hueco != 2 || js[0].Nivel != 7 || js[1].Hueco != -1 || js[1].Nivel != -1 || js[1].DesdeS != -1 || js[2].Nivel != -1 || js[2].Hueco != -1 {
		t.Errorf("jugadores: %+v", js)
	}
	c, err := g.Cuentas()
	if err != nil || len(c.Cuentas) != 2 {
		t.Fatalf("%+v %v", c, err)
	}
	f := c.Cuentas[0].Fichas
	if len(f) != 2 || f[0] != (FichaG{Hueco: 0, Nombre: "Uno", Nivel: 3, DP: -1, MP: -1}) || f[1] != (FichaG{Hueco: 2, Nombre: "Dos", Nivel: -1, DP: -1, MP: -1}) {
		t.Errorf("fichas: %+v", f)
	}
	if c.Cuentas[1].Fichas == nil || len(c.Cuentas[1].Fichas) != 0 || c.Cuentas[1].Personajes[0] != "Viejo" {
		t.Errorf("cuenta de un run.py anterior: %+v", c.Cuentas[1])
	}
	// errores con clave propia
	a.mu.Lock()
	a.fallos["/gestion/nivel"] = [2]any{400, "nivel"}
	a.mu.Unlock()
	if _, err := g.Nivel("abc123", 0, 100); claveDe(err) != "msg.gestion.nivel" {
		t.Errorf("fuera de rango: %v", err)
	}
}

func TestGestionErroresConClave(t *testing.T) {
	a := nuevaApiFalsa(t, "tok-secreto-1234567890")
	var log strings.Builder
	g, _ := a.cliente(t, &log)
	casos := map[string]string{
		"no_conectado": "msg.gestion.no_conectado", "dueno": "msg.gestion.dueno", "no_existe": "msg.gestion.no_existe",
		"cuenta_invalida": "msg.gestion.cuenta_invalida", "ambigua": "msg.gestion.cuenta_invalida", "no_vetada": "msg.gestion.no_vetada",
		"aviso_vacio": "msg.gestion.aviso_vacio", "entrada": "msg.gestion.entrada", "ya_existe": "msg.gestion.ya_existe",
		"usuario_repetido": "msg.gestion.ya_existe", "tope": "msg.gestion.tope", "usuario_otro": "msg.gestion.usuario_otro",
		"sin_confirmar": "msg.gestion.sin_confirmar", "conectada": "msg.gestion.conectada", "con_personajes": "msg.gestion.con_personajes",
		"sin_cifrado": "msg.gestion.sin_cifrado", "bloqueado": "msg.gestion.bloqueado", "inventada": "msg.gestion.error",
		"nivel": "msg.gestion.nivel", "hueco": "msg.gestion.hueco", "sin_personaje": "msg.gestion.sin_personaje",
		"puntos": "msg.gestion.puntos", "modo": "msg.gestion.modo",
	}
	for clave, quiero := range casos {
		a.mu.Lock()
		a.fallos["/gestion/expulsar"] = [2]any{400, clave}
		a.mu.Unlock()
		err := g.Expulsar("abc123")
		if got := claveDe(err); got != quiero {
			t.Errorf("%s -> %s, quería %s", clave, got, quiero)
		}
		if e, ok := err.(*ErrUI); !ok || e.Es == "" || strings.Contains(e.Es, "mensaje en español") {
			t.Errorf("%s: el texto en español tiene que ser el nuestro, no el del servidor: %v", clave, err)
		}
	}
}

func TestGestionSinServidorYSinRespuesta(t *testing.T) {
	// la API no está (puerto cerrado): no_responde, con el token ya leído
	a := nuevaApiFalsa(t, "tok-secreto-1234567890")
	var log strings.Builder
	g, _ := a.cliente(t, &log)
	a.srv.Close()
	if _, err := g.Jugadores(); claveDe(err) != "msg.gestion.no_responde" {
		t.Fatalf("sin respuesta: %v", err)
	}
	// el núcleo, con el servidor propio parado: ni lo intenta
	n, _ := nuevoDePrueba(t)
	n.cfg.Gestion = g
	if _, err := n.GestionJugadores(); claveDe(err) != "msg.gestion.sin_servidor" {
		t.Fatalf("servidor parado: %v", err)
	}
	if err := n.GestionExpulsar("abc123"); claveDe(err) != "msg.gestion.sin_servidor" {
		t.Fatalf("servidor parado: %v", err)
	}
}

func TestPuertoGestion(t *testing.T) {
	t.Setenv("YO2_GESTION_PORT", "")
	if PuertoGestion(0) != 8791 || PuertoGestion(7000) != 15791 {
		t.Fatalf("%d %d", PuertoGestion(0), PuertoGestion(7000))
	}
	t.Setenv("YO2_GESTION_PORT", "9999")
	if PuertoGestion(7000) != 9999 {
		t.Fatal("YO2_GESTION_PORT no manda")
	}
}
