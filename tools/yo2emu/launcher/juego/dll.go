package juego

// Gancho por DLL propia (rama rx/DLL, 12-10): en vez de frida-inject + redirect_hook.js, el lanzador deja una
// version.dll propia (dll/yo2net.c, compilada con dll/build_dll.ps1 en dllbin/yo2net-version.dll) junto a yo2.exe y abre
// yo2.exe directamente con YO2ONLINE_NET=1. Sin esa variable la DLL solo reenvía a la version.dll de Windows, así que se
// puede quedar en la carpeta: abrir el juego sin el lanzador (o con el mod offline) sigue igual.
//
// Frida sigue de respaldo hasta que el owner confirme la DLL:
//   - modo diagnóstico (diag_hook.js solo existe para frida)
//   - YO2_GANCHO=frida en el entorno del lanzador, o yo2-frida.txt junto al lanzador o junto a yo2.exe
//   - el lanzador se compiló sin la DLL (dllbin/ sin yo2net-version.dll)
//   - en la carpeta del juego hay una version.dll que no es nuestra (no se pisa nunca) o no se puede escribir
//   - la DLL falló la última vez (no apuntó «ganchos OK» en su registro): <datos>\gancho-dll-fallo.txt guarda el sha256
//     de la DLL que falló; con otra DLL (lanzador nuevo) se vuelve a probar. Borrar ese fichero = volver a probar ya.
// YO2_GANCHO=dll fuerza la DLL aunque haya fallado antes (sigue sin pisar una version.dll ajena).

import (
	"bytes"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

//go:embed dllbin
var dllFS embed.FS

const (
	// NombreDLL: cómo se llama en la carpeta del juego (yo2.exe la importa y Windows la busca ahí antes que en System32).
	NombreDLL = "version.dll"
	// MarcaDLL: cadena que lleva nuestra DLL (MARCA en yo2net.c). Una version.dll sin ella es de otro: no se toca.
	MarcaDLL = "YO2ONLINE-NETDLL"
	// FicheroDLLFallo: en la carpeta de datos; sha256 de la DLL que no llegó a enganchar.
	FicheroDLLFallo = "gancho-dll-fallo.txt"
	// FicheroLogDLL: en la carpeta de datos; lo escribe la DLL (YO2ONLINE_NET_LOG) y se copia al log al cerrar el juego.
	FicheroLogDLL = "yo2net.log"
	// LineaOKDLL: la DLL la escribe cuando los tres ganchos de red están puestos.
	LineaOKDLL = "ganchos OK"

	GanchoFrida = "frida"
	GanchoDLL   = "dll"
)

// DLLIncrustada: la DLL compilada que lleva este lanzador (nil si se compiló sin ella). Variable para las pruebas.
var DLLIncrustada = func() []byte {
	b, err := dllFS.ReadFile("dllbin/yo2net-version.dll")
	if err != nil || !bytes.Contains(b, []byte(MarcaDLL)) {
		return nil
	}
	return b
}

// ErrDLLAjena: en la carpeta del juego ya hay una version.dll que no es la nuestra.
var ErrDLLAjena = errors.New("ya hay una version.dll que no es del lanzador")

// ColocarDLL deja dll como version.dll en la carpeta del juego. Si ya está igual, no hace nada; si hay una nuestra de
// otra versión, la cambia; si hay una ajena, no la toca (ErrDLLAjena). Si no se puede cambiar una nuestra (p. ej. otro
// yo2.exe la tiene abierta) se sigue con la que hay: también es nuestra.
func ColocarDLL(carpeta string, dll []byte) (string, error) {
	dst := filepath.Join(carpeta, NombreDLL)
	if old, err := os.ReadFile(dst); err == nil {
		if bytes.Equal(old, dll) {
			return "ya estaba", nil
		}
		if !bytes.Contains(old, []byte(MarcaDLL)) {
			return "", ErrDLLAjena
		}
		if err := escribirAtomico(dst, dll); err != nil {
			return "la de antes (no se pudo cambiar: " + err.Error() + ")", nil
		}
		return "actualizada", nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", err
	}
	if err := escribirAtomico(dst, dll); err != nil {
		return "", err
	}
	return "copiada", nil
}

func escribirAtomico(dst string, b []byte) error {
	tmp := dst + ".yo2online-nueva"
	if err := os.WriteFile(tmp, b, 0o644); err != nil {
		os.Remove(tmp)
		return err
	}
	if err := os.Rename(tmp, dst); err != nil {
		os.Remove(tmp)
		return err
	}
	return nil
}

func shaHex(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }

// dllFalloAntes: ¿esta misma DLL ya falló en este PC?
func dllFalloAntes(datos string, dll []byte) bool {
	b, err := os.ReadFile(filepath.Join(datos, FicheroDLLFallo))
	return err == nil && strings.TrimSpace(string(b)) == shaHex(dll)
}

func marcarFalloDLL(datos string, dll []byte) error {
	return os.WriteFile(filepath.Join(datos, FicheroDLLFallo), []byte(shaHex(dll)+"\n"), 0o600)
}

// existeJuntoA: ¿hay un fichero con ese nombre junto al lanzador o en la carpeta del juego?
func existeJuntoA(carpetaJuego, nombre string) bool {
	dirs := []string{carpetaJuego}
	if exe, err := os.Executable(); err == nil {
		dirs = append(dirs, filepath.Dir(exe))
	}
	for _, d := range dirs {
		if d != "" && Existe(filepath.Join(d, nombre)) {
			return true
		}
	}
	return false
}

// ElegirGancho: frida o dll para abrir el juego, y por qué (va al log). No toca la carpeta del juego.
func (l *Lanzador) ElegirGancho(carpeta string, diag bool) (modo, motivo string) {
	env := strings.ToLower(strings.TrimSpace(os.Getenv("YO2_GANCHO")))
	dll := DLLIncrustada()
	switch {
	case diag:
		return GanchoFrida, "modo diagnóstico (diag_hook.js es de frida)"
	case env == GanchoFrida:
		return GanchoFrida, "YO2_GANCHO=frida"
	case dll == nil:
		return GanchoFrida, "este lanzador no lleva la DLL"
	case env == GanchoDLL:
		return GanchoDLL, "YO2_GANCHO=dll"
	case existeJuntoA(carpeta, "yo2-frida.txt"):
		return GanchoFrida, "existe yo2-frida.txt"
	case dllFalloAntes(l.Datos, dll):
		return GanchoFrida, fmt.Sprintf("la DLL falló la última vez (%s; bórralo para volver a probarla)",
			filepath.Join(l.Datos, FicheroDLLFallo))
	}
	return GanchoDLL, "por defecto"
}

// volcarLogDLL copia lo que apuntó la DLL al log del lanzador y dice si llegó a poner los ganchos.
func (l *Lanzador) volcarLogDLL(ruta string) (ok, hay bool) {
	b, err := os.ReadFile(ruta)
	if err != nil {
		return false, false
	}
	for _, ln := range strings.Split(strings.TrimRight(string(b), "\r\n"), "\n") {
		ln = strings.TrimRight(ln, "\r")
		if l.Log != nil {
			fmt.Fprintln(l.Log, ln)
		}
		if strings.HasSuffix(ln, "[yo2net] "+LineaOKDLL) {
			ok = true
		}
	}
	return ok, true
}
