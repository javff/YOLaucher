package sala

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAliasPythonTienda(t *testing.T) {
	if !aliasPythonTienda(`C:\Users\x\AppData\Local\Microsoft\WindowsApps\python3.exe`) {
		t.Fatal("el python3 de WindowsApps es el alias de la tienda")
	}
	if aliasPythonTienda(`C:\Python312\python.exe`) {
		t.Fatal("un python de verdad no es alias de la tienda")
	}
}

func TestBuscarPython(t *testing.T) {
	p := buscarPython()
	if p == "" {
		t.Skip("no hay Python en este PC")
	}
	mayor, menor, ok := versionPython(p)
	if !ok {
		t.Fatalf("no pude leer la versión de %s", p)
	}
	if _, err := os.Stat("/opt/homebrew/bin/python3"); err == nil && (mayor < 3 || (mayor == 3 && menor < 12)) {
		t.Fatalf("eligió %s (%d.%d) aunque Homebrew tiene uno más nuevo", p, mayor, menor)
	}
}

func TestBuscarNode(t *testing.T) {
	p := NodeExe()
	if p == "" {
		t.Skip("no hay Node en este PC")
	}
	mayor, _, ok := versionNode(p)
	if !ok {
		t.Fatalf("no pude leer la versión de %s", p)
	}
	if mayor < 18 {
		t.Fatalf("eligió %s (v%d) que es < 18", p, mayor)
	}
}

func TestCompletarEntornoNodeNoPisaYO2NODE(t *testing.T) {
	env := []string{"PATH=/usr/bin", "YO2_NODE=/pkg/node/node.exe"}
	got := completarEntornoNode(env)
	if envValor(got, "YO2_NODE") != "/pkg/node/node.exe" {
		t.Fatalf("pisó YO2_NODE: %q", envValor(got, "YO2_NODE"))
	}
	dir := filepath.Dir("/pkg/node/node.exe")
	path := envValor(got, "PATH")
	if !strings.HasPrefix(path, dir+string(os.PathListSeparator)) {
		t.Fatalf("PATH no pone el directorio de node delante: %q", path)
	}
}

func TestCompletarEntornoNodePoneYO2NODESiFalta(t *testing.T) {
	if NodeExe() == "" {
		t.Skip("no hay Node en este PC")
	}
	got := completarEntornoNode([]string{"PATH=/usr/bin", "YO2_PERFIL=vanilla"})
	if envValor(got, "YO2_NODE") != NodeExe() {
		t.Fatalf("YO2_NODE = %q, NodeExe = %q", envValor(got, "YO2_NODE"), NodeExe())
	}
}
