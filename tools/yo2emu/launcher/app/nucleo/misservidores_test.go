package nucleo

// «My servers» (misservidores.go): crear, editar sin poder cambiar las reglas, borrar (con el nombre, nunca el que está en
// marcha, a la papelera), migración del lanzador.json de antes (mismo servidor, misma identidad, mismos datos), identidades
// distintas = códigos distintos, carpetas separadas y uno a la vez.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"yo2online/internal/pruebas"
	"yo2online/sala"
)

// nucleoServidores: un núcleo sin red con la papelera de mentira (apunta lo que se le manda y lo aparta a otra carpeta).
func nucleoServidores(t *testing.T, datos string) (*Nucleo, *[]string) {
	t.Helper()
	var mu sync.Mutex
	var papelera []string
	n := Nuevo(Config{Datos: datos, ExeDir: datos, Version: 20261011120000, Papelera: func(d string) error {
		mu.Lock()
		papelera = append(papelera, d)
		mu.Unlock()
		return os.Rename(d, d+".papelera")
	}})
	t.Cleanup(n.Cerrar)
	return n, &papelera
}

func formPrueba(nombre, reglas string) FormServidor {
	return FormServidor{Nombre: nombre, Descripcion: "desc " + nombre, Max: 16, Modo: ModoOculto, Reglas: reglas, Cuentas: sala.CuentasLibre}
}

func codigoDeCarpeta(t *testing.T, dir string) (string, string) {
	t.Helper()
	s, cambiado, err := sala.CargarAnfitrion(dir, false, false)
	if err != nil || cambiado {
		t.Fatalf("identidad de %s: %v (cambiada: %v)", dir, err, cambiado)
	}
	return s.Codigo(), s.PeerID().String()
}

func TestMisServidoresCrearEditarBorrar(t *testing.T) {
	datos := pruebas.TempDir(t)
	n, papelera := nucleoServidores(t, datos)
	if len(n.MisServidores()) != 0 {
		t.Fatal("un PC sin servidor de antes no debería tener ninguno")
	}
	a, err := n.CrearMiServidor(formPrueba("Arena", sala.ReglasVanilla))
	if err != nil {
		t.Fatal(err)
	}
	b, err := n.CrearMiServidor(formPrueba("Torneo", sala.ReglasMejorado))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := n.CrearMiServidor(formPrueba(" arena ", sala.ReglasVanilla)); codigoDe(err) != "msg.srv.nombre_repetido" {
		t.Fatalf("nombre repetido: %v", err)
	}
	if _, err := n.CrearMiServidor(FormServidor{Nombre: "Con clave", Max: 8, Modo: ModoClave, Reglas: sala.ReglasVanilla}); codigoDe(err) != "msg.form.clave_vacia" {
		t.Fatalf("uno nuevo con contraseña sin escribirla: %v", err)
	}
	// carpetas separadas y una identidad NUEVA cada uno: códigos y peer IDs distintos
	if a.ID == b.ID || !reIDServidor.MatchString(a.ID) || a.Legado || b.Legado {
		t.Fatalf("ids: %+v %+v", a, b)
	}
	ca, pa := codigoDeCarpeta(t, filepath.Join(datos, "servidores", a.ID, "anfitrion"))
	cb, pb := codigoDeCarpeta(t, filepath.Join(datos, "servidores", b.ID, "anfitrion"))
	if ca == cb || pa == pb || a.Huella == b.Huella || a.Huella == "" {
		t.Fatal("dos servidores con la misma identidad")
	}
	if _, err := os.Stat(filepath.Join(datos, "anfitrion")); err == nil {
		t.Fatal("un servidor nuevo tocó la identidad de siempre (anfitrion\\)")
	}
	ga, _ := n.guardado(a.ID)
	if d := n.dirDatos(ga); d != filepath.Join(datos, "servidores", a.ID, "servidor") {
		t.Fatalf("carpeta de datos: %s", d)
	}

	// editar: todo menos las reglas
	f := formPrueba("Arena 2", sala.ReglasVanilla)
	f.Max, f.NivelInicial, f.Cuentas = 50, 5, sala.CuentasCerrada
	if err := n.EditarMiServidor(a.ID, f); err != nil {
		t.Fatal(err)
	}
	f.Reglas = sala.ReglasMejorado
	if err := n.EditarMiServidor(a.ID, f); codigoDe(err) != "msg.srv.reglas_fijas" {
		t.Fatalf("cambiar las reglas: %v", err)
	}
	if err := n.EditarMiServidor(a.ID, formPrueba("torneo", sala.ReglasVanilla)); codigoDe(err) != "msg.srv.nombre_repetido" {
		t.Fatalf("editar con el nombre de otro: %v", err)
	}
	// contraseña: al ponerla queda la clave derivada ligada a SU identidad; vacía después = la guardada
	f = formPrueba("Arena 2", sala.ReglasVanilla)
	f.Max, f.NivelInicial, f.Cuentas, f.Modo, f.Contrasena = 50, 5, sala.CuentasCerrada, ModoClave, "clave larga"
	if err := n.EditarMiServidor(a.ID, f); err != nil {
		t.Fatal(err)
	}
	f.Contrasena = ""
	if err := n.EditarMiServidor(a.ID, f); err != nil {
		t.Fatalf("vacía = la de antes: %v", err)
	}
	var ma MiServidor
	for _, m := range n.MisServidores() {
		if m.ID == a.ID {
			ma = m
		}
	}
	if ma.Form.Nombre != "Arena 2" || ma.Form.Max != 50 || ma.Form.NivelInicial != 5 || ma.Form.Reglas != sala.ReglasVanilla || !ma.Form.TieneClave ||
		ma.Form.Contrasena != "" || ma.UsadoHaceS != -1 || !ma.BorraDatos {
		t.Fatalf("tras editar: %+v", ma)
	}
	raw, _ := os.ReadFile(filepath.Join(datos, ficheroAjustes))
	if strings.Contains(string(raw), "clave larga") || !strings.Contains(string(raw), `"v": 2`) {
		t.Fatalf("lanzador.json: %s", raw)
	}
	if ga, _ := n.guardado(a.ID); ga.Clave == nil || ga.Clave.Peer != pa {
		t.Fatalf("la clave no está ligada a su identidad: %+v", ga.Clave)
	}

	// borrar: con el nombre exacto; su carpeta a la papelera; el otro intacto
	if err := n.BorrarMiServidor(b.ID, "torneo"); codigoDe(err) != "msg.srv.borrar_nombre" {
		t.Fatalf("borrar con otro nombre: %v", err)
	}
	if err := n.BorrarMiServidor(b.ID, " Torneo "); err != nil {
		t.Fatal(err)
	}
	if len(*papelera) != 1 || (*papelera)[0] != filepath.Join(datos, "servidores", b.ID) {
		t.Fatalf("papelera: %v", *papelera)
	}
	if _, err := os.Stat(filepath.Join(datos, "servidores", a.ID, "anfitrion", "identidad.key")); err != nil {
		t.Fatal("borrar uno tocó la carpeta de otro")
	}
	if ms := n.MisServidores(); len(ms) != 1 || ms[0].ID != a.ID {
		t.Fatalf("tras borrar: %+v", ms)
	}
	if err := n.BorrarMiServidor(b.ID, "Torneo"); codigoDe(err) != "msg.srv.no_existe" {
		t.Fatalf("borrar dos veces: %v", err)
	}
	// otro arranque del lanzador: lo mismo
	n2, _ := nucleoServidores(t, datos)
	if ms := n2.MisServidores(); len(ms) != 1 || ms[0].ID != a.ID || ms[0].Huella != ma.Huella || !ms[0].Form.TieneClave {
		t.Fatalf("tras reabrir: %+v", ms)
	}
}

// Sin papelera (o si Windows no la deja usar), la carpeta se aparta a servidores\_borrados: nunca se borra del todo.
func TestBorrarSinPapeleraAparta(t *testing.T) {
	datos := pruebas.TempDir(t)
	n := Nuevo(Config{Datos: datos, ExeDir: datos, Papelera: func(string) error { return os.ErrPermission }})
	t.Cleanup(n.Cerrar)
	m, err := n.CrearMiServidor(formPrueba("Uno", sala.ReglasVanilla))
	if err != nil {
		t.Fatal(err)
	}
	if err := n.BorrarMiServidor(m.ID, "Uno"); err != nil {
		t.Fatal(err)
	}
	ap, _ := filepath.Glob(filepath.Join(datos, "servidores", "_borrados", m.ID+"-*", "anfitrion", "identidad.key"))
	if len(ap) != 1 {
		t.Fatal("no se apartó la carpeta")
	}
	if len(n.MisServidores()) != 0 {
		t.Fatal("sigue en la lista")
	}
}

// El lanzador.json de antes (formato 1: un formulario y su contraseña) pasa a «My servers» sin perder nada: el servidor de
// antes es el primero, con su formulario, su contraseña, SU identidad de siempre (mismo código) y sin carpeta propia
// (sus datos siguen donde estaban). La copia del fichero viejo queda en lanzador.json.v1.bak.
func TestMigracionFormato1(t *testing.T) {
	datos := pruebas.TempDir(t)
	dirAnf := filepath.Join(datos, "anfitrion")
	os.MkdirAll(dirAnf, 0o700)
	s, _, err := sala.CargarAnfitrion(dirAnf, false, false)
	if err != nil {
		t.Fatal(err)
	}
	codigo, peerID := s.Codigo(), s.PeerID().String()
	cs, err := sala.NuevaClaveServidor("contrasena vieja", s.Pub())
	if err != nil {
		t.Fatal(err)
	}
	g, err := guardarClave(cs.Ver, cs.Sal, cs.Clave, peerID)
	if err != nil {
		t.Skipf("sin DPAPI: %v", err)
	}
	viejo := map[string]any{"v": 1, "actualizar": false, "tema": "dark", "carpeta_juego": `C:\YO2`,
		"servidor":       FormServidor{Nombre: "ServerTest", Descripcion: "el de siempre", Max: 30, Publica: true, Modo: ModoClave, Reglas: sala.ReglasMejorado, Cuentas: sala.CuentasCerrada, NivelInicial: 2, PuntosDP: 100, DuelosGratis: true},
		"clave_servidor": g, "red": AjustesRed{PuertoFijo: true, Puerto: 5956}}
	b, _ := json.Marshal(viejo)
	os.WriteFile(filepath.Join(datos, ficheroAjustes), b, 0o600)

	n, papelera := nucleoServidores(t, datos)
	a := n.Ajustes()
	if a.Actualizar || a.Tema != "dark" || a.CarpetaJuego != `C:\YO2` || !a.Red.PuertoFijo || a.Red.Puerto != 5956 {
		t.Fatalf("se perdieron los ajustes: %+v", a)
	}
	ms := n.MisServidores()
	if len(ms) != 1 {
		t.Fatalf("servidores: %+v", ms)
	}
	m := ms[0]
	if !m.Legado || m.Form.Nombre != "ServerTest" || m.Form.Reglas != sala.ReglasMejorado || m.Form.Modo != ModoClave || !m.Form.TieneClave ||
		m.Form.NivelInicial != 2 || m.Form.PuntosDP != 100 || !m.Form.DuelosGratis || m.Form.Cuentas != sala.CuentasCerrada || m.Huella != s.Huella() {
		t.Fatalf("migrado: %+v", m)
	}
	sg, _ := n.guardado(m.ID)
	if n.dirAnfitrion(sg) != dirAnf || n.dirDatos(sg) != "" || n.dirPropio(sg) != "" {
		t.Fatal("el servidor de antes no usa sus carpetas de siempre")
	}
	if c, p := codigoDeCarpeta(t, dirAnf); c != codigo || p != peerID {
		t.Fatal("la migración cambió la identidad (otro código de sala)")
	}
	if sg.Clave.claveServidor(peerID) == nil {
		t.Fatal("la contraseña guardada no sobrevive a la migración")
	}
	if bak, err := os.ReadFile(filepath.Join(datos, ficheroAjustes+".v1.bak")); err != nil || string(bak) != string(b) {
		t.Fatalf("copia del formato 1: %v", err)
	}
	raw, _ := os.ReadFile(filepath.Join(datos, ficheroAjustes))
	if !strings.Contains(string(raw), `"v": 2`) || strings.Contains(string(raw), `"servidor":`) {
		t.Fatalf("lanzador.json migrado: %s", raw)
	}
	// en desarrollo, borrar el de antes NO toca datos (son los del repo) ni su identidad: solo sale de la lista
	if m.BorraDatos {
		t.Fatal("el de antes en desarrollo no debería mandar nada a la papelera")
	}
	if err := n.BorrarMiServidor(m.ID, "ServerTest"); err != nil {
		t.Fatal(err)
	}
	if len(*papelera) != 0 {
		t.Fatalf("papelera: %v", *papelera)
	}
	if c, _ := codigoDeCarpeta(t, dirAnf); c != codigo {
		t.Fatal("borrar el de antes tocó su identidad")
	}
	// la migración es una sola vez: al reabrir no vuelve a salir
	if n3, _ := nucleoServidores(t, datos); len(n3.MisServidores()) != 0 {
		t.Fatal("el de antes reaparece tras borrarlo")
	}
}

// Formato 1 de un jugador que nunca hospedó (formulario sin nombre, sin identidad): no se inventa ningún servidor. Y un PC
// con solo la identidad de OpenServer (sin lanzador.json) sí lo conserva, como «My server».
func TestMigracionSinServidorOSoloOpenServer(t *testing.T) {
	d := pruebas.TempDir(t)
	os.WriteFile(filepath.Join(d, ficheroAjustes), []byte(`{"v":1,"actualizar":true,"servidor":{"nombre":"","max":30,"publica":true}}`), 0o600)
	if n, _ := nucleoServidores(t, d); len(n.MisServidores()) != 0 {
		t.Fatalf("se inventó un servidor: %+v", n.MisServidores())
	}
	d2 := pruebas.TempDir(t)
	os.MkdirAll(filepath.Join(d2, "anfitrion"), 0o700)
	s, _, _ := sala.CargarAnfitrion(filepath.Join(d2, "anfitrion"), false, false)
	n2, _ := nucleoServidores(t, d2)
	ms := n2.MisServidores()
	if len(ms) != 1 || !ms[0].Legado || ms[0].Form.Nombre != nombreDeAntes || ms[0].Huella != s.Huella() {
		t.Fatalf("solo OpenServer: %+v", ms)
	}
}

// Con el PAQUETE, el servidor de antes guarda su mundo en servidor\<puerto>: Delete lo manda a la papelera (los nuevos,
// su carpeta servidores\<id> entera); al arrancar, uno nuevo nunca usa servidor\<puerto>.
func TestPaqueteDeAntesYNuevoSeparados(t *testing.T) {
	datos := pruebas.TempDir(t)
	exe := filepath.Join(datos, "paquete")
	for _, f := range []string{"server/yo2emu/run.py", "python/python.exe", "node/node.exe", "engine/duelcore.mjs",
		"server/yo2emu/paquete/primer_arranque.py", "vendor/cards.cdb"} {
		p := filepath.Join(exe, filepath.FromSlash(f))
		os.MkdirAll(filepath.Dir(p), 0o700)
		os.WriteFile(p, []byte("x"), 0o600)
	}
	viejo := filepath.Join(datos, "servidor", "5730")
	os.MkdirAll(viejo, 0o700)
	os.WriteFile(filepath.Join(viejo, "state.json.sqlite"), []byte("mundo"), 0o600)
	var papelera []string
	n := Nuevo(Config{Datos: datos, ExeDir: exe, Papelera: func(d string) error { papelera = append(papelera, d); return nil }})
	t.Cleanup(n.Cerrar)
	ms := n.MisServidores()
	if len(ms) != 1 || !ms[0].Legado || !ms[0].BorraDatos {
		t.Fatalf("el de antes del paquete: %+v", ms)
	}
	if err := n.BorrarMiServidor(ms[0].ID, ms[0].Form.Nombre); err != nil {
		t.Fatal(err)
	}
	if len(papelera) != 1 || papelera[0] != viejo {
		t.Fatalf("papelera: %v", papelera)
	}
}

// Uno a la vez y cada uno con lo suyo, arrancando de verdad (red P2P de 127.0.0.1, run.py de mentira): el entorno de run.py
// lleva SU estado y SUS logs (el de antes, ninguno: los de siempre), la identidad es la suya (otro peer ID, otro código) y
// no se puede arrancar un segundo, ni editar o borrar el que está en marcha.
func TestUnoALaVezYCadaUnoLoSuyo(t *testing.T) {
	juegoFalso(t, 5730+offHost)
	red := redLocal(t, 3)
	h, _ := nucleoDeRed(t, red)
	// el de antes: una identidad de siempre (como la de OpenServer) y un lanzador.json v1 con su formulario
	os.MkdirAll(filepath.Join(h.cfg.Datos, "anfitrion"), 0o700)
	sOld, _, _ := sala.CargarAnfitrion(filepath.Join(h.cfg.Datos, "anfitrion"), false, false)
	b, _ := json.Marshal(map[string]any{"v": 1, "actualizar": true, "servidor": formPrueba("De antes", sala.ReglasVanilla)})
	os.WriteFile(filepath.Join(h.cfg.Datos, ficheroAjustes), b, 0o600)
	h.aj = abrirAlmacen(h.cfg.Datos)
	var opcs []sala.OpcServidor
	ajustar := h.cfg.AjustarServidor
	h.cfg.AjustarServidor = func(o *sala.OpcServidor) { ajustar(o); opcs = append(opcs, *o) }

	viejo := h.MisServidores()[0]
	nuevo, err := h.CrearMiServidor(formPrueba("Nuevo", sala.ReglasMejorado))
	if err != nil {
		t.Fatal(err)
	}
	if err := h.ArrancarMiServidor(viejo.ID); err != nil {
		t.Fatal(err)
	}
	e := h.EstadoServidor()
	if !e.EnMarcha || e.ID != viejo.ID || e.Nombre != "De antes" || h.l.EstadoServidor().PeerID != sOld.PeerID().String() {
		t.Fatalf("el de antes no arranca con su identidad: %+v", e)
	}
	env := strings.Join(opcs[0].Env, "\n")
	if strings.Contains(env, "YO2_STATE=") || strings.Contains(env, "YO2_LOGDIR=") || opcs[0].DirDatos != "" {
		t.Fatalf("el de antes no debe cambiar dónde guarda su mundo:\n%s", env)
	}
	if err := h.ArrancarMiServidor(nuevo.ID); codigoDe(err) != "msg.srv.otro_en_marcha" {
		t.Fatalf("dos a la vez: %v", err)
	}
	if err := h.EditarMiServidor(viejo.ID, formPrueba("De antes", sala.ReglasVanilla)); codigoDe(err) != "msg.srv.editar_en_marcha" {
		t.Fatalf("editar el que está en marcha: %v", err)
	}
	if err := h.BorrarMiServidor(viejo.ID, "De antes"); codigoDe(err) != "msg.srv.borrar_en_marcha" {
		t.Fatalf("borrar el que está en marcha: %v", err)
	}
	if ms := h.MisServidores(); !ms[0].EnMarcha || ms[1].EnMarcha {
		t.Fatalf("tarjetas: %+v", ms)
	}
	codViejo := h.l.CodigoPrivado()
	if r := h.PararServidor(); !r.OK {
		t.Fatalf("parar: %+v", r)
	}
	if h.EstadoServidor().ID != "" {
		t.Fatal("sigue apuntando al de antes")
	}
	if err := h.ArrancarMiServidor(nuevo.ID); err != nil {
		t.Fatal(err)
	}
	defer h.PararServidor()
	pNuevo := h.l.EstadoServidor().PeerID
	if pNuevo == sOld.PeerID().String() || h.l.CodigoPrivado() == codViejo || h.EstadoServidor().Nombre != "Nuevo" {
		t.Fatal("el nuevo arrancó con la identidad del de antes")
	}
	o := opcs[len(opcs)-1]
	dir := filepath.Join(h.cfg.Datos, "servidores", nuevo.ID)
	env = strings.Join(o.Env, "\n")
	if o.DirAnfitrion != filepath.Join(dir, "anfitrion") || o.DirDatos != filepath.Join(dir, "servidor") ||
		!strings.Contains(env, "YO2_STATE="+filepath.Join(dir, "servidor", "state.json")) ||
		!strings.Contains(env, "YO2_LOGDIR="+filepath.Join(dir, "servidor", "logs")) || !strings.Contains(env, "YO2_PERFIL=enhanced") {
		t.Fatalf("el nuevo no usa su carpeta: %+v\n%s", o, env)
	}
	if g, _ := h.guardado(nuevo.ID); time.Since(g.Usado) > time.Minute {
		t.Fatal("no se apuntó el último arranque")
	}
}
