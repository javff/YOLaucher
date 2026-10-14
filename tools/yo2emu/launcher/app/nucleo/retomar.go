package nucleo

// Recordar qué estaba haciendo la ventana cuando se reinicia por una autoactualización: entrar en un servidor público
// (por su peer ID) o con un código privado. Pasa al proceso nuevo por el ENTORNO (nunca por argumentos ni al log: el
// código es secreto) y el proceso nuevo lo borra de su entorno nada más leerlo, para que no lo hereden ni frida ni el juego.

import (
	"os"
	"strings"

	"github.com/libp2p/go-libp2p/core/peer"
	"yo2online/sala"
)

const (
	EnvRetomar       = "YO2_RETOMAR"        // «pub:<peer ID>» | «cod» | «dir»
	EnvRetomarDir    = "YO2_RETOMAR_DIR"    // «Join by address»: host:puerto (con el código o la clave, si los había)
	EnvRetomarCodigo = "YO2_RETOMAR_CODIGO" // el código, si EnvRetomar == «cod»
	EnvRetomarClave  = "YO2_RETOMAR_CLAVE"  // la clave derivada de la contraseña (sala.Credencial.Texto), si «pub:» la pedía
	EnvPadrePID      = "YO2_PADRE_PID"      // el proceso viejo: el nuevo espera a que se cierre antes de abrir ventana
)

// Retomar: a qué volver a entrar tras reiniciar.
type Retomar struct {
	Publico string // peer ID del servidor público
	Codigo  string // código de sala (privado)
	Clave   string // servidor público con contraseña: la clave derivada (nunca la contraseña), sala.Credencial.Texto()
	Dir     string // «Join by address» (red.go): host:puerto; con Codigo o Clave si se entró con ellos
}

// Entorno: las variables que lleva el proceso nuevo.
func (r Retomar) Entorno() []string {
	switch {
	case r.Dir != "":
		e := []string{EnvRetomar + "=dir", EnvRetomarDir + "=" + r.Dir}
		if r.Codigo != "" {
			e = append(e, EnvRetomarCodigo+"="+r.Codigo)
		}
		if r.Clave != "" {
			e = append(e, EnvRetomarClave+"="+r.Clave)
		}
		return e
	case r.Publico != "" && r.Clave != "":
		return []string{EnvRetomar + "=pub:" + r.Publico, EnvRetomarClave + "=" + r.Clave}
	case r.Publico != "":
		return []string{EnvRetomar + "=pub:" + r.Publico}
	case r.Codigo != "":
		return []string{EnvRetomar + "=cod", EnvRetomarCodigo + "=" + r.Codigo}
	}
	return nil
}

// LeerRetomar lee y BORRA lo que dejó el proceso anterior. nil = arranque normal (o datos que no valen).
func LeerRetomar(getenv func(string) string, unset func(string)) *Retomar {
	v := getenv(EnvRetomar)
	cod := getenv(EnvRetomarCodigo)
	clave := getenv(EnvRetomarClave)
	dir := getenv(EnvRetomarDir)
	unset(EnvRetomarDir)
	unset(EnvRetomar)
	unset(EnvRetomarCodigo)
	unset(EnvRetomarClave)
	switch {
	case strings.HasPrefix(v, "pub:"):
		id := strings.TrimPrefix(v, "pub:")
		if _, err := peer.Decode(id); err == nil {
			r := &Retomar{Publico: id}
			if _, err := sala.CredencialDeTexto(clave); err == nil {
				r.Clave = clave
			}
			return r
		}
	case v == "dir" && dir != "":
		d, err := sala.ParseDireccion(dir)
		if err != nil {
			return nil
		}
		r := &Retomar{Dir: d.Texto}
		if _, err := sala.Parse(cod); cod != "" && err == nil {
			r.Codigo = cod
		}
		if _, err := sala.CredencialDeTexto(clave); clave != "" && err == nil {
			r.Clave = clave
		}
		return r
	case v == "cod" && cod != "":
		if _, err := sala.Parse(cod); err == nil {
			return &Retomar{Codigo: cod}
		}
	}
	return nil
}

// leerRetomarProceso: lo mismo con el entorno real.
func leerRetomarProceso() *Retomar {
	return LeerRetomar(os.Getenv, func(k string) { os.Unsetenv(k) })
}
