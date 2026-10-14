package sala

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// 12-10: el exe del lanzador NUNCA puede llamarse como el lanzador de Konami (Yo2Launcher.exe, en la carpeta del
// cliente): Windows no distingue mayúsculas y lo sobrescribía. Esta prueba falla si algún nombre de SALIDA (la
// constante, wails.json, build.ps1 o empaquetar.py) vuelve a ser «yo2launcher.exe» sin mayúsculas.
func TestNombreExeNoChocaConKonami(t *testing.T) {
	if strings.EqualFold(ExeLanzadorApp, "yo2launcher.exe") || ChocaConKonami(ExeLanzadorApp) {
		t.Fatalf("ExeLanzadorApp = %q choca con el lanzador de Konami", ExeLanzadorApp)
	}
	if !ChocaConKonami(`C:\juego\YO2LAUNCHER.EXE`) || !ChocaConKonami("Yo2Launcher.exe") || ChocaConKonami("YO2OnlineLauncher.exe") {
		t.Fatal("ChocaConKonami no reconoce el nombre sin distinguir mayúsculas")
	}
	// La identidad firmada se queda en "YO2Launcher.exe" a propósito (los lanzadores ya repartidos solo aceptan esa).
	if NombreLanzadorApp != "YO2Launcher.exe" {
		t.Fatalf("NombreLanzadorApp = %q: si cambia, ningún lanzador viejo se podrá actualizar", NombreLanzadorApp)
	}

	var w struct {
		Output string `json:"outputfilename"`
	}
	b, err := os.ReadFile(filepath.Join("..", "app", "wails.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &w); err != nil {
		t.Fatal(err)
	}
	if !strings.EqualFold(w.Output+".exe", ExeLanzadorApp) {
		t.Fatalf("wails.json outputfilename = %q, se esperaba %q", w.Output, strings.TrimSuffix(ExeLanzadorApp, ".exe"))
	}

	exe := regexp.MustCompile(`(?i)[A-Za-z0-9_-]*\.exe\b`)
	salida := func(fichero string, quitarComentario string) {
		b, err := os.ReadFile(fichero)
		if err != nil {
			t.Fatal(err)
		}
		for i, l := range strings.Split(string(b), "\n") {
			if c := strings.Index(l, quitarComentario); c >= 0 {
				l = l[:c] // los comentarios sí pueden nombrar el de Konami (para explicar por qué no)
			}
			for _, m := range exe.FindAllString(l, -1) {
				if strings.EqualFold(m, "yo2launcher.exe") {
					t.Errorf("%s:%d: nombre de salida %q choca con el lanzador de Konami", fichero, i+1, m)
				}
			}
		}
	}
	salida(filepath.Join("..", "app", "build.ps1"), "#")
	// empaquetar.py: el exe del paquete (EXE_LANZADOR) y la carpeta raíz del zip (RAIZ_ZIP)
	b, err = os.ReadFile(filepath.Join("..", "..", "paquete", "empaquetar.py"))
	if os.IsNotExist(err) {
		t.Skip("sin paquete/empaquetar.py (herramienta de publicación del editor: no va en el árbol público)")
	}
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), `EXE_LANZADOR = "`+ExeLanzadorApp+`"`) {
		t.Fatalf("empaquetar.py no usa EXE_LANZADOR = %q", ExeLanzadorApp)
	}
	if m := regexp.MustCompile(`(?m)^RAIZ_ZIP = "([^"]+)"`).FindSubmatch(b); m == nil || strings.EqualFold(string(m[1])+".exe", "yo2launcher.exe") {
		t.Fatalf("empaquetar.py: RAIZ_ZIP %q", m)
	}
}
