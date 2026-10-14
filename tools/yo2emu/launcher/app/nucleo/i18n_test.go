package nucleo

// Pruebas de los textos de la ventana (solo inglés: frontend/dist/i18n/en.json):
//   - todas las claves «msg.*» que emite Go (sala, juego y nucleo, buscadas en el código con go/ast) están en la tabla, y
//     cada {parámetro} del texto lo da algún sitio que emite esa clave;
//   - todas las claves que usa la página (app.js, i18n.js, index.html) existen, y no sobra ninguna;
//   - ningún texto de la tabla ni novedad lleva letras o signos del español (la interfaz es solo en inglés).

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"yo2online/internal/pruebas"
)

const dirFrontend = "../frontend/dist"

func cargarTabla(t *testing.T) map[string]string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(dirFrontend, "i18n", "en.json"))
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]string
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("en.json no es un objeto JSON de textos: %v", err)
	}
	return m
}

var reParam = regexp.MustCompile(`\{(\w+)\}`)

func parametros(s string) []string {
	var r []string
	seen := map[string]bool{}
	for _, m := range reParam.FindAllStringSubmatch(s, -1) {
		if !seen[m[1]] {
			seen[m[1]] = true
			r = append(r, m[1])
		}
	}
	return r
}

// existe: la clave está tal cual o en sus formas de plural (_one / _other).
func existe(tabla map[string]string, k string) bool {
	for _, c := range []string{k, k + "_one", k + "_other"} {
		if _, ok := tabla[c]; ok {
			return true
		}
	}
	return false
}

// Un texto en inglés no lleva ñ, vocales con tilde, ¿ ni ¡ (los nombres de fichero y rutas no cuentan).
var reEspanol = regexp.MustCompile(`[áéíóúñÁÉÍÓÚÑ¿¡]`)

func TestTablaSoloIngles(t *testing.T) {
	en := cargarTabla(t)
	for k, v := range en {
		if strings.TrimSpace(v) == "" {
			t.Errorf("%s: texto vacío", k)
		}
		if reEspanol.MatchString(v) {
			t.Errorf("%s: parece español: %q", k, v)
		}
	}
	b, err := os.ReadFile(filepath.Join(dirFrontend, "novedades.json"))
	if err != nil {
		t.Fatal(err)
	}
	var nov []struct {
		Fecha  string   `json:"fecha"`
		Titulo string   `json:"titulo"`
		Puntos []string `json:"puntos"`
	}
	if err := json.Unmarshal(b, &nov); err != nil {
		t.Fatal(err)
	}
	if len(nov) == 0 {
		t.Fatal("sin novedades")
	}
	for i, n := range nov {
		if !regexp.MustCompile(`^\d{4}-\d\d-\d\d$`).MatchString(n.Fecha) {
			t.Errorf("novedad %d: fecha %q (AAAA-MM-DD)", i, n.Fecha)
		}
		if strings.TrimSpace(n.Titulo) == "" || len(n.Puntos) == 0 {
			t.Errorf("novedad %d: sin título o sin puntos", i)
		}
		for _, s := range append([]string{n.Titulo}, n.Puntos...) {
			if reEspanol.MatchString(s) {
				t.Errorf("novedad %d: parece español: %q", i, s)
			}
		}
	}
	if _, err := os.Stat(filepath.Join(dirFrontend, "i18n", "es.json")); err == nil {
		t.Error("sobra i18n/es.json: la interfaz es solo en inglés")
	}
}

// clavesDeGo: cada sitio del código que crea un mensaje con clave (sala.M, M, errM, errMc) y sus parámetros.
type usoClave struct {
	donde  string
	params map[string]bool
}

func clavesDeGo(t *testing.T) map[string][]usoClave {
	t.Helper()
	reClave := regexp.MustCompile(`^msg\.[a-z0-9_]+(\.[a-z0-9_]+)*$`)
	usos := map[string][]usoClave{}
	for _, dir := range []string{".", "..", "../../sala", "../../juego"} {
		fset := token.NewFileSet()
		pkgs, err := parser.ParseDir(fset, dir, func(fi os.FileInfo) bool { return !strings.HasSuffix(fi.Name(), "_test.go") }, 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, pkg := range pkgs {
			for nombre, f := range pkg.Files {
				ast.Inspect(f, func(n ast.Node) bool {
					// 1) llamadas M(clave, es, k1, v1, ...) / errM(clave, es, ...) / errMc(causa, clave, es, ...): con parámetros
					if c, ok := n.(*ast.CallExpr); ok {
						var nom string
						switch fn := c.Fun.(type) {
						case *ast.Ident:
							nom = fn.Name
						case *ast.SelectorExpr:
							nom = fn.Sel.Name
						}
						idx := -1
						switch nom {
						case "M", "errM":
							idx = 0
						case "errMc":
							idx = 1
						}
						if idx >= 0 && len(c.Args) > idx+1 {
							if l, ok := c.Args[idx].(*ast.BasicLit); ok && l.Kind == token.STRING {
								k, _ := strconv.Unquote(l.Value)
								if reClave.MatchString(k) {
									u := usoClave{donde: nombre, params: map[string]bool{}}
									for i := idx + 2; i+1 < len(c.Args); i += 2 {
										if p, ok := c.Args[i].(*ast.BasicLit); ok && p.Kind == token.STRING {
											pn, _ := strconv.Unquote(p.Value)
											u.params[pn] = true
										}
									}
									usos[k] = append(usos[k], u)
									return true
								}
							}
						}
					}
					// 2) cualquier otro literal con pinta de clave (p. ej. una variable que luego se pasa a M)
					if l, ok := n.(*ast.BasicLit); ok && l.Kind == token.STRING {
						k, _ := strconv.Unquote(l.Value)
						if reClave.MatchString(k) {
							if _, hay := usos[k]; !hay {
								usos[k] = nil
							}
						}
					}
					return true
				})
			}
		}
	}
	return usos
}

func TestClavesDeGoTraducidas(t *testing.T) {
	en := cargarTabla(t)
	usos := clavesDeGo(t)
	if len(usos) < 100 {
		t.Fatalf("solo se encontraron %d claves en el código: ¿cambió la forma de crearlas?", len(usos))
	}
	// una clave puede salir de varios sitios (con los mismos parámetros) o de una variable: el conjunto es la unión
	for k, us := range usos {
		if !existe(en, k) {
			t.Errorf("clave de Go sin texto en en.json: %s", k)
			continue
		}
		if len(us) == 0 {
			continue
		}
		union := map[string]bool{}
		for _, u := range us {
			for p := range u.params {
				union[p] = true
			}
		}
		for _, c := range []string{k, k + "_one", k + "_other"} {
			for _, p := range parametros(en[c]) {
				if !union[p] {
					t.Errorf("%s: el texto usa {%s} pero ningún sitio de Go lo da", c, p)
				}
			}
		}
	}
}

func TestClavesDeLaPaginaExisten(t *testing.T) {
	en := cargarTabla(t)
	// literales de texto con pinta de clave: 'algo.otro_algo'
	reLit := regexp.MustCompile(`['"]((?:app|common|st|server|lang|rules|room|play|tag|ping|det|bar|ov|code|pw|conf|busy|host|srv|toast|news|set|time|msg|mg|net)\.[a-z0-9_.]*[a-z0-9_])['"]`)
	usadas := map[string]bool{}
	for _, f := range []string{"app.js", "i18n.js", "index.html", "red.js"} {
		b, err := os.ReadFile(filepath.Join(dirFrontend, f))
		if err != nil {
			t.Fatal(err)
		}
		for _, m := range reLit.FindAllStringSubmatch(string(b), -1) {
			if strings.HasSuffix(m[1], ".js") || strings.HasSuffix(m[1], ".css") || strings.HasSuffix(m[1], ".json") { // ficheros, no claves
				continue
			}
			usadas[m[1]] = true
		}
	}
	// claves que la página arma con un prefijo: 'rules.' + vanilla|mejorado
	for _, k := range []string{"rules.vanilla", "rules.mejorado"} {
		usadas[k] = true
	}
	if len(usadas) < 80 {
		t.Fatalf("solo se encontraron %d claves en la página", len(usadas))
	}
	for k := range usadas {
		if !existe(en, k) {
			t.Errorf("la página usa %s y falta en en.json", k)
		}
	}
	// lo contrario: claves en la tabla que nadie usa (las de Go se cuentan por el código)
	usos := clavesDeGo(t)
	for k := range en {
		base := strings.TrimSuffix(strings.TrimSuffix(k, "_one"), "_other")
		if _, enGo := usos[base]; !usadas[k] && !usadas[base] && !enGo {
			t.Errorf("clave sin usar en en.json: %s", k)
		}
	}
}

// La interfaz no guarda ni acepta ningún ajuste de idioma: el fichero de ajustes de antes (con o sin «idioma_ui») se lee igual.
func TestAjustesSinIdioma(t *testing.T) {
	d := pruebas.TempDir(t)
	os.WriteFile(filepath.Join(d, ficheroAjustes), []byte(`{"v":1,"actualizar":false,"idioma_ui":"es"}`), 0o600)
	if a := abrirAlmacen(d).leer(); a.Actualizar {
		t.Fatalf("no se leyó el fichero: %+v", a)
	}
	b, _ := json.Marshal(AjustesPorDefecto())
	if strings.Contains(string(b), "idioma_ui") {
		t.Fatalf("los ajustes siguen llevando un idioma de interfaz: %s", b)
	}
}

// 12-10: el servidor ya no tiene idioma en la interfaz. El formulario no lo guarda (uno de antes con «idioma» se lee igual)
// y la ficha firmada sigue llevando el campo, con "multi", para los lanzadores viejos.
func TestServidorSinIdioma(t *testing.T) {
	b, _ := json.Marshal(FormPorDefecto())
	if strings.Contains(string(b), "idioma") {
		t.Fatalf("el formulario sigue llevando idioma: %s", b)
	}
	var f FormServidor
	if err := json.Unmarshal([]byte(`{"nombre":"x","max":8,"idioma":"fr","reglas":"vanilla"}`), &f); err != nil || f.Nombre != "x" {
		t.Fatalf("formulario de antes con idioma: %v %+v", err, f)
	}
	if o := f.Normalizado().OpcServidor(0); o.Idioma != "multi" {
		t.Fatalf("idioma de la ficha: %q", o.Idioma)
	}
}
