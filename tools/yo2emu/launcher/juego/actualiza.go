package juego

// Autoactualización del lanzador (JoinServer.exe o YO2OnlineLauncher.exe, antes YO2Launcher.exe) y, si el invitado lo pide, de mazos .ydc desde el
// OpenServer / servidor del anfitrión, por el MISMO túnel ya autenticado (P2P o Tor): nada de servidores externos.
// Protocolo y lado del anfitrión en sala/actualiza.go; firma de editor en sala/firma.go.
//
// Solo se instala un exe si:
//   - trae firma del EDITOR válida PARA SU NOMBRE con la clave pública incrustada en ESTE exe (no basta el sha256 del
//     anfitrión; un JoinServer.exe firmado no vale como lanzador con ventana ni al revés);
//   - su versión firmada es MAYOR que la de este exe (nadie nos puede «actualizar» a una versión vieja);
//   - el fichero descargado tiene exactamente el sha256 y el tamaño firmados.
//
// Reemplazo seguro en Windows (un .exe en uso no se puede borrar ni sobrescribir, pero sí renombrar):
//  1. se baja a %LOCALAPPDATA%\YO2Online\actualiza\<nombre>-<sha>.exe y se comprueba;
//  2. <exe> -> <exe>.old, copia del nuevo -> <exe>, se vuelve a comprobar el sha256;
//  3. se relanza con los mismos argumentos. JoinServer espera en su ventana a que acabe el nuevo; la ventana del lanzador
//     espera unos segundos a ver que no se cae y se cierra (el nuevo se queda con el túnel liberado).
//
// Si algo falla antes de relanzar, o la versión nueva se cierra con error nada más arrancar, se deshace el paso 2 y se
// sigue con la versión actual (y esa versión nueva no se vuelve a intentar).

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/hashicorp/yamux"
	"yo2online/sala"
)

const (
	EnvActualizado = "YO2_ACTUALIZADO" // el proceso relanzado no vuelve a intentar actualizar el lanzador
	EnvCodigo      = "YO2_SALA_CODIGO" // el código de sala pasa al relanzado por el entorno (nunca por argumentos ni al log)
	// Si la versión nueva termina con error antes de este tiempo, se considera que no arranca y se vuelve a la anterior.
	ArranqueMinimo = 15 * time.Second
)

// Actualizador: quién soy (qué exe, qué versión) y dónde guardo lo descargado.
type Actualizador struct {
	Datos   string // %LOCALAPPDATA%\YO2Online
	Logf    func(string, ...any)
	Version int64  // versión de ESTE exe (la que puso build.ps1)
	Tipo    string // sala.TipoLanzador (JoinServer) | sala.TipoLanzadorApp (YO2OnlineLauncher)
	Nombre  string // sala.NombreLanzador | sala.NombreLanzadorApp: la identidad firmada (no el nombre del fichero: se reemplaza Exe)
	Exe     string // ruta de este exe ("" = la del proceso)
}

// OpcActualiza: qué hacer en esta conexión.
type OpcActualiza struct {
	Desactivada bool   // -sin-actualizar o el ajuste de la ventana
	SoloMirar   bool   // -probar: se informa, no se cambia nada
	Mazos       bool   // aceptar mazos .ydc del anfitrión
	Carpeta     string // carpeta del juego (vacía en -probar / -sin-juego)

	Aviso       func(string)             // mensajes para quien usa el lanzador (nil = solo al log)
	AvisoMsg    func(sala.Msg)           // la ventana: los avisos que tienen clave van aquí en vez de a Aviso (nil = a Aviso)
	Progreso    func(hecho, total int64) // descarga, como mucho una vez por segundo (nil = nada)
	FinProgreso func()                   // tras la descarga, bien o mal (JoinServer cierra la línea de progreso)

	// Relanzado: argumentos (nil = los del proceso) y variables de entorno extra del exe nuevo.
	Args []string
	Env  []string
	// Consola: JoinServer; el nuevo hereda la ventana y se espera a que acabe. Si no, se espera EsperaHijo a ver que no
	// se cae y se devuelve relanzado=true (la ventana debe cerrarse).
	Consola    bool
	EsperaHijo time.Duration
}

func (a *Actualizador) logf(f string, x ...any) {
	if a.Logf != nil {
		a.Logf(f, x...)
	}
}

// RutaPropia: el exe de este proceso, sin enlaces.
func RutaPropia() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	if r, err := filepath.EvalSymlinks(exe); err == nil {
		exe = r
	}
	return exe
}

func (a *Actualizador) exe() string {
	if a.Exe != "" {
		return a.Exe
	}
	return RutaPropia()
}

// LimpiarViejos borra los <exe>.old* de actualizaciones anteriores (si alguno sigue en uso, se queda).
func (a *Actualizador) LimpiarViejos() {
	exe := a.exe()
	if exe == "" {
		return
	}
	viejos, _ := filepath.Glob(exe + ".old*")
	for _, v := range viejos {
		os.Remove(v)
	}
	// La descarga de la versión que ya está instalada (esta) ya no hace falta.
	if sha, err := sala.ShaFichero(exe); err == nil {
		os.Remove(filepath.Join(a.Datos, "actualiza", a.prefijo()+sha[:16]+".exe"))
	}
}

// visible: el nombre del exe para los avisos (el fichero de verdad: YO2OnlineLauncher.exe, o YO2Launcher.exe si es uno
// de antes del 12-10); a.Nombre es la identidad firmada y puede no coincidir.
func (a *Actualizador) visible() string {
	if e := a.exe(); e != "" {
		return filepath.Base(e)
	}
	return a.Nombre
}

func (a *Actualizador) prefijo() string { return strings.TrimSuffix(a.Nombre, ".exe") + "-" }

func (a *Actualizador) marcaFallida(sha string) string {
	return filepath.Join(a.Datos, "actualiza", "fallida-"+a.prefijo()+sha[:16]+".txt")
}

// Actualizar mira el manifiesto del anfitrión y, si ofrece un exe nuevo y firmado, lo instala y lo relanza. Devuelve
// relanzado=true si ya hay una versión nueva en marcha y este proceso debe terminar (con ese código en modo consola).
func (a *Actualizador) Actualizar(inv *sala.Invitado, s *sala.Sala, o OpcActualiza) (relanzado bool, codigo int) {
	base := o.Aviso
	if base == nil {
		base = func(m string) { a.logf("%s", m) }
	}
	aviso := func(m sala.Msg) {
		if o.AvisoMsg != nil && m.K != "" {
			o.AvisoMsg(m)
			return
		}
		base(m.Es)
	}
	if o.Desactivada {
		a.logf("[actualiza] desactivada")
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	ses, err := inv.Sesion(ctx)
	if err != nil {
		return
	}
	m, err := sala.PedirManifiesto(ses)
	if errors.Is(err, sala.ErrSinActualizacion) {
		a.logf("[actualiza] %v", err)
		return
	}
	if err != nil {
		a.logf("[actualiza] no se pudo pedir el manifiesto: %v", err)
		return
	}
	a.logf("[actualiza] el anfitrión ofrece %d fichero(s); esta versión: %d", len(m.Ficheros), a.Version)

	if f := m.PorTipo(a.Tipo); f != nil {
		if ok, cod := a.revisar(inv, ses, s, *f, o, aviso); ok {
			return true, cod
		}
	}
	if o.Mazos && !o.SoloMirar && o.Carpeta != "" {
		if ses, err = inv.Sesion(ctx); err == nil { // la de antes puede haberse cerrado (relanzamiento fallido)
			a.actualizarMazos(ses, m, o.Carpeta, func(s string) { aviso(sala.Msg{Es: s}) })
		}
	}
	return
}

func (a *Actualizador) revisar(inv *sala.Invitado, ses *yamux.Session, s *sala.Sala, f sala.Fichero, o OpcActualiza,
	aviso func(sala.Msg)) (bool, int) {
	exe := a.exe()
	propio, err := sala.ShaFichero(exe)
	if err != nil {
		a.logf("[actualiza] no puedo leer mi propio exe: %v", err)
		return false, 0
	}
	if strings.EqualFold(propio, f.SHA256) {
		a.logf("[actualiza] lanzador al día (%s…)", propio[:12])
		return false, 0
	}
	// 1. Firma del editor: lo primero, antes de descargar nada. Y que sea la de MI exe, no la de otro.
	if f.Nombre != a.Nombre {
		aviso(sala.M("msg.act.otro_nombre", "El anfitrión ofrece un fichero con otro nombre ("+f.Nombre+") en lugar de "+a.Nombre+": NO se instala.",
			"nombre", f.Nombre, "esperado", a.Nombre))
		return false, 0
	}
	if err := f.Firma.VerificarPara(a.Nombre); err != nil {
		a.logf("[actualiza] el anfitrión ofrece un %s que NO se instala: %v", a.visible(), err)
		aviso(sala.M("msg.act.sin_firma", "El anfitrión ofrece un "+a.visible()+" sin firma válida del editor: NO se instala ("+err.Error()+").",
			"nombre", a.visible()))
		return false, 0
	}
	if !strings.EqualFold(f.Firma.SHA256, f.SHA256) || f.Firma.Tam != f.Tam {
		aviso(sala.M("msg.act.no_firmado", "El anfitrión ofrece un "+a.visible()+" que no es el firmado: NO se instala.", "nombre", a.visible()))
		return false, 0
	}
	// 2. Anti-rollback.
	if f.Firma.Version <= a.Version {
		a.logf("[actualiza] el anfitrión ofrece la versión %d y esta es la %d: no se instala (no se baja de versión)",
			f.Firma.Version, a.Version)
		return false, 0
	}
	if Existe(a.marcaFallida(f.SHA256)) {
		a.logf("[actualiza] la versión %d ya falló al arrancar en este PC: no se reintenta (borra %s para reintentar)",
			f.Firma.Version, a.marcaFallida(f.SHA256))
		return false, 0
	}
	switch {
	case os.Getenv(EnvActualizado) != "":
		// Recién actualizado y el anfitrión ya ofrece otra: no se encadena otra.
		a.logf("[actualiza] recién actualizado y el anfitrión ya ofrece la versión %d: se deja para la próxima", f.Firma.Version)
	case o.SoloMirar:
		aviso(sala.Msg{Es: fmt.Sprintf("Hay una versión nueva y FIRMADA de %s en el anfitrión (%d; esta es la %d): se instalará al jugar.",
			a.visible(), f.Firma.Version, a.Version)}) // solo -probar (consola)
	default:
		return a.instalar(inv, ses, s, f, exe, o, aviso)
	}
	return false, 0
}

func (a *Actualizador) instalar(inv *sala.Invitado, ses *yamux.Session, s *sala.Sala, f sala.Fichero, exe string,
	o OpcActualiza, aviso func(sala.Msg)) (bool, int) {
	mb := float64(f.Tam) / (1 << 20)
	aviso(sala.M("msg.act.descargando", fmt.Sprintf("Hay una versión nueva de %s (%d, firmada; %.0f MB): descargándola por %s...",
		a.visible(), f.Firma.Version, mb, inv.Via), "nombre", a.visible(), "version", f.Firma.Version, "mb", int(mb+0.5), "via", inv.ViaMsg))
	if strings.HasPrefix(inv.Via, "Tor") {
		if o.Consola {
			aviso(sala.Msg{Es: "Por Tor puede tardar unos minutos. (Para saltarlo: cierra y abre con -sin-actualizar.)"})
		} else {
			aviso(sala.M("msg.act.tor_tarda", "Por Tor puede tardar unos minutos. (Para saltarlo: desactiva «Actualizar solo» en Ajustes.)"))
		}
	}
	dir := filepath.Join(a.Datos, "actualiza")
	os.MkdirAll(dir, 0o700)
	nuevo := filepath.Join(dir, a.prefijo()+f.SHA256[:16]+".exe")
	if sha, err := sala.ShaFichero(nuevo); err != nil || !strings.EqualFold(sha, f.SHA256) {
		t0 := time.Now()
		ultimo := time.Time{}
		err := sala.Descargar(ses, f, nuevo, func(hecho, total int64) {
			if o.Progreso != nil && (time.Since(ultimo) > time.Second || hecho == total) {
				ultimo = time.Now()
				o.Progreso(hecho, total)
			}
		})
		if o.FinProgreso != nil {
			o.FinProgreso()
		}
		if err != nil {
			aviso(sala.M("msg.act.descarga_falla", "No se pudo descargar la versión nueva ("+err.Error()+"): sigo con la actual.", "error", err.Error()))
			return false, 0
		}
		a.logf("[actualiza] descargado y verificado contra la firma (sha256 %s…) en %v", f.SHA256[:12],
			time.Since(t0).Round(time.Millisecond))
	} else {
		a.logf("[actualiza] ya estaba descargado de un intento anterior: %s", nuevo)
	}

	// Reemplazo: el exe en uso se renombra (Windows lo permite) y el nuevo ocupa su nombre.
	viejo := exe + ".old"
	if os.Remove(viejo) != nil && Existe(viejo) {
		viejo = exe + ".old-" + time.Now().Format("20060102-150405")
	}
	if err := os.Rename(exe, viejo); err != nil {
		aviso(sala.M("msg.act.no_reemplaza", fmt.Sprintf("No puedo reemplazar %s (%v): sigo con la versión actual. Puedes copiar a mano %s en su lugar.",
			exe, err, nuevo), "exe", exe, "error", err.Error(), "nuevo", nuevo))
		return false, 0
	}
	deshacer := func(motivo sala.Msg) {
		if err := os.Remove(exe); err != nil && Existe(exe) {
			os.Rename(exe, exe+".fallido-"+time.Now().Format("150405"))
		}
		if err := os.Rename(viejo, exe); err != nil {
			a.logf("[actualiza] ¡no pude devolver %s a su nombre!: %v", viejo, err)
			aviso(sala.M("msg.act.renombra", "OJO: renombra a mano "+viejo+" a "+filepath.Base(exe)+".", "viejo", viejo, "exe", filepath.Base(exe)))
		}
		aviso(sala.M("msg.act.no_instala", "No se pudo instalar la versión nueva ("+motivo.Es+"): sigo con la actual.", "motivo", motivo))
	}
	if err := copiarFichero(nuevo, exe); err != nil {
		deshacer(sala.M("msg.act.motivo_error", err.Error(), "error", err.Error()))
		return false, 0
	}
	if sha, err := sala.ShaFichero(exe); err != nil || !strings.EqualFold(sha, f.SHA256) {
		deshacer(sala.M("msg.act.motivo_copia", "la copia no coincide con la descargada"))
		return false, 0
	}

	return a.relanzar(inv, s, f, exe, nuevo, o, aviso, deshacer)
}

// relanzar: suelta la conexión y arranca el exe nuevo. En modo consola (JoinServer) espera a que termine; en el de ventana
// espera EsperaHijo a ver que no se cae y devuelve relanzado=true (la ventana debe cerrarse). Si el nuevo se cae nada más
// arrancar, deshace el reemplazo y se sigue con la versión de antes.
func (a *Actualizador) relanzar(inv *sala.Invitado, s *sala.Sala, f sala.Fichero, exe, nuevo string, o OpcActualiza,
	aviso func(sala.Msg), deshacer func(sala.Msg)) (bool, int) {
	// Antes se suelta la conexión (el nuevo usa la misma carpeta de Tor).
	aviso(sala.M("msg.act.reiniciando", fmt.Sprintf("%s actualizado a la versión %d. Reiniciándolo...", a.visible(), f.Firma.Version),
		"nombre", a.visible(), "version", f.Firma.Version))
	inv.Cerrar()
	args := o.Args
	if args == nil {
		args = os.Args[1:]
	}
	cmd := exec.Command(exe, args...)
	cmd.Env = append(os.Environ(), EnvActualizado+"=1")
	cmd.Env = append(cmd.Env, o.Env...)
	if o.Consola {
		cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
		cmd.Env = append(cmd.Env, EnvCodigo+"="+s.Codigo())
	}
	t0 := time.Now()
	if err := cmd.Start(); err != nil {
		deshacer(sala.M("msg.act.motivo_no_arranca", "no arranca: "+err.Error()+"; ¿lo ha bloqueado el antivirus?", "error", err.Error()))
		os.WriteFile(a.marcaFallida(f.SHA256), []byte(err.Error()), 0o600)
		return false, 0 // la sesión se rehará sola en el siguiente uso (Invitado.Sesion reconecta)
	}
	a.logf("[actualiza] relanzado como pid %d", cmd.Process.Pid)
	var err error
	if o.Consola {
		a.logf("[actualiza] espero a que termine")
		// Ctrl+C / cerrar la ventana llega a los dos procesos: este no hace nada y deja que el nuevo cierre bien.
		signal.Notify(make(chan os.Signal, 1), os.Interrupt, syscall.SIGTERM)
		err = cmd.Wait()
	} else {
		hecho := make(chan error, 1)
		go func() { hecho <- cmd.Wait() }()
		espera := o.EsperaHijo
		if espera <= 0 {
			espera = 5 * time.Second
		}
		select {
		case err = <-hecho:
		case <-time.After(espera):
			// Sigue vivo pasados unos segundos: arranca bien. Este proceso se cierra y le deja el sitio.
			a.logf("[actualiza] el exe nuevo sigue vivo tras %v: este proceso se cierra", espera)
			os.Remove(nuevo)
			return true, 0
		}
	}
	cod := 0
	if ee, ok := err.(*exec.ExitError); ok {
		cod = ee.ExitCode()
	}
	a.logf("[actualiza] la versión nueva terminó en %v: %v", time.Since(t0).Round(time.Millisecond), err)
	if err != nil && time.Since(t0) < ArranqueMinimo {
		// Se ha caído nada más arrancar (antivirus, binario roto...): vuelta a la versión de antes y se sigue con ella.
		deshacer(sala.M("msg.act.motivo_se_cerro", fmt.Sprintf("la versión nueva se cerró al arrancar (%v)", err), "error", fmt.Sprint(err)))
		os.WriteFile(a.marcaFallida(f.SHA256), []byte(fmt.Sprint(err)), 0o600)
		os.Remove(nuevo)
		if o.Consola {
			signal.Reset(os.Interrupt, syscall.SIGTERM)
		}
		return false, 0
	}
	os.Remove(nuevo)
	return true, cod
}

// actualizarMazos instala los .ydc del anfitrión en cada perfil del juego (profiles\<perfil>\<mazo>.ydc). Un mazo que
// ya exista con otro contenido se guarda antes como <mazo>.ydc.antes.
func (a *Actualizador) actualizarMazos(ses *yamux.Session, m *sala.Manifiesto, carpeta string, aviso func(string)) {
	var perfiles []string
	ents, _ := os.ReadDir(filepath.Join(carpeta, "profiles"))
	for _, e := range ents {
		if e.IsDir() {
			perfiles = append(perfiles, filepath.Join(carpeta, "profiles", e.Name()))
		}
	}
	hay := 0
	for _, f := range m.Ficheros {
		if f.Tipo == sala.TipoMazo {
			hay++
		}
	}
	if hay == 0 {
		return
	}
	if len(perfiles) == 0 {
		aviso(fmt.Sprintf("El anfitrión comparte %d mazo(s), pero aún no tienes ningún perfil en el juego: entra una vez "+
			"y se instalarán la próxima.", hay))
		return
	}
	tmpDir := filepath.Join(a.Datos, "actualiza", "mazos")
	os.MkdirAll(tmpDir, 0o700)
	n, fallos := 0, 0
	for _, f := range m.Ficheros {
		if f.Tipo != sala.TipoMazo {
			continue
		}
		if !sala.NombreMazoValido(f.Nombre) || f.Tam > sala.MaxMazo {
			a.logf("[actualiza] mazo rechazado: %q (%d bytes)", f.Nombre, f.Tam)
			continue
		}
		var falta []string
		for _, p := range perfiles {
			if sha, err := sala.ShaFichero(filepath.Join(p, f.Nombre)); err != nil || !strings.EqualFold(sha, f.SHA256) {
				falta = append(falta, p)
			}
		}
		if len(falta) == 0 {
			continue
		}
		tmp := filepath.Join(tmpDir, f.Nombre)
		if err := sala.Descargar(ses, f, tmp, nil); err != nil {
			fallos++
			a.logf("[actualiza] mazo %s: %v", f.Nombre, err)
			continue
		}
		for _, p := range falta {
			dst := filepath.Join(p, f.Nombre)
			if Existe(dst) {
				copiarFichero(dst, dst+".antes")
			}
			if err := copiarFichero(tmp, dst); err != nil {
				fallos++
				a.logf("[actualiza] mazo %s -> %s: %v", f.Nombre, p, err)
				continue
			}
			n++
		}
		os.Remove(tmp)
		a.logf("[actualiza] mazo instalado: %s en %d perfil(es)", f.Nombre, len(falta))
	}
	if n > 0 || fallos > 0 {
		aviso(fmt.Sprintf("Mazos del anfitrión: %d copiados a tus perfiles, %d con error (si ya tenías uno con ese nombre, "+
			"el tuyo queda como .antes).", n, fallos))
	}
}

func copiarFichero(origen, destino string) error {
	in, err := os.Open(origen)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(destino, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		os.Remove(destino)
		return err
	}
	if err := out.Sync(); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}
