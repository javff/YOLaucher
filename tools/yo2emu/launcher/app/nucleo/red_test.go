package nucleo

// NET (12-10): Ajustes → Network (puerto fijo), «Join by address» de punta a punta con DOS núcleos y SIN DHT (ningún
// nodo de arranque: como si la DHT pública hubiera desaparecido), «Your server» con la dirección, retomar por dirección
// y la lista de nodos firmada al arrancar. Puertos fijos 5951-5955 (nunca los del juego ni los de las otras pruebas).

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/libp2p/go-libp2p/core/crypto"
	"github.com/libp2p/go-libp2p/core/peer"
	"yo2online/internal/pruebas"
	"yo2online/sala"
)

func TestEntrarPorDireccionDePuntaAPunta(t *testing.T) {
	juegoFalso(t, 5730+offHost)
	sinDHT := []peer.AddrInfo{} // ningún nodo de arranque: ni DHT ni relays

	// ---- anfitrión: Ajustes → Network → puerto fijo y servidor con contraseña
	h, _ := nucleoDeRed(t, sinDHT)
	if err := h.PonerPuertoFijo(true, 5745); codigoDe(err) != "msg.red.puerto_juego" {
		t.Fatalf("puerto del juego: %v", err)
	}
	if err := h.PonerPuertoFijo(true, 80); codigoDe(err) != "msg.red.puerto_rango" {
		t.Fatalf("puerto < 1024: %v", err)
	}
	if err := h.PonerPuertoFijo(true, 5951); err != nil {
		t.Fatal(err)
	}
	if a := h.Ajustes(); !a.Red.PuertoFijo || a.Red.Puerto != 5951 {
		t.Fatalf("ajuste guardado: %+v", a.Red)
	}
	var copiado string
	h.cfg.Portapapeles = func(s string) error { copiado = s; return nil }
	if err := h.CopiarDireccion(); codigoDe(err) != "msg.err.srv_parado" {
		t.Fatalf("copiar dirección sin servidor: %v", err)
	}
	form := FormServidor{Nombre: "Por direccion", Max: 8, Modo: ModoClave, Contrasena: "abre la puerta", Reglas: "vanilla", Cuentas: "libre"}
	if err := h.ArrancarServidor(form); err != nil {
		t.Fatal(err)
	}
	es := h.EstadoServidor()
	if es.Acceso == nil || !es.Acceso.Fijo || es.Acceso.Puerto != 5951 || es.Acceso.PuertoUDP != 5951 {
		t.Fatalf("«Your server» → acceso: %+v", es.Acceso)
	}
	if i := h.InfoRed(); !i.PuertoFijo || i.EnUso != 5951 || i.Reiniciar || i.NFabrica == 0 {
		t.Fatalf("InfoRed: %+v", i)
	}
	// en 127.0.0.1 no hay dirección pública: no se copia nada (y el código nunca sale por aquí)
	if err := h.CopiarDireccion(); codigoDe(err) != "msg.red.sin_direccion" || copiado != "" {
		t.Fatalf("copiar dirección: %v %q", err, copiado)
	}
	// cambiar el puerto con el servidor en marcha: vale al rearrancarlo
	if err := h.PonerPuertoFijo(true, 5952); err != nil {
		t.Fatal(err)
	}
	if i := h.InfoRed(); !i.Reiniciar || i.EnUso != 5951 || i.Puerto != 5952 {
		t.Fatalf("InfoRed tras cambiar: %+v", i)
	}
	h.cfg.Portapapeles = func(s string) error { copiado = s; return nil }
	if err := h.CopiarCodigo(); err != nil {
		t.Fatal(err)
	}
	codigo := copiado

	// ---- invitado
	inv, g := nucleoDeRed(t, sinDHT)
	if err := inv.EntrarPorDireccion("no es una dirección", DirPublico, ""); codigoDe(err) != "msg.err.dir_formato" {
		t.Fatalf("dirección mala: %v", err)
	}
	if err := inv.EntrarPorDireccion("127.0.0.1:5951", DirConCodigo, ""); codigoDe(err) != "msg.err.dir_falta_codigo" {
		t.Fatalf("sin código: %v", err)
	}
	if err := inv.EntrarPorDireccion("127.0.0.1:5951", DirConCodigo, "YO2-ABC"); codigoDe(err) != "msg.err.codigo_formato" {
		t.Fatalf("código mal: %v", err)
	}
	// sin nada: el servidor pide contraseña -> la página vuelve a abrir el diálogo de la dirección
	n0 := len(g.entradas())
	if err := inv.EntrarPorDireccion("127.0.0.1:5951", DirPublico, ""); err != nil {
		t.Fatal(err)
	}
	if ev := ultimaEntrada(t, g, n0); ev.Fase != "error" || !ev.PideClave || ev.Direccion != "127.0.0.1:5951" || ev.ServidorID != "" ||
		ev.Error.K != "msg.err.pide_clave" {
		t.Fatalf("sin contraseña: %+v", ev)
	}
	n0 = len(g.entradas())
	if err := inv.EntrarPorDireccion("127.0.0.1:5951", DirConClave, "mala"); err != nil {
		t.Fatal(err)
	}
	if ev := ultimaEntrada(t, g, n0); ev.Fase != "error" || !ev.PideClave || ev.Error.K != "msg.err.clave_mala" {
		t.Fatalf("contraseña mala: %+v", ev)
	}
	// Reintentar repite la última (por dirección)
	n0 = len(g.entradas())
	if err := inv.Reintentar(); err != nil {
		t.Fatal(err)
	}
	ultimaEntrada(t, g, n0)
	n0 = len(g.entradas())
	if err := inv.EntrarPorDireccion("127.0.0.1:5951", DirConClave, "abre la puerta"); err != nil {
		t.Fatal(err)
	}
	if ev := ultimaEntrada(t, g, n0); ev.Fase != "listo" {
		t.Fatalf("contraseña buena: %+v", ev)
	}
	if e := inv.Estado(); e.Conexion == nil || e.Conexion.Nombre != "127.0.0.1:5951" || !e.Conexion.Publica {
		t.Fatalf("conectado: %+v", e.Conexion)
	}
	inv.Desconectar()
	// con el código (salta la contraseña)
	n0 = len(g.entradas())
	if err := inv.EntrarPorDireccion("127.0.0.1:5951", DirConCodigo, codigo); err != nil {
		t.Fatal(err)
	}
	if ev := ultimaEntrada(t, g, n0); ev.Fase != "listo" {
		t.Fatalf("con código: %+v", ev)
	}
	inv.Desconectar()
	// nadie en esa dirección: error claro (sin reintentar por DHT ni Tor)
	inv2, g2 := nucleoDeRed(t, sinDHT)
	if err := inv2.EntrarPorDireccion("127.0.0.1:5953", DirConCodigo, codigo); err != nil {
		t.Fatal(err)
	}
	if ev := ultimaEntrada(t, g2, 0); ev.Fase != "error" || ev.Error.K != "msg.err.dir_sin_respuesta" || ev.PideClave {
		t.Fatalf("nadie en la dirección: %+v", ev)
	}
}

func TestRetomarPorDireccion(t *testing.T) {
	s, _, err := sala.CargarAnfitrion(pruebas.TempDir(t), false, false)
	if err != nil {
		t.Fatal(err)
	}
	cod := s.Codigo()
	r := Retomar{Dir: "1.2.3.4:47300", Codigo: cod}
	env := map[string]string{}
	for _, kv := range r.Entorno() {
		k, v, _ := strings.Cut(kv, "=")
		env[k] = v
	}
	if env[EnvRetomar] != "dir" || env[EnvRetomarDir] != "1.2.3.4:47300" {
		t.Fatalf("entorno: %v", env)
	}
	got := LeerRetomar(func(k string) string { return env[k] }, func(k string) { delete(env, k) })
	if got == nil || got.Dir != "1.2.3.4:47300" || got.Codigo != cod || len(env) != 0 {
		t.Fatalf("leído: %+v (quedan %v)", got, env)
	}
	env = map[string]string{EnvRetomar: "dir", EnvRetomarDir: "no vale nada"}
	if LeerRetomar(func(k string) string { return env[k] }, func(k string) { delete(env, k) }) != nil {
		t.Fatal("una dirección mala se retoma")
	}
}

// TestNodosDeArranqueAlArrancar: el núcleo de verdad (sin red de pruebas) arranca con la lista firmada guardada delante
// de la del usuario y la de fábrica, y con el puerto fijo de Ajustes.
func TestNodosDeArranqueAlArrancar(t *testing.T) {
	pub, priv, _ := ed25519.GenerateKey(rand.Reader)
	t.Cleanup(sala.UsarClaveEditorDePrueba(pub)) // nunca la clave del editor de verdad
	nodo := func(ip string) string {
		_, p, _ := crypto.GenerateEd25519Key(rand.Reader)
		id, _ := peer.IDFromPublicKey(p)
		return "/ip4/" + ip + "/tcp/4001/p2p/" + id.String()
	}
	firmado, usuario := nodo("198.51.100.1"), nodo("192.0.2.1")
	l, err := sala.FirmarListaRed(priv, sala.ListaRed{Version: 20261012000000, Arranque: []string{firmado}})
	if err != nil {
		t.Fatal(err)
	}
	datos := pruebas.TempDir(t)
	if ok, err := sala.GuardarRedFirmada(datos, l); !ok || err != nil {
		t.Fatal(err)
	}
	os.WriteFile(filepath.Join(datos, sala.NombreRedUsuario), []byte("bootstrap "+usuario+"\n"), 0o600)
	b, _ := json.Marshal(Ajustes{V: 1, Red: AjustesRed{PuertoFijo: true, Puerto: 5954}})
	os.WriteFile(filepath.Join(datos, ficheroAjustes), b, 0o600)

	n := Nuevo(Config{Datos: datos, ExeDir: datos, Exe: filepath.Join(datos, "YO2OnlineLauncher.exe")})
	t.Cleanup(n.Cerrar)
	a := n.l.Red.Arranque
	if len(a) < 3 || a[0].ID.String() != strings.Split(firmado, "/p2p/")[1] || a[1].ID.String() != strings.Split(usuario, "/p2p/")[1] {
		t.Fatalf("orden de los nodos de arranque: %v", a)
	}
	if n.l.PuertoFijo() != 5954 {
		t.Fatalf("puerto fijo: %d", n.l.PuertoFijo())
	}
	if i := n.InfoRed(); i.VersionFirmada != l.Version || i.OrigenFirmada != sala.OrigenFirmadaCach || i.NUsuario != 1 ||
		i.NFabrica != len(sala.ArranqueFabrica()) || !strings.HasSuffix(i.FicheroUsuario, sala.NombreRedUsuario) {
		t.Fatalf("InfoRed: %+v", i)
	}
}
