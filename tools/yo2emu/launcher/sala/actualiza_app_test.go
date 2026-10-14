package sala

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
	"yo2online/internal/pruebas"
)

// El lanzador con ventana se reparte aparte del JoinServer y las firmas no se pueden cambiar de uno a otro.

func TestFirmaLlevaElNombreDelExe(t *testing.T) {
	f := &FirmaLanzador{V: FirmaVersionV, Nombre: NombreLanzadorApp, Version: 20261011000000, SHA256: strings.Repeat("a", 64), Tam: 10, Firma: "AAAA"}
	// Aunque la firma fuese buena, una firma de YO2Launcher.exe no vale para JoinServer.exe ni al revés.
	if err := f.Verificar(); err == nil || !strings.Contains(err.Error(), "campos no válidos") {
		t.Fatalf("Verificar (JoinServer) aceptó la firma de YO2Launcher.exe: %v", err)
	}
	f.Nombre = NombreLanzador
	if err := f.VerificarPara(NombreLanzadorApp); err == nil || !strings.Contains(err.Error(), "campos no válidos") {
		t.Fatalf("VerificarPara(YO2Launcher.exe) aceptó la firma de JoinServer.exe: %v", err)
	}
	if err := f.VerificarPara("cmd.exe"); err == nil {
		t.Fatal("VerificarPara aceptó un nombre desconocido")
	}
	// Con el nombre bueno llega a comprobar la firma criptográfica (que aquí es basura) y la rechaza.
	if err := f.VerificarPara(NombreLanzador); err == nil || !strings.Contains(err.Error(), "NO es la nuestra") {
		t.Fatalf("firma basura aceptada: %v", err)
	}
}

func TestCatalogoNoOfreceLanzadorAppSinFirma(t *testing.T) {
	dir := pruebas.TempDir(t)
	exe := filepath.Join(dir, NombreLanzadorApp)
	os.WriteFile(exe, []byte("exe de mentira"), 0o755)
	old := time.Now().Add(-time.Minute)
	os.Chtimes(exe, old, old) // ya lleva rato quieto
	c := &Catalogo{LanzadorApp: exe, Logf: func(string, ...any) {}}
	m := c.Manifiesto()
	if m.LanzadorApp() != nil || m.Lanzador() != nil {
		t.Fatal("se ofrece un exe sin firma")
	}
	if !strings.Contains(c.ProblemaLanzadorApp, "falta la firma") || c.ProblemaLanzador != "" {
		t.Fatalf("problemas: %q / %q", c.ProblemaLanzador, c.ProblemaLanzadorApp)
	}
	// Una firma de JoinServer.exe junto al YO2Launcher.exe tampoco vale.
	os.WriteFile(exe+ExtFirma, []byte(`{"v":1,"nombre":"JoinServer.exe","version":1,"sha256":"`+strings.Repeat("b", 64)+`","tam":14,"firma":"AAAA"}`), 0o644)
	c.Manifiesto()
	if !strings.Contains(c.ProblemaLanzadorApp, "no es válida") {
		t.Fatalf("firma de otro exe aceptada: %q", c.ProblemaLanzadorApp)
	}
}
