package nucleo

// Ajustes de la ventana: se guardan en <datos>\lanzador.json (solo en este equipo). Incluyen «My servers» (los servidores
// guardados, misservidores.go) y los servidores de la lista que el usuario ha ocultado (por peer ID, nunca por nombre).
//
// Formato (campo «v»): 1 = hasta el 12-10 (un solo formulario «servidor» y su «clave_servidor»); 2 = «My servers»
// («servidores»: lista). Un fichero v1 se migra solo al abrirlo: su servidor pasa a ser el primero de la lista CON su
// identidad y sus datos de siempre (Legado) y el fichero viejo queda en lanzador.json.v1.bak.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"sync"

	"github.com/libp2p/go-libp2p/core/peer"
)

const (
	ficheroAjustes = "lanzador.json"
	formatoAjustes = 2
)

// Ajustes: lo que ve la pestaña Ajustes y «My servers».
type Ajustes struct {
	V              int      `json:"v"`
	CarpetaJuego   string   `json:"carpeta_juego,omitempty"` // "" = la que se detecta
	Actualizar     bool     `json:"actualizar"`              // autoactualización del lanzador por el túnel
	MostrarOcultos bool     `json:"mostrar_ocultos"`
	Tema           string   `json:"tema,omitempty"`    // "" o "light" = claro (por defecto); "system" = el de Windows; "dark"
	Ocultos        []string `json:"ocultos,omitempty"` // peer IDs
	// Servidores: «My servers» (formato 2). Nunca va a la página así (lleva las claves): ver MisServidores.
	Servidores []ServidorGuardado `json:"servidores"`
	// Formato 1: solo se leen para migrar (y nunca se vuelven a escribir).
	ViejoForm  *FormServidor  `json:"servidor,omitempty"`
	ViejaClave *ClaveGuardada `json:"clave_servidor,omitempty"`
	// Red: Ajustes → Network (puerto fijo del servidor propio; red.go). Del PC, no del servidor guardado. Se conserva
	// tal cual al migrar del formato 1 (el JSON v1 ya lo traía en «red»).
	Red AjustesRed `json:"red"`
}

// AjustesPorDefecto: primera vez.
func AjustesPorDefecto() Ajustes {
	return Ajustes{V: formatoAjustes, Actualizar: true}
}

// almacen: Ajustes protegido y guardado en disco.
type almacen struct {
	mu   sync.Mutex
	ruta string
	a    Ajustes
}

// abrirAlmacen lee <datos>\lanzador.json (si no existe o está roto, parte de los valores por defecto) y migra el formato 1.
// Sin fichero (o roto) pero con rastro de un servidor de antes (la identidad de OpenServer, el estado del paquete), ese
// servidor entra también en la lista: nunca se pierde un mundo por no tener lanzador.json.
func abrirAlmacen(datos string) *almacen {
	al := &almacen{ruta: filepath.Join(datos, ficheroAjustes), a: AjustesPorDefecto()}
	b, err := os.ReadFile(al.ruta)
	var a Ajustes
	a.Actualizar = true
	if err == nil && json.Unmarshal(b, &a) == nil && (a.V == 1 || a.V == formatoAjustes) {
		if a.V == 1 {
			if sg, ok := servidorDeAntes(a.ViejoForm, a.ViejaClave, datos); ok {
				a.Servidores = []ServidorGuardado{sg}
			} else {
				a.Servidores = nil
			}
			a.V = formatoAjustes
			a.ViejoForm, a.ViejaClave = nil, nil
			copia := al.ruta + ".v1.bak"
			if _, e := os.Stat(copia); e != nil {
				os.WriteFile(copia, b, 0o600) // por si hay que volver a un lanzador de antes
			}
			al.a = a
			al.guardarLocked()
			return al
		}
		a.ViejoForm, a.ViejaClave = nil, nil
		a.Servidores = limpiarServidores(a.Servidores)
		al.a = a
		return al
	}
	if sg, ok := servidorDeAntes(nil, nil, datos); ok {
		al.a.Servidores = []ServidorGuardado{sg}
		al.guardarLocked()
	}
	return al
}

func (al *almacen) guardarLocked() error {
	al.a.V = formatoAjustes
	b, err := json.MarshalIndent(al.a, "", " ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(al.ruta), 0o700); err != nil {
		return err
	}
	tmp := al.ruta + ".tmp"
	if err := os.WriteFile(tmp, b, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, al.ruta)
}

// leer devuelve una copia.
func (al *almacen) leer() Ajustes {
	al.mu.Lock()
	defer al.mu.Unlock()
	a := al.a
	a.Ocultos = append([]string(nil), al.a.Ocultos...)
	a.Servidores = make([]ServidorGuardado, len(al.a.Servidores))
	for i, s := range al.a.Servidores {
		if s.Clave != nil {
			c := *s.Clave
			s.Clave = &c
		}
		a.Servidores[i] = s
	}
	return a
}

// paraPagina: lo que se manda a la ventana (sin los servidores guardados: llevan sus claves; la página usa MisServidores).
func (a Ajustes) paraPagina() Ajustes {
	a.Servidores, a.ViejoForm, a.ViejaClave = nil, nil, nil
	return a
}

// cambiar aplica f bajo el cerrojo y guarda.
func (al *almacen) cambiar(f func(*Ajustes)) error {
	al.mu.Lock()
	defer al.mu.Unlock()
	f(&al.a)
	return al.guardarLocked()
}

// TemaValido: los tres valores que acepta la ventana.
func TemaValido(t string) bool { return t == "system" || t == "light" || t == "dark" }

// EsOculto: ¿ese peer ID está oculto?
func (a Ajustes) EsOculto(id string) bool {
	for _, o := range a.Ocultos {
		if o == id {
			return true
		}
	}
	return false
}

// ponerOculto añade o quita un peer ID (solo IDs válidos: lo que llega de la ventana no se guarda sin mirar).
func (al *almacen) ponerOculto(id string, si bool) error {
	if _, err := peer.Decode(id); err != nil {
		return errM("msg.err.id_invalido", "identificador de servidor no válido")
	}
	return al.cambiar(func(a *Ajustes) {
		set := map[string]bool{}
		for _, o := range a.Ocultos {
			set[o] = true
		}
		if si {
			set[id] = true
		} else {
			delete(set, id)
		}
		a.Ocultos = a.Ocultos[:0]
		for o := range set {
			a.Ocultos = append(a.Ocultos, o)
		}
		sort.Strings(a.Ocultos)
	})
}
