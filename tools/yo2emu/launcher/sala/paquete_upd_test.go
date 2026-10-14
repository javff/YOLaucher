package sala

// Pruebas de la versión firmada del paquete (paquete_man.go, paquete_red.go, paquete_inst.go): firma, rutas,
// diferencial, rechazos (firma mala, sha256 distinto, fichero de más, ruta con ..), aplicar y deshacer, y de punta a
// punta por libp2p (lista) y por el túnel (Join) con la DHT de 3 nodos en 127.0.0.1 de lista_test.go. La clave del
// editor es una de prueba generada en el momento (la de verdad nunca está aquí).

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
	"yo2online/internal/pruebas"
)

type arbolPrueba map[string]string // ruta del manifiesto -> contenido (sin el exe ni su .firma)

func arbolV1() arbolPrueba {
	return arbolPrueba{
		"README.txt":           "readme v1",
		"server/yo2emu/run.py": "print('v1')",
		"server/yo2emu/paquete/primer_arranque.py": "print('datos')",
		"server/yo2emu/viejo.py":                   "solo en v1",
		"python/python.exe":                        "python",
		"python/python312._pth":                    "python312.zip\r\n.\r\n",
		"node/node.exe":                            "node",
		"engine/duelcore.mjs":                      "motor",
		"vendor/cards.cdb":                         "cdb",
		"vendor/CardScripts/c1.lua":                "lua 1",
	}
}

func arbolV2() arbolPrueba {
	a := arbolV1()
	a["README.txt"] = "readme v2"
	a["server/yo2emu/run.py"] = "print('v2')"
	delete(a, "server/yo2emu/viejo.py")
	a["vendor/CardScripts/c2.lua"] = "lua 2"
	return a
}

func clavePrueba(t *testing.T) ed25519.PrivateKey {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(UsarClaveEditorDePrueba(pub))
	return priv
}

func firmaExePrueba(priv ed25519.PrivateKey, contenido []byte, v int64) []byte {
	sum := shaBytes(contenido)
	f := FirmaLanzador{V: FirmaVersionV, Nombre: NombreLanzadorApp, Version: v, SHA256: sum, Tam: int64(len(contenido))}
	f.Firma = base64.StdEncoding.EncodeToString(ed25519.Sign(priv, f.Mensaje()))
	b, _ := json.Marshal(f)
	return b
}

func shaBytes(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

// escribirArbol: los ficheros, el exe con su .firma (versión vl) y paquete.json firmado (versión v). Devuelve lo firmado.
func escribirArbol(t *testing.T, priv ed25519.PrivateKey, dir string, a arbolPrueba, exe string, v, vl int64) *PaqueteVerificado {
	t.Helper()
	for r, c := range a {
		p := filepath.Join(dir, filepath.FromSlash(r))
		os.MkdirAll(filepath.Dir(p), 0o755)
		if err := os.WriteFile(p, []byte(c), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	os.WriteFile(filepath.Join(dir, ExeLanzadorApp), []byte(exe), 0o755)
	os.WriteFile(filepath.Join(dir, ExeLanzadorApp+ExtFirma), firmaExePrueba(priv, []byte(exe), vl), 0o644)
	raw, err := ManifiestoDeArbol(dir, v, vl)
	if err != nil {
		t.Fatal(err)
	}
	out, err := FirmarManifiesto(priv, raw)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, NombrePaquete), out, 0o644); err != nil {
		t.Fatal(err)
	}
	pv, err := VerificarPaquete(out)
	if err != nil {
		t.Fatal(err)
	}
	if err := ComprobarArbol(dir, pv.Man); err != nil {
		t.Fatalf("el árbol de prueba no cuadra: %v", err)
	}
	return pv
}

// clientePipe: el anfitrión sp al otro lado de una tubería en memoria.
func clientePipe(sp *ServidorPaquete) ClientePaquete {
	return ClientePaquete{Quien: "tubería", Abrir: func(ctx context.Context) (flujoPaquete, error) {
		a, b := net.Pipe()
		go sp.Atender(b, "prueba")
		return a, nil
	}}
}

func instPrueba(dir string, t *testing.T) *Instalacion {
	return &Instalacion{Raiz: dir, Exe: filepath.Join(dir, ExeLanzadorApp), Logf: t.Logf,
		ComprobarExe: func(string, int64) error { return nil }}
}

// igualA: la carpeta tiene instalada exactamente la versión pv (y nada de lo que la versión ya no lleva).
func igualA(t *testing.T, dir string, pv *PaqueteVerificado, noEsta ...string) {
	t.Helper()
	for _, f := range pv.Man.Ficheros {
		if !shaIgual(filepath.Join(dir, filepath.FromSlash(f.Ruta)), f) {
			t.Fatalf("%s no es el de la versión %d", f.Ruta, pv.Man.Version)
		}
	}
	for _, r := range noEsta {
		if Existe(filepath.Join(dir, filepath.FromSlash(r))) {
			t.Fatalf("%s sigue ahí", r)
		}
	}
	b, _ := os.ReadFile(filepath.Join(dir, NombrePaquete))
	if !bytes.Equal(b, pv.Bytes) {
		t.Fatal("paquete.json no es el de esa versión")
	}
}

// ---------------------------------------------------------------- firma y rutas

func TestPaqueteFirmaYRutas(t *testing.T) {
	priv := clavePrueba(t)
	dir := pruebas.TempDir(t)
	pv := escribirArbol(t, priv, dir, arbolV1(), "exe v1", 20261012000001, 20261012000000)
	if pv.Man.Version != 20261012000001 || pv.Fichero("server/yo2emu/run.py") == nil || pv.Fichero("SERVER/yo2emu/run.py") == nil {
		t.Fatalf("manifiesto: %+v", pv.Man)
	}
	// otra clave: no vale
	_, otra, _ := ed25519.GenerateKey(rand.Reader)
	raw, _ := ManifiestoDeArbol(dir, 20261012000001, 20261012000000)
	mala, _ := FirmarManifiesto(otra, raw)
	if _, err := VerificarPaquete(mala); err == nil || !strings.Contains(err.Error(), "NO es la del editor") {
		t.Fatalf("firma de otra clave aceptada: %v", err)
	}
	// un byte cambiado del manifiesto: no vale
	tocado := bytes.Replace(pv.Bytes, []byte(`"tam":5`), []byte(`"tam":6`), 1)
	if bytes.Equal(tocado, pv.Bytes) {
		t.Fatal("la prueba no ha tocado nada")
	}
	if _, err := VerificarPaquete(tocado); err == nil {
		t.Fatal("manifiesto tocado aceptado")
	}
	// rutas
	malas := []string{"../x.txt", "server/../x.py", "/server/a", `server\a`, "C:/x.txt", "server/a:b", "server/CON.py",
		"server/nul", "server/a./b", "x.exe", "otra/a.py", "server//a", "server/./a", NombrePaquete, "_update/v1/a",
		"server/a\x01", "YO2Launcher.exe", "server/ a"}
	for _, r := range malas {
		if RutaPaqueteValida(r) {
			t.Errorf("ruta aceptada: %q", r)
		}
	}
	for _, r := range []string{"server/yo2emu/run.py", "vendor/CardScripts/c1.lua", "README.txt", ExeLanzadorApp, ExeLanzadorApp + ExtFirma, "python/python312._pth"} {
		if !RutaPaqueteValida(r) {
			t.Errorf("ruta buena rechazada: %q", r)
		}
	}
	// manifiestos que no se firman ni se aceptan
	base := func() ManifiestoPaquete {
		var m ManifiestoPaquete
		json.Unmarshal(raw, &m)
		return m
	}
	casos := map[string]func(*ManifiestoPaquete){
		"ruta con ..":     func(m *ManifiestoPaquete) { m.Ficheros[0].Ruta = "server/../README.txt" },
		"parte que no es": func(m *ManifiestoPaquete) { m.Ficheros[0].Parte = "vendor" },
		"mayúsculas": func(m *ManifiestoPaquete) {
			m.Ficheros = append(m.Ficheros, m.Ficheros[1])
			m.Ficheros[len(m.Ficheros)-1].Ruta = strings.ToUpper(m.Ficheros[1].Ruta[:1]) + m.Ficheros[1].Ruta[1:]
		},
		"fichero y carpeta": func(m *ManifiestoPaquete) {
			f := m.Ficheros[0]
			f.Ruta, f.Parte = "server/yo2emu", "server"
			m.Ficheros = append(m.Ficheros, f)
		},
		"sha mal": func(m *ManifiestoPaquete) { m.Ficheros[0].SHA256 = strings.ToUpper(m.Ficheros[0].SHA256) },
		"sin exe": func(m *ManifiestoPaquete) {
			var r []ArchivoPaquete
			for _, f := range m.Ficheros {
				if f.Ruta != ExeLanzadorApp {
					r = append(r, f)
				}
			}
			m.Ficheros = r
		},
		"versión 0": func(m *ManifiestoPaquete) { m.Version = 0 },
	}
	for nombre, cambiar := range casos {
		m := base()
		cambiar(&m)
		b, _ := json.Marshal(m)
		if _, err := FirmarManifiesto(priv, b); err == nil {
			t.Errorf("%s: se firmó", nombre)
		}
		// firmado a la fuerza (sin validar): el lanzador lo rechaza igual
		sig := base64.StdEncoding.EncodeToString(ed25519.Sign(priv, msgPaquete(b)))
		forzado := append([]byte(`{"v":1,"firma":"`+sig+`","manifiesto":`), append(b, '}')...)
		if _, err := VerificarPaquete(forzado); err == nil {
			t.Errorf("%s: aceptado", nombre)
		}
	}
	// el árbol tiene que ser exactamente el manifiesto
	os.WriteFile(filepath.Join(dir, "server", "yo2emu", "de_mas.py"), []byte("x"), 0o644)
	if err := ComprobarArbol(dir, pv.Man); err == nil || !strings.Contains(err.Error(), "no está en el manifiesto") {
		t.Fatalf("fichero de más en el árbol: %v", err)
	}
}

// ---------------------------------------------------------------- diferencial y aplicar

func TestPaqueteDiferencialYAplicar(t *testing.T) {
	priv := clavePrueba(t)
	srv, cli := pruebas.TempDir(t), pruebas.TempDir(t)
	v2 := escribirArbol(t, priv, srv, arbolV2(), "exe v2", 2, 20)
	v1 := escribirArbol(t, priv, cli, arbolV1(), "exe v1", 1, 10)
	// lo que no es del paquete y no se toca; y la caché de Python, que desaparece al cambiar server\
	os.WriteFile(filepath.Join(cli, "notas.txt"), []byte("mías"), 0o644)
	os.MkdirAll(filepath.Join(cli, "server", "yo2emu", "__pycache__"), 0o755)
	os.WriteFile(filepath.Join(cli, "server", "yo2emu", "__pycache__", "run.pyc"), []byte("pyc"), 0o644)

	sp := &ServidorPaquete{Semilla: srv, Logf: t.Logf}
	if sp.Version() != 2 {
		t.Fatalf("reparte la versión %d", sp.Version())
	}
	c := clientePipe(sp)
	if v, err := c.Version(context.Background()); err != nil || v != 2 {
		t.Fatalf("versión %d, %v", v, err)
	}
	pv, err := c.Manifiesto(context.Background())
	if err != nil || !bytes.Equal(pv.Bytes, v2.Bytes) {
		t.Fatalf("manifiesto: %v", err)
	}
	inst := instPrueba(cli, t)
	if _, v := inst.Instalada(); v != 1 {
		t.Fatalf("instalada %d", v)
	}
	var progreso [][2]int64
	p, err := inst.Preparar(context.Background(), c, pv, func(h, tot int64) { progreso = append(progreso, [2]int64{h, tot}) })
	if err != nil {
		t.Fatal(err)
	}
	// solo baja lo que cambia: run.py, c2.lua, README.txt, el exe y su .firma
	var esperado int64
	for _, f := range v2.Man.Ficheros {
		if g := v1.Fichero(f.Ruta); g == nil || g.SHA256 != f.SHA256 {
			esperado += f.Tam
		}
	}
	if p.Bytes != esperado || len(progreso) == 0 || progreso[len(progreso)-1] != [2]int64{esperado, esperado} {
		t.Fatalf("bajado %d, esperado %d (progreso %v)", p.Bytes, esperado, progreso)
	}
	if !reflect.DeepEqual(p.Partes, []string{"server", "vendor"}) || !p.Exe || len(p.Borrar) != 0 {
		t.Fatalf("pendiente: %+v", p)
	}
	if q := inst.Pendiente(); q == nil || q.Version != 2 {
		t.Fatalf("pendiente guardado: %+v", q)
	}
	igualA(t, cli, v1) // preparar no toca nada instalado
	if _, err := inst.Preparar(context.Background(), c, pv, nil); !errors.Is(err, ErrNoMasNueva) {
		t.Fatalf("preparar dos veces: %v", err)
	}
	// con el servidor en marcha no se aplica
	if _, err := inst.Aplicar(func() bool { return true }); !errors.Is(err, ErrServidorEnMarcha) {
		t.Fatalf("con el servidor en marcha: %v", err)
	}
	r, err := inst.Aplicar(func() bool { return false })
	if err != nil || r == nil || r.Version != 2 || !r.ExeCambiado {
		t.Fatalf("aplicar: %+v %v", r, err)
	}
	igualA(t, cli, v2, "server/yo2emu/viejo.py", "server/yo2emu/__pycache__")
	if b, _ := os.ReadFile(filepath.Join(cli, "notas.txt")); string(b) != "mías" {
		t.Fatal("se tocó un fichero que no es del paquete")
	}
	if inst.Pendiente() != nil || Existe(inst.dir("aplicando.json")) {
		t.Fatal("queda algo pendiente")
	}
	inst.Limpiar() // AplicarAlArrancar lo hace aparte
	ents, _ := os.ReadDir(inst.dir())
	for _, e := range ents {
		if strings.HasPrefix(e.Name(), "viejo-") || e.Name() == "objetos" || e.Name() == "v2" {
			t.Fatalf("no se limpió %s", e.Name())
		}
	}
	// anti-rollback: la v1 ya no se instala
	sp1 := &ServidorPaquete{Semilla: pruebas.TempDir(t)}
	escribirArbol(t, priv, sp1.Semilla, arbolV1(), "exe v1", 1, 10)
	pv1, _ := clientePipe(sp1).Manifiesto(context.Background())
	if _, err := inst.Preparar(context.Background(), clientePipe(sp1), pv1, nil); !errors.Is(err, ErrNoMasNueva) {
		t.Fatalf("bajar de versión: %v", err)
	}
	// y la carpeta, ahora de la v2, reparte la v2 (el exe se sirve desde el exe en marcha)
	spc := &ServidorPaquete{Raiz: cli, Exe: inst.Exe}
	if spc.Version() != 2 {
		t.Fatalf("la instalación actualizada reparte la %d", spc.Version())
	}
	otro := instPrueba(pruebas.TempDir(t), t)
	escribirArbol(t, priv, otro.Raiz, arbolV1(), "exe v1", 1, 10)
	pvc, _ := clientePipe(spc).Manifiesto(context.Background())
	if _, err := otro.Preparar(context.Background(), clientePipe(spc), pvc, nil); err != nil {
		t.Fatalf("de una instalación actualizada a otra: %v", err)
	}
}

// Un zip de antes del 12-10: sin paquete.json, con el exe ya actualizado por el camino viejo. Baja el resto.
func TestPaqueteDesdeZipSinVersion(t *testing.T) {
	priv := clavePrueba(t)
	srv, cli := pruebas.TempDir(t), pruebas.TempDir(t)
	v2 := escribirArbol(t, priv, srv, arbolV2(), "exe v2", 2, 20)
	escribirArbol(t, priv, cli, arbolV1(), "exe v2", 1, 20) // el exe ya es el nuevo (camino viejo)
	os.Remove(filepath.Join(cli, NombrePaquete))
	inst := NuevaInstalacion(filepath.Join(cli, ExeLanzadorApp), t.Logf)
	if inst == nil {
		t.Fatal("no se reconoce como paquete")
	}
	inst.ComprobarExe = func(string, int64) error { return errors.New("no debería arrancar el exe: no cambia") }
	if _, v := inst.Instalada(); v != 0 {
		t.Fatalf("instalada %d", v)
	}
	p, err := inst.Preparar(context.Background(), clientePipe(&ServidorPaquete{Semilla: srv}), v2, nil)
	if err != nil {
		t.Fatal(err)
	}
	if p.Exe || !reflect.DeepEqual(p.Partes, []string{"server", "vendor"}) {
		t.Fatalf("pendiente: %+v", p)
	}
	if _, err := inst.Aplicar(nil); err != nil {
		t.Fatal(err)
	}
	igualA(t, cli, v2)
	// el exe de desarrollo (sin paquete al lado) no se actualiza por aquí
	if NuevaInstalacion(filepath.Join(pruebas.TempDir(t), ExeLanzadorApp), nil) != nil {
		t.Fatal("una carpeta sin paquete se toma por instalación")
	}
}

// ---------------------------------------------------------------- rechazos

// servidorTramposo: contesta 'M' con paquete y 'B' con lo que diga cuerpo(f) por cada fichero (+ extra al final).
func servidorTramposo(paquete []byte, cuerpo func(ArchivoPaquete) []byte, extra []byte) ClientePaquete {
	pv, _ := VerificarPaquete(paquete)
	return ClientePaquete{Quien: "tramposo", Abrir: func(ctx context.Context) (flujoPaquete, error) {
		a, b := net.Pipe()
		go func() {
			defer b.Close()
			var cab [4]byte
			io.ReadFull(b, cab[:])
			switch cab[3] {
			case opManifiesto:
				b.Write([]byte{estOK})
				zw := gzip.NewWriter(b)
				zw.Write(paquete)
				zw.Close()
			case opLote:
				var n [2]byte
				io.ReadFull(b, n[:])
				var fs []ArchivoPaquete
				for range int(binary.BigEndian.Uint16(n[:])) {
					var l [2]byte
					io.ReadFull(b, l[:])
					r := make([]byte, binary.BigEndian.Uint16(l[:]))
					io.ReadFull(b, r)
					if pv != nil {
						fs = append(fs, *pv.Fichero(string(r)))
					}
				}
				b.Write([]byte{estOK})
				zw := gzip.NewWriter(b)
				for _, f := range fs {
					zw.Write(cuerpo(f))
				}
				zw.Write(extra)
				zw.Close()
			}
		}()
		return a, nil
	}}
}

func TestPaqueteRechazos(t *testing.T) {
	priv := clavePrueba(t)
	srv := pruebas.TempDir(t)
	v2 := escribirArbol(t, priv, srv, arbolV2(), "exe v2", 2, 20)
	nuevoCliente := func() (*Instalacion, *PaqueteVerificado) {
		d := pruebas.TempDir(t)
		return instPrueba(d, t), escribirArbol(t, priv, d, arbolV1(), "exe v1", 1, 10)
	}
	ctx := context.Background()
	original := func(f ArchivoPaquete) []byte {
		b, _ := os.ReadFile(filepath.Join(srv, filepath.FromSlash(f.Ruta)))
		return b
	}

	t.Run("manifiesto mal firmado", func(t *testing.T) {
		_, otra, _ := ed25519.GenerateKey(rand.Reader)
		raw, _ := ManifiestoDeArbol(srv, 3, 20)
		mala, _ := FirmarManifiesto(otra, raw)
		if _, err := servidorTramposo(mala, original, nil).Manifiesto(ctx); err == nil {
			t.Fatal("aceptado")
		}
	})
	t.Run("sha256 distinto", func(t *testing.T) {
		inst, v1 := nuevoCliente()
		mal := func(f ArchivoPaquete) []byte { return bytes.Repeat([]byte{'x'}, int(f.Tam)) } // mismo tamaño, otro contenido
		if _, err := inst.Preparar(ctx, servidorTramposo(v2.Bytes, mal, nil), v2, nil); err == nil || !strings.Contains(err.Error(), "sha256") {
			t.Fatalf("contenido cambiado aceptado: %v", err)
		}
		if inst.Pendiente() != nil {
			t.Fatal("quedó algo preparado")
		}
		igualA(t, inst.Raiz, v1)
	})
	t.Run("datos de más en el lote", func(t *testing.T) {
		inst, _ := nuevoCliente()
		if _, err := inst.Preparar(ctx, servidorTramposo(v2.Bytes, original, []byte("de más")), v2, nil); err == nil || !strings.Contains(err.Error(), "más datos") {
			t.Fatalf("lote con bytes de más aceptado: %v", err)
		}
	})
	t.Run("fichero tocado en el anfitrión", func(t *testing.T) {
		otro := pruebas.TempDir(t)
		escribirArbol(t, priv, otro, arbolV2(), "exe v2", 2, 20)
		os.WriteFile(filepath.Join(otro, "server", "yo2emu", "run.py"), []byte("print('v9')"), 0o644) // mismo tamaño
		inst, _ := nuevoCliente()
		c := clientePipe(&ServidorPaquete{Semilla: otro, Logf: t.Logf})
		pv, err := c.Manifiesto(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := inst.Preparar(ctx, c, pv, nil); err == nil || !strings.Contains(err.Error(), "estado 4") {
			t.Fatalf("el anfitrión repartió un fichero que no es el firmado: %v", err)
		}
	})
	t.Run("fichero de más en lo preparado", func(t *testing.T) {
		inst, v1 := nuevoCliente()
		if _, err := inst.Preparar(ctx, clientePipe(&ServidorPaquete{Semilla: srv}), v2, nil); err != nil {
			t.Fatal(err)
		}
		os.WriteFile(filepath.Join(inst.dir("v2"), "server", "yo2emu", "colado.py"), []byte("import os"), 0o644)
		if _, err := inst.Aplicar(nil); err == nil || !strings.Contains(err.Error(), "de más") {
			t.Fatalf("fichero colado aceptado: %v", err)
		}
		igualA(t, inst.Raiz, v1, "server/yo2emu/colado.py")
		if !inst.Fallida(2) || inst.Pendiente() != nil {
			t.Fatal("la versión no quedó marcada como fallida")
		}
		if _, err := inst.Preparar(ctx, clientePipe(&ServidorPaquete{Semilla: srv}), v2, nil); err == nil {
			t.Fatal("se reintenta una versión fallida")
		}
	})
	t.Run("exe que no arranca", func(t *testing.T) {
		inst, v1 := nuevoCliente()
		inst.ComprobarExe = func(string, int64) error { return errors.New("bloqueado por el antivirus") }
		if _, err := inst.Preparar(ctx, clientePipe(&ServidorPaquete{Semilla: srv}), v2, nil); err == nil || !strings.Contains(err.Error(), "no arranca") {
			t.Fatalf("exe que no arranca aceptado: %v", err)
		}
		igualA(t, inst.Raiz, v1)
	})
	t.Run("límites del anfitrión", func(t *testing.T) {
		antes := maxLotesPorPar
		maxLotesPorPar = 0
		defer func() { maxLotesPorPar = antes }()
		inst, _ := nuevoCliente()
		if _, err := inst.Preparar(ctx, clientePipe(&ServidorPaquete{Semilla: srv}), v2, nil); !errors.Is(err, ErrOcupado) {
			t.Fatalf("sin sitio: %v", err)
		}
	})
	t.Run("ruta que no está en el manifiesto", func(t *testing.T) {
		c := clientePipe(&ServidorPaquete{Semilla: srv, Logf: t.Logf})
		err := c.Lote(ctx, []ArchivoPaquete{{Ruta: "../../secreto.txt", Tam: 1}}, func(ArchivoPaquete, io.Reader) error { return nil })
		if err == nil || !strings.Contains(err.Error(), "estado 1") {
			t.Fatalf("pidió fuera del manifiesto: %v", err)
		}
	})
}

// ---------------------------------------------------------------- deshacer

func TestPaqueteAplicarDeshace(t *testing.T) {
	priv := clavePrueba(t)
	srv := pruebas.TempDir(t)
	v2 := escribirArbol(t, priv, srv, arbolV2(), "exe v2", 2, 20)
	ctx := context.Background()
	preparado := func() (*Instalacion, *PaqueteVerificado) {
		d := pruebas.TempDir(t)
		inst := instPrueba(d, t)
		v1 := escribirArbol(t, priv, d, arbolV1(), "exe v1", 1, 10)
		if _, err := inst.Preparar(ctx, clientePipe(&ServidorPaquete{Semilla: srv}), v2, nil); err != nil {
			t.Fatal(err)
		}
		return inst, v1
	}

	t.Run("una carpeta en uso", func(t *testing.T) {
		inst, v1 := preparado()
		// vendor\ con un fichero abierto (como python.exe o node.exe en marcha): no se puede renombrar
		f, err := os.Open(filepath.Join(inst.Raiz, "vendor", "cards.cdb"))
		if err != nil {
			t.Fatal(err)
		}
		_, err = inst.Aplicar(nil)
		if err == nil {
			f.Close()
			t.Skip("este sistema deja renombrar una carpeta con un fichero abierto")
		}
		igualA(t, inst.Raiz, v1)
		if Existe(inst.dir("aplicando.json")) || inst.Pendiente() == nil {
			t.Fatal("tras deshacer debería seguir pendiente y sin diario")
		}
		f.Close()
		if r, err := inst.Aplicar(nil); err != nil || r == nil {
			t.Fatalf("al soltarla: %v", err)
		}
		igualA(t, inst.Raiz, v2, "server/yo2emu/viejo.py")
	})
	t.Run("corte a medias", func(t *testing.T) {
		inst, v1 := preparado()
		for _, corte := range []int{1, 3, 6} {
			cortePrueba = func(k int) bool { return k == corte }
			_, err := inst.Aplicar(nil)
			cortePrueba = nil
			if err == nil || !Existe(inst.dir("aplicando.json")) {
				t.Fatalf("corte %d: %v", corte, err)
			}
			inst.Recuperar() // lo que hace el siguiente arranque
			igualA(t, inst.Raiz, v1)
			if Existe(inst.dir("aplicando.json")) {
				t.Fatal("queda el diario")
			}
			if corte == 6 {
				break
			}
		}
		// tres cortes = versión fallida; nada más que hacer con ella
		if !inst.Fallida(2) || inst.Pendiente() != nil {
			t.Fatal("tras 3 cortes debería quedar como fallida")
		}
	})
	t.Run("diario que no se pudo borrar tras aplicar bien", func(t *testing.T) {
		inst, _ := preparado()
		stg := inst.dir("v2")
		if _, err := inst.Aplicar(nil); err != nil {
			t.Fatal(err)
		}
		// como si el antivirus no hubiera dejado borrar el diario: el siguiente arranque NO debe deshacer nada
		d, _ := json.Marshal(diario{Version: 2, Pasos: []paso{{filepath.Join(stg, "server"), filepath.Join(inst.Raiz, "server")}}})
		os.WriteFile(inst.dir("aplicando.json"), d, 0o644)
		inst.Recuperar()
		igualA(t, inst.Raiz, v2, "server/yo2emu/viejo.py")
		if Existe(inst.dir("aplicando.json")) {
			t.Fatal("queda el diario")
		}
	})
	t.Run("corte y luego bien", func(t *testing.T) {
		inst, _ := preparado()
		cortePrueba = func(k int) bool { return k == 2 }
		inst.Aplicar(nil)
		cortePrueba = nil
		r, err := inst.Aplicar(nil) // Recuperar va dentro
		if err != nil || r == nil {
			t.Fatal(err)
		}
		igualA(t, inst.Raiz, v2, "server/yo2emu/viejo.py")
	})
}

// ---------------------------------------------------------------- de punta a punta (lista y Join)

func TestPaqueteRedDePuntaAPunta(t *testing.T) {
	priv := clavePrueba(t)
	juegoFalso(t, 5730+offPrueba)
	red := redLocal(t, 3)
	semilla := pruebas.TempDir(t)
	v2 := escribirArbol(t, priv, semilla, arbolV2(), "exe v2", 2, 20)

	anf := lanzadorDePrueba(t, red, &registro{})
	anf.Paquete = &ServidorPaquete{Semilla: semilla, Logf: t.Logf}
	crearPublico(t, anf, "PAQUETE nuevo")
	viejo := lanzadorDePrueba(t, red, &registro{}) // un anfitrión que no reparte paquete (como uno antiguo)
	crearPublico(t, viejo, "PAQUETE viejo")

	inv := lanzadorDePrueba(t, red, &registro{})
	var nuevo, antiguo ServidorVisto
	esperar(t, 60*time.Second, "los dos servidores en la lista", func() bool {
		for _, s := range vivos(buscarTodo(t, inv)) {
			switch s.Ficha.Nombre {
			case "PAQUETE nuevo":
				nuevo = s
			case "PAQUETE viejo":
				antiguo = s
			}
		}
		return nuevo.ID != "" && antiguo.ID != ""
	})
	if nuevo.Ficha.VersionPaquete != 2 || antiguo.Ficha.VersionPaquete != 0 {
		t.Fatalf("fichas: %d / %d", nuevo.Ficha.VersionPaquete, antiguo.Ficha.VersionPaquete)
	}

	// (a) lista: libp2p, sin código
	c, err := inv.ClientePaqueteP2P(nuevo.ID)
	if err != nil {
		t.Fatal(err)
	}
	pv, err := c.Manifiesto(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	inst := instPrueba(pruebas.TempDir(t), t)
	escribirArbol(t, priv, inst.Raiz, arbolV1(), "exe v1", 1, 10)
	if _, err := inst.Preparar(context.Background(), c, pv, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := inst.Aplicar(nil); err != nil {
		t.Fatal(err)
	}
	igualA(t, inst.Raiz, v2, "server/yo2emu/viejo.py")
	if c2, _ := inv.ClientePaqueteP2P(antiguo.ID); true {
		if _, err := c2.Version(context.Background()); !errors.Is(err, ErrSinPaquete) {
			t.Fatalf("anfitrión sin paquete por libp2p: %v", err)
		}
	}

	// (b) Join: por el túnel (puerto 3)
	con, err := inv.Entrar(context.Background(), Destino{Servidor: &nuevo}, OpcEntrar{Modo: "p2p", SinMDNS: true, SinTuneles: true}, nil)
	if err != nil {
		t.Fatal(err)
	}
	ct := ClientePaqueteTunel(con.Invitado(), "túnel")
	if v, err := ct.Version(context.Background()); err != nil || v != 2 {
		t.Fatalf("versión por el túnel: %d %v", v, err)
	}
	inst2 := instPrueba(pruebas.TempDir(t), t)
	escribirArbol(t, priv, inst2.Raiz, arbolV1(), "exe v1", 1, 10)
	pvt, err := ct.Manifiesto(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := inst2.Preparar(context.Background(), ct, pvt, nil); err != nil {
		t.Fatal(err)
	}
	con.Cerrar()
	// un anfitrión antiguo cierra el puerto 3 sin contestar: «sin paquete» (y se sigue con el camino viejo)
	con2, err := inv.Entrar(context.Background(), Destino{Codigo: viejo.CodigoPrivado()}, OpcEntrar{Modo: "p2p", SinMDNS: true, SinTuneles: true}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer con2.Cerrar()
	if _, err := ClientePaqueteTunel(con2.Invitado(), "túnel").Version(context.Background()); !errors.Is(err, ErrSinPaquete) {
		t.Fatalf("anfitrión sin paquete por el túnel: %v", err)
	}
}

// ---------------------------------------------------------------- con exes de verdad (a mano)

// TestPaqueteExeDeVerdad: con dos YO2OnlineLauncher.exe compilados con la clave pública de PRUEBA incrustada
// (-ldflags "-X yo2online/sala.claveEditorHex=<pública>") y versiones N y N+1, prepara la N+1 en una carpeta de
// paquete con la N y la aplica el propio exe N («-aplicar-actualizacion», sin ventana): el exe en marcha se renombra a
// sí mismo y queda el N+1. Solo con YO2_PRUEBA_EXE_N, YO2_PRUEBA_EXE_N1 y YO2_PRUEBA_SEMILLA (semilla ed25519 en hex).
func TestPaqueteExeDeVerdad(t *testing.T) {
	exeN, exeN1, sem := os.Getenv("YO2_PRUEBA_EXE_N"), os.Getenv("YO2_PRUEBA_EXE_N1"), os.Getenv("YO2_PRUEBA_SEMILLA")
	if exeN == "" || exeN1 == "" || sem == "" {
		t.Skip("prueba a mano: faltan YO2_PRUEBA_EXE_N / YO2_PRUEBA_EXE_N1 / YO2_PRUEBA_SEMILLA")
	}
	seed, err := hex.DecodeString(sem)
	if err != nil || len(seed) != ed25519.SeedSize {
		t.Fatal("YO2_PRUEBA_SEMILLA no es una semilla ed25519 en hex")
	}
	priv := ed25519.NewKeyFromSeed(seed)
	t.Cleanup(UsarClaveEditorDePrueba(priv.Public().(ed25519.PublicKey)))
	version := func(exe string) int64 {
		var v int64
		out, err := exec.Command(exe, "-version").Output()
		if err != nil {
			t.Fatalf("%s -version: %v", exe, err)
		}
		fmt.Sscan(string(out), &v)
		return v
	}
	vN, vN1 := version(exeN), version(exeN1)
	if vN <= 0 || vN1 <= vN {
		t.Fatalf("versiones %d y %d", vN, vN1)
	}
	bN, _ := os.ReadFile(exeN)
	bN1, _ := os.ReadFile(exeN1)
	semilla, pkg := pruebas.TempDir(t), pruebas.TempDir(t)
	v2 := escribirArbol(t, priv, semilla, arbolV2(), string(bN1), 2, vN1)
	escribirArbol(t, priv, pkg, arbolV1(), string(bN), 1, vN)
	// el exe instalado se llama de otra forma (como un YO2Launcher.exe de antes, o renombrado por el jugador; y así no
	// choca con un YO2OnlineLauncher.exe que esté abierto en este PC)
	exe := filepath.Join(pkg, "PruebaUPD.exe")
	os.Rename(filepath.Join(pkg, ExeLanzadorApp), exe)
	os.Rename(filepath.Join(pkg, ExeLanzadorApp+ExtFirma), exe+ExtFirma)
	inst := NuevaInstalacion(exe, t.Logf) // ComprobarExe de verdad: «<exe nuevo> -version»
	p, err := inst.Preparar(context.Background(), clientePipe(&ServidorPaquete{Semilla: semilla}), v2, nil)
	if err != nil || !p.Exe {
		t.Fatalf("preparar: %+v %v", p, err)
	}
	cmd := exec.Command(exe, "-aplicar-actualizacion")
	cmd.Env = append(os.Environ(), "YO2_LANZADOR_DATOS="+pruebas.TempDir(t), "YO2_PORT_OFFSET=5950")
	t0 := time.Now()
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("el exe N al aplicar: %v %s", err, out)
	}
	t.Logf("aplicado por el exe N en %v", time.Since(t0).Round(time.Millisecond))
	if v := version(exe); v != vN1 {
		t.Fatalf("tras aplicar, el exe es la versión %d", v)
	}
	fi, err := LeerFirma(exe + ExtFirma)
	if err != nil || fi.Version != vN1 {
		t.Fatalf("la .firma instalada: %+v %v", fi, err)
	}
	for _, f := range v2.Man.Ficheros {
		if f.Parte != ParteLanzador && !shaIgual(filepath.Join(pkg, filepath.FromSlash(f.Ruta)), f) {
			t.Fatalf("%s no es el de la versión 2", f.Ruta)
		}
	}
	if b, _ := os.ReadFile(filepath.Join(pkg, NombrePaquete)); !bytes.Equal(b, v2.Bytes) || Existe(filepath.Join(pkg, "server", "yo2emu", "viejo.py")) {
		t.Fatal("paquete.json o server/ no son los de la versión 2")
	}
	if inst.Pendiente() != nil {
		t.Fatal("sigue pendiente")
	}
}

// TestPaqueteArbolReal: el paquete de verdad (~23 000 ficheros) que deja empaquetar.py --firmar (con firmar.exe y la
// clave de PRUEBA), instalado, y una versión siguiente con un script de carta y run.py cambiados: cuánto tarda
// preparar (diferencial) y aplicar (vendor\ entera por renombre). Solo con YO2_PRUEBA_ARBOL y YO2_PRUEBA_SEMILLA.
func TestPaqueteArbolReal(t *testing.T) {
	arbol, sem := os.Getenv("YO2_PRUEBA_ARBOL"), os.Getenv("YO2_PRUEBA_SEMILLA")
	if arbol == "" || sem == "" {
		t.Skip("prueba a mano: faltan YO2_PRUEBA_ARBOL / YO2_PRUEBA_SEMILLA")
	}
	seed, _ := hex.DecodeString(sem)
	priv := ed25519.NewKeyFromSeed(seed)
	t.Cleanup(UsarClaveEditorDePrueba(priv.Public().(ed25519.PublicKey)))
	v1, err := LeerPaquete(filepath.Join(arbol, NombrePaquete))
	if err != nil {
		t.Fatal(err)
	}
	// instalada = copia (enlaces) del árbol; siguiente = otra copia con dos ficheros cambiados
	copia := func(dst string) {
		for _, f := range v1.Man.Ficheros {
			if err := enlazarOCopiar(filepath.Join(arbol, filepath.FromSlash(f.Ruta)), filepath.Join(dst, filepath.FromSlash(f.Ruta))); err != nil {
				t.Fatal(err)
			}
		}
		enlazarOCopiar(filepath.Join(arbol, NombrePaquete), filepath.Join(dst, NombrePaquete))
	}
	inst, sig := pruebas.TempDir(t), pruebas.TempDir(t)
	t0 := time.Now()
	copia(inst)
	copia(sig)
	t.Logf("copias: %v", time.Since(t0).Round(time.Millisecond))
	var lua string
	for _, f := range v1.Man.Ficheros {
		if strings.HasPrefix(f.Ruta, "vendor/CardScripts/") && strings.HasSuffix(f.Ruta, ".lua") {
			lua = f.Ruta
			break
		}
	}
	for _, r := range []string{lua, "server/yo2emu/run.py"} {
		p := filepath.Join(sig, filepath.FromSlash(r))
		b, _ := os.ReadFile(p)
		os.Remove(p) // es un enlace: no tocar el original
		os.WriteFile(p, append(b, []byte("\n-- v siguiente\n")...), 0o644)
	}
	raw, err := ManifiestoDeArbol(sig, v1.Man.Version+1, v1.Man.VersionLanzador)
	if err != nil {
		t.Fatal(err)
	}
	out, _ := FirmarManifiesto(priv, raw)
	os.Remove(filepath.Join(sig, NombrePaquete)) // es un enlace: no tocar el original
	os.WriteFile(filepath.Join(sig, NombrePaquete), out, 0o644)
	sp := &ServidorPaquete{Semilla: sig}
	c := clientePipe(sp)
	t0 = time.Now()
	pv, err := c.Manifiesto(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("manifiesto: %d bytes en %v", len(pv.Bytes), time.Since(t0).Round(time.Millisecond))
	in := NuevaInstalacion(filepath.Join(inst, ExeLanzadorApp), t.Logf)
	t0 = time.Now()
	p, err := in.Preparar(context.Background(), c, pv, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("preparar: %v (partes %v, %d bytes bajados)", time.Since(t0).Round(time.Millisecond), p.Partes, p.Bytes)
	t0 = time.Now()
	if _, err := in.Aplicar(nil); err != nil {
		t.Fatal(err)
	}
	t.Logf("aplicar: %v", time.Since(t0).Round(time.Millisecond))
	if _, v := in.Instalada(); v != v1.Man.Version+1 {
		t.Fatalf("instalada %d", v)
	}
	b, _ := os.ReadFile(filepath.Join(arbol, filepath.FromSlash(lua)))
	if bytes.Contains(b, []byte("v siguiente")) {
		t.Fatal("se tocó el árbol original")
	}
}
