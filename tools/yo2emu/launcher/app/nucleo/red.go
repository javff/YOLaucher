package nucleo

// Red para que el proyecto dure años (NET, 12-10; launcher/NOTES-red.md §13):
//   - Ajustes → Network: «Fixed port» (puerto fijo TCP+UDP del servidor propio, para abrirlo en el router);
//   - «Join by address»: entrar directo a IP:puerto (o nombre:puerto) con el código o la contraseña, sin DHT;
//   - nodos de arranque: la unión de la lista firmada por el editor más nueva (paquete / guardada), la del usuario
//     (<datos>\red-nodos.txt) y la de fábrica (sala/red_nodos.go).
// Todo lo de aquí es ajuste del PC (lanzador.json → «red»), no del formulario del servidor.

import (
	"errors"
	"path/filepath"
	"strconv"
	"strings"
	"unicode/utf8"

	"yo2online/sala"
)

// AjustesRed: Ajustes → Network.
type AjustesRed struct {
	PuertoFijo bool `json:"puerto_fijo"`
	Puerto     int  `json:"puerto,omitempty"` // 0 = el por defecto (sala.PuertoFijoPorDefecto)
}

// PuertoEfectivo: el puerto fijo que se pide al arrancar el servidor (0 = aleatorio).
func (r AjustesRed) PuertoEfectivo() int {
	if !r.PuertoFijo {
		return 0
	}
	if r.Puerto == 0 {
		return sala.PuertoFijoPorDefecto
	}
	return r.Puerto
}

// iniciarRed: lo llama Nuevo. Aplica el puerto fijo guardado y, con la red de verdad (produccion), la lista de nodos.
func (n *Nucleo) iniciarRed(produccion bool) {
	n.l.PonerPuertoFijo(n.aj.leer().Red.PuertoEfectivo())
	if !produccion {
		return
	}
	r := sala.CalcularRed(n.cfg.Datos, n.listasFirmadas(), n.cfg.Logf)
	n.l.Red.Arranque, n.l.Red.Relays = r.Arranque, r.Relays
	n.mu.Lock()
	n.red = r
	n.mu.Unlock()
}

// listasFirmadas: las listas de nodos que traen los manifiestos firmados que tiene este PC (paquete instalado y semilla).
func (n *Nucleo) listasFirmadas() []sala.FuenteRed {
	var r []sala.FuenteRed
	if n.paq != nil && n.paq.inst != nil {
		if pv, _ := n.paq.inst.Instalada(); pv != nil && pv.Man.Red != nil {
			r = append(r, sala.FuenteRed{Lista: pv.Man.Red, Origen: sala.OrigenFirmadaPaq})
		}
	}
	if n.paq != nil && n.paq.semillaD != "" {
		if pv, err := sala.LeerPaquete(filepath.Join(n.paq.semillaD, sala.NombrePaquete)); err == nil && pv.Man.Red != nil {
			r = append(r, sala.FuenteRed{Lista: pv.Man.Red, Origen: sala.OrigenFirmadaPaq})
		}
	}
	return r
}

// verRedFirmada: un manifiesto firmado recién bajado (paquete_act.go). Si trae una lista de nodos más nueva, se guarda
// ya (vale para el próximo arranque aunque el paquete no se llegue a instalar).
func (n *Nucleo) verRedFirmada(pv *sala.PaqueteVerificado) {
	if pv == nil || pv.Man.Red == nil {
		return
	}
	if ok, err := sala.GuardarRedFirmada(n.cfg.Datos, pv.Man.Red); err != nil {
		n.logf("[red] lista de nodos del paquete %d no guardada: %v", pv.Man.Version, err)
	} else if ok {
		n.logf("[red] lista de nodos firmada %d guardada (se usa desde el próximo arranque)", pv.Man.Red.Version)
	}
}

// InfoRed: Ajustes → Network.
type InfoRed struct {
	PuertoFijo     bool
	Puerto         int // el elegido (o el por defecto)
	PorDefecto     int
	EnUso          int  // el puerto TCP en que escucha el servidor propio ahora (0 = parado)
	Reiniciar      bool // el servidor está en marcha con otro ajuste: vale al volver a arrancarlo
	VersionFirmada int64
	OrigenFirmada  string // "paquete" | "guardada" | ""
	NFirmada       int
	NUsuario       int
	MalasUsuario   int
	NFabrica       int
	FicheroUsuario string
}

// InfoRed: lo que enseña Ajustes → Network.
func (n *Nucleo) InfoRed() InfoRed {
	a := n.aj.leer().Red
	i := InfoRed{PuertoFijo: a.PuertoFijo, Puerto: a.Puerto, PorDefecto: sala.PuertoFijoPorDefecto,
		FicheroUsuario: filepath.Join(n.cfg.Datos, sala.NombreRedUsuario)}
	if i.Puerto == 0 {
		i.Puerto = sala.PuertoFijoPorDefecto
	}
	if e := n.l.EstadoServidor(); e.EnMarcha && e.Acceso != nil {
		i.EnUso = e.Acceso.Puerto
		i.Reiniciar = e.Acceso.Pedido != a.PuertoEfectivo()
	}
	n.mu.Lock()
	r := n.red
	n.mu.Unlock()
	i.VersionFirmada, i.OrigenFirmada, i.NFirmada, i.NUsuario, i.MalasUsuario, i.NFabrica =
		r.VersionFirmada, r.OrigenFirmada, r.NFirmada, r.NUsuario, len(r.ErroresUsuario), r.NFabrica
	if i.NFabrica == 0 {
		i.NFabrica = len(sala.ArranqueFabrica())
	}
	return i
}

// PonerPuertoFijo: Ajustes → Network → «Fixed port». Vale para el próximo arranque del servidor propio.
func (n *Nucleo) PonerPuertoFijo(si bool, puerto int) error {
	if puerto == 0 {
		puerto = sala.PuertoFijoPorDefecto
	}
	if err := sala.ValidarPuertoFijo(puerto); err != nil {
		if errors.Is(err, sala.ErrPuertoJuego) {
			return errM("msg.red.puerto_juego", "Ese puerto lo usa el juego. Elige otro (p. ej. 47300).", "por_defecto", sala.PuertoFijoPorDefecto)
		}
		return errM("msg.red.puerto_rango", "El puerto tiene que estar entre 1024 y 65535.")
	}
	if err := n.aj.cambiar(func(a *Ajustes) { a.Red = AjustesRed{PuertoFijo: si, Puerto: puerto} }); err != nil {
		return errGen(err)
	}
	n.l.PonerPuertoFijo(n.aj.leer().Red.PuertoEfectivo())
	return nil
}

// CopiarDireccion: copia «IP pública:puerto» del servidor propio (no es secreta; el código no va aquí).
func (n *Nucleo) CopiarDireccion() error {
	e := n.l.EstadoServidor()
	if !e.EnMarcha {
		return errM("msg.err.srv_parado", "Tu servidor no está en marcha.")
	}
	if e.Acceso == nil || e.Acceso.DirPublica == "" {
		return errM("msg.red.sin_direccion", "Todavía no se sabe tu dirección pública.")
	}
	if n.cfg.Portapapeles == nil {
		return errM("msg.err.sin_portapapeles", "no se puede usar el portapapeles desde aquí")
	}
	if err := n.cfg.Portapapeles(e.Acceso.DirPublica); err != nil {
		return errM("msg.err.no_copia", "No pude copiar al portapapeles: "+err.Error(), "error", err.Error())
	}
	return nil
}

// Modos de «Join by address»: con qué se entra.
const (
	DirConCodigo = "codigo"  // el código de sala (Hidden, o cualquiera: salta la contraseña)
	DirConClave  = "clave"   // la contraseña (servidor con candado)
	DirPublico   = "publico" // nada (servidor público)
)

// EntrarPorDireccion: «Join by address». dir = IP:puerto o nombre:puerto del anfitrión; modo = DirConCodigo |
// DirConClave | DirPublico; secreto = el código o la contraseña. Vuelve enseguida (el progreso va por el evento «entrar»).
func (n *Nucleo) EntrarPorDireccion(dir, modo, secreto string) error {
	d, err := sala.ParseDireccion(dir)
	if err != nil {
		return errM("msg.err.dir_formato", "La dirección no es válida. Escribe la IP o el nombre del anfitrión y su puerto, p. ej. 203.0.113.7:47300.",
			"ejemplo", "203.0.113.7:"+strconv.Itoa(sala.PuertoFijoPorDefecto))
	}
	des := destinoUI{Dir: d.Texto, Nombre: d.Texto}
	switch modo {
	case DirConCodigo:
		if strings.TrimSpace(secreto) == "" {
			return errM("msg.err.dir_falta_codigo", "Pega el código de sala.")
		}
		if err := validarCodigo(secreto); err != nil {
			return err
		}
		des.Codigo = secreto
	case DirConClave:
		if secreto == "" {
			return errM("msg.err.clave_vacia", "Escribe la contraseña del servidor.")
		}
		if utf8.RuneCountInString(secreto) > sala.MaxContrasena {
			return errM("msg.err.clave_mala", "Contraseña incorrecta.")
		}
		des.cred = sala.NuevaCredencial(secreto)
	case DirPublico:
	default:
		return errM("msg.err.generico", "modo de entrada desconocido", "error", modo)
	}
	return n.iniciarEntrada(des)
}

// validarCodigo: el código de sala tiene buena pinta (formato, letras, versión), con el mensaje para la página.
func validarCodigo(codigo string) error {
	_, err := sala.Parse(codigo)
	switch {
	case err == nil:
		return nil
	case errors.Is(err, sala.ErrCodigoV1):
		return errM("msg.err.codigo_v1", "Ese código es de la versión antigua: pide uno nuevo al anfitrión.")
	case errors.Is(err, sala.ErrCodigoFormato):
		return errM("msg.err.codigo_formato", "El código no es válido: "+err.Error()+".")
	case errors.Is(err, sala.ErrCodigoLetra):
		return errM("msg.err.codigo_letra", "El código no es válido: "+err.Error()+".")
	}
	return errM("msg.err.codigo_invalido", "El código no es válido: "+err.Error()+".", "error", err.Error())
}

// msgDireccion: los fallos propios de «Join by address», para la página.
func msgDireccion(err error) Msg {
	switch {
	case errors.Is(err, sala.ErrDireccion):
		return sala.M("msg.err.dir_formato", "La dirección no es válida.", "ejemplo", "203.0.113.7:"+strconv.Itoa(sala.PuertoFijoPorDefecto))
	case errors.Is(err, sala.ErrDirOculta):
		return sala.M("msg.err.dir_oculta", "Ese servidor solo deja entrar con su código de sala.")
	case errors.Is(err, sala.ErrDirOtro):
		return sala.M("msg.err.dir_otro", "En esa dirección contesta otro servidor, no el de tu código.")
	}
	return sala.M("msg.err.dir_sin_respuesta", "No se llega a esa dirección: comprueba la IP y el puerto, y que el anfitrión lo tenga abierto (TCP y UDP) en su router.")
}
