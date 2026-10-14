package nucleo

// Modo «Password» de punta a punta con DOS núcleos (anfitrión e invitado, cada uno con su carpeta de datos temporal) sobre
// la red P2P de 127.0.0.1 de integracion_test.go: formulario con contraseña, fila con candado, diálogo (pide_clave),
// contraseña mala, buena con «Remember password», entrar con lo guardado, contraseña cambiada y código de sala.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"yo2online/internal/pruebas"
	"yo2online/sala"
)

// ultimaEntrada espera a que la entrada acabe (listo o error) y devuelve el último evento.
func ultimaEntrada(t *testing.T, g *grabador, desde int) EventoEntrada {
	t.Helper()
	fin := time.Now().Add(30 * time.Second)
	for time.Now().Before(fin) {
		if evs := g.entradas(); len(evs) > desde {
			if ev := evs[len(evs)-1]; ev.Fase == "listo" || ev.Fase == "error" {
				return ev
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("la entrada no acaba: %+v", g.entradas()[desde:])
	return EventoEntrada{}
}

func filaDe(g *grabador, id string) (Fila, bool) {
	for _, f := range g.ultimaLista().Filas {
		if f.ID == id {
			return f, true
		}
	}
	return Fila{}, false
}

func TestContrasenaDePuntaAPunta(t *testing.T) {
	juegoFalso(t, 5730+offHost)
	red := redLocal(t, 3)
	const clave = "abre sésamo 2008"

	// ---- anfitrión: «Password» con contraseña
	h, _ := nucleoDeRed(t, red)
	var copiado string
	h.cfg.Portapapeles = func(s string) error { copiado = s; return nil }
	form := FormServidor{Nombre: "Con candado", Max: 8, Modo: ModoClave, Reglas: "vanilla", Cuentas: "libre"}
	if err := h.ArrancarServidor(form); codigoDe(err) != "msg.form.clave_vacia" {
		t.Fatalf("sin contraseña: %v", err)
	}
	form.Contrasena = "abc"
	if err := h.ArrancarServidor(form); codigoDe(err) != "msg.form.clave_corta" {
		t.Fatalf("contraseña corta: %v", err)
	}
	form.Contrasena = clave
	if err := h.ArrancarServidor(form); err != nil {
		t.Fatal(err)
	}
	esperar(t, 20*time.Second, "anuncio en la lista pública", func() bool { return h.EstadoServidor().ListadoOK })
	if es := h.EstadoServidor(); !es.Publica || !es.ConClave {
		t.Fatalf("estado: %+v", es)
	}
	// la contraseña no se guarda ni vuelve a la página: queda la clave derivada, protegida
	if f := h.FormServidor(); f.Contrasena != "" || !f.TieneClave || f.Modo != ModoClave {
		t.Fatalf("formulario guardado: %+v", f)
	}
	if a := h.Ajustes(); a.Servidores != nil || a.ViejaClave != nil {
		t.Fatal("la clave del servidor llega a la página")
	}
	for _, m := range h.MisServidores() {
		if m.Form.Contrasena != "" || !m.Form.TieneClave {
			t.Fatalf("tarjeta: %+v", m)
		}
	}
	b, _ := os.ReadFile(filepath.Join(h.cfg.Datos, ficheroAjustes))
	if strings.Contains(string(b), "sésamo") || strings.Contains(string(b), "contrasena\"") || !strings.Contains(string(b), "clave_servidor") {
		t.Fatalf("lanzador.json: %s", b)
	}
	// en modo Password el código se puede copiar (entra sin contraseña)
	if err := h.CopiarCodigo(); err != nil || !strings.HasPrefix(copiado, "YO2-") {
		t.Fatalf("copiar código: %v", err)
	}

	// ---- invitado: la fila lleva candado
	inv, g := nucleoDeRed(t, red)
	inv.Buscar()
	esperar(t, 30*time.Second, "el servidor en la lista", func() bool {
		for _, f := range g.ultimaLista().Filas {
			if f.Estado == sala.EstadoVivo {
				return true
			}
		}
		return false
	})
	f := g.ultimaLista().Filas[0]
	if !f.Contrasena || f.ClaveGuard || f.Nombre != "Con candado" {
		t.Fatalf("fila: %+v", f)
	}
	// Join sin contraseña: la página tiene que abrir el diálogo
	if err := inv.Entrar(f.ID); codigoDe(err) != "msg.err.pide_clave" {
		t.Fatalf("Join sin contraseña: %v", err)
	}
	if err := inv.EntrarConClave(f.ID, "", true); codigoDe(err) != "msg.err.clave_vacia" {
		t.Fatalf("contraseña vacía: %v", err)
	}
	// mala: error «Wrong password» que vuelve al diálogo
	n0 := len(g.entradas())
	if err := inv.EntrarConClave(f.ID, "abre sesamo 2008", true); err != nil {
		t.Fatal(err)
	}
	ev := ultimaEntrada(t, g, n0)
	if ev.Fase != "error" || !ev.PideClave || ev.ServidorID != f.ID || ev.Error.K != "msg.err.clave_mala" || !ev.SinReintento {
		t.Fatalf("contraseña mala: %+v", ev)
	}
	if inv.cl.tiene(f.ID) {
		t.Fatal("se guardó una contraseña mala")
	}
	// buena, con «Remember password»
	n0 = len(g.entradas())
	if err := inv.EntrarConClave(f.ID, clave, true); err != nil {
		t.Fatal(err)
	}
	if ev = ultimaEntrada(t, g, n0); ev.Fase != "listo" {
		t.Fatalf("contraseña buena: %+v", ev)
	}
	if e := inv.Estado(); e.Conexion == nil || !e.Conexion.Publica {
		t.Fatalf("conexión: %+v", e.Conexion)
	}
	bc, err := os.ReadFile(filepath.Join(inv.cfg.Datos, "lista", ficheroClaves))
	if err != nil || strings.Contains(string(bc), "sésamo") || !strings.Contains(string(bc), f.ID) {
		t.Fatalf("claves.json: %v %s", err, bc)
	}
	inv.emitirLista(true)
	if f2, _ := filaDe(g, f.ID); !f2.ClaveGuard {
		t.Fatal("la fila no sabe que la contraseña está guardada")
	}
	inv.Desconectar()

	// ---- otra vez, con lo guardado (sin diálogo)
	n0 = len(g.entradas())
	if err := inv.Entrar(f.ID); err != nil {
		t.Fatal(err)
	}
	if ev = ultimaEntrada(t, g, n0); ev.Fase != "listo" {
		t.Fatalf("con la contraseña guardada: %+v", ev)
	}
	inv.Desconectar()

	// ---- el código de sala entra sin contraseña
	n0 = len(g.entradas())
	if err := inv.EntrarConCodigo(copiado); err != nil {
		t.Fatal(err)
	}
	if ev = ultimaEntrada(t, g, n0); ev.Fase != "listo" {
		t.Fatalf("con el código: %+v", ev)
	}
	inv.Desconectar()

	// ---- el anfitrión cambia la contraseña: lo guardado deja de valer, se olvida y vuelve el diálogo
	if r := h.PararServidor(); !r.OK {
		t.Fatalf("parar: %+v", r)
	}
	form.Contrasena = "otra contraseña"
	if err := h.ArrancarServidor(form); err != nil {
		t.Fatal(err)
	}
	esperar(t, 20*time.Second, "anuncio otra vez", func() bool { return h.EstadoServidor().ListadoOK })
	n0 = len(g.entradas())
	if err := inv.Entrar(f.ID); err != nil {
		t.Fatal(err)
	}
	ev = ultimaEntrada(t, g, n0)
	if ev.Fase != "error" || !ev.PideClave || ev.Error.K != "msg.err.pide_clave" {
		t.Fatalf("contraseña cambiada: %+v", ev)
	}
	if inv.cl.tiene(f.ID) {
		t.Fatal("lo guardado de la contraseña vieja sigue ahí")
	}
	// y sin escribirla otra vez, el anfitrión arranca con la guardada (campo vacío = la de antes)
	h.PararServidor()
	form.Contrasena = ""
	form.TieneClave = true
	if err := h.ArrancarServidor(form); err != nil {
		t.Fatalf("rearrancar con la contraseña guardada: %v", err)
	}
	if !h.EstadoServidor().ConClave {
		t.Fatal("rearrancó sin contraseña")
	}
}

// «Remember password»: en disco solo va K cifrada con DPAPI (ni la contraseña ni K en claro) y vuelve igual.
func TestClavesGuardadasConDPAPI(t *testing.T) {
	d := pruebas.TempDir(t)
	ac := abrirClaves(d)
	k := []byte(strings.Repeat("K", sala.LenClave))
	sal := []byte(strings.Repeat("s", sala.LenSalClave))
	id := "12D3KooWPrueba"
	if err := ac.recordar(id, sala.CredencialGuardada(1, sal, k)); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(d, "lista", ficheroClaves))
	if strings.Contains(string(b), b64.EncodeToString(k)) || !strings.Contains(string(b), id) {
		t.Fatalf("claves.json: %s", b)
	}
	cr := abrirClaves(d).credencial(id) // otro arranque
	if cr == nil {
		t.Fatal("no se pudo leer lo guardado")
	}
	if _, s2, k2, ok := cr.Derivada(); !ok || string(s2) != string(sal) || string(k2) != string(k) {
		t.Fatal("no vuelve igual")
	}
	// un fichero tocado (K que no es de DPAPI) no se usa
	os.WriteFile(filepath.Join(d, "lista", ficheroClaves), []byte(`{"v":1,"claves":{"`+id+`":{"kdf":1,"sal":"`+b64.EncodeToString(sal)+`","clave":"`+b64.EncodeToString(k)+`"}}}`), 0o600)
	if abrirClaves(d).credencial(id) != nil {
		t.Fatal("aceptó una clave sin DPAPI")
	}
	if err := ac.olvidar(id); err != nil || ac.tiene(id) {
		t.Fatalf("olvidar: %v", err)
	}
}
