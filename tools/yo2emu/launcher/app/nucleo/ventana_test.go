package nucleo

// PAN (24-10): Ajustes → Display → «Window size». Apagado por defecto; solo se guardan los valores válidos.

import "testing"

func TestVentanaAjuste(t *testing.T) {
	n, datos := nuevoDePrueba(t)
	if v := n.Ajustes().Ventana; v != "" {
		t.Fatalf("por defecto debe ser Original (vacío), hay %q", v)
	}
	for _, v := range []string{"1.5", "2", "fit", ""} {
		if err := n.PonerVentana(v); err != nil {
			t.Fatalf("PonerVentana(%q): %v", v, err)
		}
		if got := n.Ajustes().Ventana; got != v {
			t.Fatalf("tras PonerVentana(%q) hay %q", v, got)
		}
	}
	if err := n.PonerVentana("2x"); err != nil || n.Ajustes().Ventana != "2" { // se guarda la forma canónica
		t.Fatalf("2x: %v, %q", err, n.Ajustes().Ventana)
	}
	for _, malo := range []string{"3", "fit;calc", "../x", "1,5"} {
		if err := n.PonerVentana(malo); codigoDe(err) != "msg.err.ventana_invalida" {
			t.Fatalf("PonerVentana(%q) debía rechazarse: %v", malo, err)
		}
		if n.Ajustes().Ventana != "2" {
			t.Fatalf("un valor malo no debe cambiar el ajuste: %q", n.Ajustes().Ventana)
		}
	}
	// persiste en disco
	if a := abrirAlmacen(datos).leer(); a.Ventana != "2" {
		t.Fatalf("en disco: %q", a.Ventana)
	}
	// Original no escribe nada en lanzador.json (omitempty): un fichero de antes y uno de ahora son iguales
	if err := n.PonerVentana(""); err != nil {
		t.Fatal(err)
	}
	if a := abrirAlmacen(datos).leer(); a.Ventana != "" {
		t.Fatalf("Original en disco: %q", a.Ventana)
	}
}
