package juego

// Tamaño de la ventana del juego (rama rx/PAN, 24-10): «Window size» en Ajustes → Display. Es una mejora NUESTRA (no 1:1):
// el cliente de 2008 fija la interfaz a 1024x768 y los jugadores lo ven pequeño en monitores modernos. La DLL propia
// (dll/yo2net.c, «ventana ampliada») agranda la ventana real, estira el backbuffer y traduce el ratón; el lanzador solo le
// pasa el valor en YO2ONLINE_WINDOW. APAGADO por defecto: "" = «Original (1024x768)», el juego tal cual.
//
// Solo funciona con el gancho por DLL: con frida de respaldo (YO2_GANCHO=frida, DLL que falló…) se ignora y se apunta en el log.

import "strings"

// Valores que se guardan en lanzador.json («ventana») y se pasan a la DLL.
const (
	VentanaOriginal = ""    // el juego tal cual (1024x768)
	VentanaX15      = "1.5" // ventana 1.5x (1536x1152 de área cliente, si cabe)
	VentanaX2       = "2"   // ventana 2x (2048x1536, si cabe)
	VentanaAjustar  = "fit" // sin bordes, a todo el monitor, 4:3 con bandas negras
)

// EnvVentana: la variable de entorno que lee la DLL.
const EnvVentana = "YO2ONLINE_WINDOW"

// NormalizarVentana devuelve el valor canónico («», «1.5», «2» o «fit») de lo que llegue (la ventana, el disco…) y si era
// válido. «orig»/«original»/«0» valen como «Original».
func NormalizarVentana(s string) (string, bool) {
	switch strings.ToLower(strings.TrimSpace(s)) {
	case "", "orig", "original", "0":
		return VentanaOriginal, true
	case "1.5", "1.5x":
		return VentanaX15, true
	case "2", "2x":
		return VentanaX2, true
	case "fit":
		return VentanaAjustar, true
	}
	return VentanaOriginal, false
}

// entornoVentana: las variables extra para el juego (vacío con «Original»: la DLL no engancha nada).
func entornoVentana(v string) []string {
	n, ok := NormalizarVentana(v)
	if !ok || n == VentanaOriginal {
		return nil
	}
	return []string{EnvVentana + "=" + n}
}
