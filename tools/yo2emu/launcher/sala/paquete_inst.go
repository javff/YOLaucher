package sala

// Instalación de una versión firmada del paquete (UPD, 12-10). Formato y firma en paquete_man.go, red en paquete_red.go.
//
// Todo pasa DENTRO de la carpeta del lanzador (así los renombres son del mismo disco y atómicos):
//
//	<raiz>\_update\objetos\<sha256>          lo descargado, ya comprobado (sirve para reanudar y entre versiones)
//	<raiz>\_update\v<versión>\<parte>\…      cada parte que cambia, COMPLETA (lo que no cambia, enlazado o copiado de
//	                                         lo instalado) y comprobada fichero a fichero: ni uno de más ni de menos
//	<raiz>\_update\v<versión>\raiz\…         los ficheros sueltos que cambian (el exe, su .firma, los .txt)
//	<raiz>\_update\v<versión>\paquete.json   el manifiesto firmado
//	<raiz>\_update\listo.json                «esta versión está lista para aplicarse»
//
// Preparar (en segundo plano, con el lanzador funcionando) deja todo eso. Aplicar (al arrancar el lanzador, antes de
// abrir nada, o al pulsar «Restart») vuelve a comprobar firma y sha256 y cambia cada parte por renombre:
// <parte> -> _update\viejo-<v>\<parte> y _update\v<v>\<parte> -> <parte>; el exe en marcha se renombra (Windows lo deja)
// y el nuevo ocupa su nombre; paquete.json va el último. Antes de empezar se escribe el plan en
// _update\aplicando.json: si algo falla (o se corta la luz a medias) se deshace en orden inverso, ahora o en el
// siguiente arranque (Recuperar). Nunca se toca nada fuera de esta carpeta: ni la del juego de Konami ni los datos del
// jugador o del servidor (%LOCALAPPDATA%\YO2Online\…).

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ErrServidorEnMarcha: no se aplica con el servidor propio funcionando (usa server\, python\ y node\).
var ErrServidorEnMarcha = errors.New("el servidor está en marcha: se aplicará al pararlo y reiniciar")

// ErrNoMasNueva: la versión ofrecida no es más nueva que la instalada (o ya está preparada).
var ErrNoMasNueva = errors.New("esa versión no es más nueva que la instalada")

const maxIntentosAplicar = 3

// cortePrueba: solo pruebas; true = Aplicar se corta antes del paso k sin deshacer nada (proceso matado a medias).
var cortePrueba func(k int) bool

type Instalacion struct {
	Raiz string // carpeta del lanzador (la del paquete)
	Exe  string // el exe en marcha: ahí se instala el YO2OnlineLauncher.exe del manifiesto (se llame como se llame)
	Logf func(string, ...any)
	// ComprobarExe: que el exe nuevo arranca y es la versión firmada (nil = «<exe> -version»). Las pruebas lo cambian.
	ComprobarExe func(exe string, version int64) error
}

// NuevaInstalacion: la instalación del paquete en la que está este exe, o nil si el exe no está en un paquete (el de
// desarrollo del repo: ese no se actualiza por aquí).
func NuevaInstalacion(exe string, logf func(string, ...any)) *Instalacion {
	if exe == "" {
		return nil
	}
	dir := filepath.Dir(exe)
	if BuscarPaquete(dir) == nil {
		if _, err := os.Stat(filepath.Join(dir, NombrePaquete)); err != nil {
			return nil
		}
	}
	if logf == nil {
		logf = func(string, ...any) {}
	}
	return &Instalacion{Raiz: dir, Exe: exe, Logf: logf}
}

func (i *Instalacion) dir(p ...string) string {
	return filepath.Join(append([]string{i.Raiz, DirActualiza}, p...)...)
}

// rutaLocal: dónde va (o está) esa ruta del manifiesto en esta instalación.
func (i *Instalacion) rutaLocal(r string) string {
	switch r {
	case ExeLanzadorApp:
		return i.Exe
	case ExeLanzadorApp + ExtFirma:
		return i.Exe + ExtFirma
	}
	return filepath.Join(i.Raiz, filepath.FromSlash(r))
}

// Instalada: la versión firmada instalada (nil, 0 = ninguna: un zip de antes del 12-10 no traía paquete.json).
func (i *Instalacion) Instalada() (*PaqueteVerificado, int64) {
	pv, err := LeerPaquete(filepath.Join(i.Raiz, NombrePaquete))
	if err != nil {
		return nil, 0
	}
	return pv, pv.Man.Version
}

func (i *Instalacion) marcaFallida(v int64) string {
	return i.dir("fallida-" + strconv.FormatInt(v, 10) + ".txt")
}

// Fallida: esa versión ya falló al prepararse o aplicarse en este PC y no se reintenta.
func (i *Instalacion) Fallida(v int64) bool { return Existe(i.marcaFallida(v)) }

func (i *Instalacion) marcarFallida(v int64, motivo string) {
	os.MkdirAll(i.dir(), 0o755)
	os.WriteFile(i.marcaFallida(v), []byte(motivo), 0o644)
}

// Existe: hay algo en esa ruta.
func Existe(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// Pendiente: una versión preparada y lista para aplicar.
type Pendiente struct {
	Version         int64    `json:"version"`
	VersionLanzador int64    `json:"version_lanzador"`
	Partes          []string `json:"partes"` // carpetas que se cambian enteras
	Raiz            []string `json:"raiz"`   // ficheros sueltos que se cambian (rutas del manifiesto)
	Borrar          []string `json:"borrar"` // ficheros sueltos que ya no están en la versión nueva
	Exe             bool     `json:"exe"`    // cambia el lanzador (hay que reiniciarlo)
	Bytes           int64    `json:"bytes"`  // lo descargado
}

// Pendiente: lo preparado (nil = nada, o ya no vale).
func (i *Instalacion) Pendiente() *Pendiente {
	b, err := os.ReadFile(i.dir("listo.json"))
	if err != nil {
		return nil
	}
	var p Pendiente
	if json.Unmarshal(b, &p) != nil || p.Version <= 0 {
		return nil
	}
	if _, v := i.Instalada(); p.Version <= v {
		return nil
	}
	return &p
}

// ---------------------------------------------------------------- preparar

// Preparar: deja lista la versión pv (ya verificada) bajando de cli solo lo que falta o ha cambiado. progreso
// (puede ser nil) recibe bytes bajados y total. No toca nada instalado.
func (i *Instalacion) Preparar(ctx context.Context, cli ClientePaquete, pv *PaqueteVerificado, progreso func(hecho, total int64)) (*Pendiente, error) {
	viejo, vInst := i.Instalada()
	v := pv.Man.Version
	if v <= vInst {
		return nil, ErrNoMasNueva
	}
	if p := i.Pendiente(); p != nil && p.Version >= v {
		return p, ErrNoMasNueva
	}
	if i.Fallida(v) {
		return nil, fmt.Errorf("la versión %d ya falló en este PC (borra %s para reintentar)", v, i.marcaFallida(v))
	}
	objetos := i.dir("objetos")
	if err := os.MkdirAll(objetos, 0o755); err != nil {
		return nil, fmt.Errorf("no puedo escribir en la carpeta del lanzador (%w)", err)
	}

	// 1. qué hay ya: lo instalado con el sha256 firmado, o descargado antes
	igual := map[string]bool{}
	tengo := map[string]bool{}
	var faltan []ArchivoPaquete
	var total int64
	pedido := map[string]bool{}
	for _, f := range pv.Man.Ficheros {
		if shaIgual(i.rutaLocal(f.Ruta), f) {
			igual[f.Ruta] = true
			continue
		}
		if tengo[f.SHA256] || shaIgual(filepath.Join(objetos, f.SHA256), f) {
			tengo[f.SHA256] = true
			continue
		}
		if !pedido[f.SHA256] {
			pedido[f.SHA256] = true
			faltan = append(faltan, f)
			total += f.Tam
		}
	}
	i.Logf("[paquete] versión %d: %d fichero(s) iguales, faltan %d (%d KB)", v, len(igual), len(faltan), total>>10)

	// 2. descarga por lotes; cada fichero se comprueba al llegar
	var hecho int64
	for len(faltan) > 0 {
		n, tam := 0, int64(0)
		for n < len(faltan) && n < 2000 && (n == 0 || tam+faltan[n].Tam <= maxLoteBytes) {
			tam += faltan[n].Tam
			n++
		}
		lote := faltan[:n]
		faltan = faltan[n:]
		err := cli.Lote(ctx, lote, func(f ArchivoPaquete, r io.Reader) error {
			if err := guardarObjeto(objetos, f, r, func(m int64) {
				if progreso != nil {
					progreso(hecho+m, total)
				}
			}); err != nil {
				return err
			}
			hecho += f.Tam
			tengo[f.SHA256] = true
			return nil
		})
		if err != nil {
			return nil, fmt.Errorf("descarga: %w", err)
		}
	}
	if progreso != nil {
		progreso(total, total)
	}

	// 3. qué cambia
	p := &Pendiente{Version: v, VersionLanzador: pv.Man.VersionLanzador, Bytes: total}
	cambia := map[string]bool{}
	nuevas := map[string]bool{}
	for _, f := range pv.Man.Ficheros {
		nuevas[strings.ToLower(f.Ruta)] = true
		if igual[f.Ruta] {
			continue
		}
		if esParteDir(f.Parte) {
			cambia[f.Parte] = true
		} else {
			p.Raiz = append(p.Raiz, f.Ruta)
			p.Exe = p.Exe || f.Ruta == ExeLanzadorApp
		}
	}
	if viejo != nil {
		for _, f := range viejo.Man.Ficheros {
			if nuevas[strings.ToLower(f.Ruta)] {
				continue
			}
			if esParteDir(f.Parte) {
				cambia[f.Parte] = true
			} else if Existe(i.rutaLocal(f.Ruta)) {
				p.Borrar = append(p.Borrar, f.Ruta)
			}
		}
	}
	for _, x := range PartesDir {
		if cambia[x] {
			p.Partes = append(p.Partes, x)
		}
	}

	// 4. staging completo y comprobado
	stg := i.dir("v" + strconv.FormatInt(v, 10))
	if err := os.RemoveAll(stg); err != nil {
		return nil, err
	}
	fuente := func(f ArchivoPaquete) string {
		if igual[f.Ruta] {
			return i.rutaLocal(f.Ruta)
		}
		return filepath.Join(objetos, f.SHA256)
	}
	for _, f := range pv.Man.Ficheros {
		var dst string
		switch {
		case cambia[f.Parte]:
			dst = filepath.Join(stg, filepath.FromSlash(f.Ruta))
		case !esParteDir(f.Parte) && !igual[f.Ruta]:
			dst = filepath.Join(stg, "raiz", filepath.FromSlash(f.Ruta))
		default:
			continue
		}
		if err := enlazarOCopiar(fuente(f), dst); err != nil {
			return nil, fmt.Errorf("staging de %s: %w", f.Ruta, err)
		}
	}
	if err := os.WriteFile(filepath.Join(stg, NombrePaquete), pv.Bytes, 0o644); err != nil {
		return nil, err
	}
	if err := i.comprobarStaging(pv, p, stg); err != nil {
		i.marcarFallida(v, err.Error())
		os.RemoveAll(stg)
		return nil, err
	}
	b, _ := json.MarshalIndent(p, "", " ")
	if err := escribirSeguro(i.dir("listo.json"), b); err != nil {
		return nil, err
	}
	// staging de versiones anteriores: ya no hace falta
	if ds, _ := filepath.Glob(i.dir("v*")); len(ds) > 0 {
		for _, d := range ds {
			if d != stg {
				os.RemoveAll(d)
			}
		}
	}
	i.Logf("[paquete] versión %d lista para aplicar: partes %v, ficheros sueltos %v, exe nuevo %v", v, p.Partes, p.Raiz, p.Exe)
	return p, nil
}

func shaIgual(p string, f ArchivoPaquete) bool {
	st, err := os.Stat(p)
	if err != nil || !st.Mode().IsRegular() || st.Size() != f.Tam {
		return false
	}
	sha, err := shaFichero(p)
	return err == nil && sha == f.SHA256
}

// guardarObjeto: lee exactamente f.Tam bytes de r a objetos\<sha>, comprobando el sha256 (si no coincide, nada).
func guardarObjeto(objetos string, f ArchivoPaquete, r io.Reader, avisa func(int64)) error {
	tmp := filepath.Join(objetos, f.SHA256+".parcial")
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	cw := &contador{w: out, h: sha256.New(), max: f.Tam, avisa: avisa}
	_, err = io.Copy(cw, r)
	if err == nil {
		err = out.Sync()
	}
	out.Close()
	if err == nil && cw.n != f.Tam {
		err = fmt.Errorf("%s: llegaron %d de %d bytes", f.Ruta, cw.n, f.Tam)
	}
	if err == nil && hex.EncodeToString(cw.h.Sum(nil)) != f.SHA256 {
		err = fmt.Errorf("%s: el sha256 no es el firmado", f.Ruta)
	}
	if err != nil {
		os.Remove(tmp)
		return err
	}
	return renombrar(tmp, filepath.Join(objetos, f.SHA256))
}

// enlazarOCopiar: enlace duro (instantáneo, mismo disco) o, si no se puede, copia.
func enlazarOCopiar(src, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return err
	}
	os.Remove(dst)
	if os.Link(src, dst) == nil {
		return nil
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

// escribirSeguro: escribe en un temporal (nombre único: un antivirus puede tener abierto el de la vez anterior) y lo
// renombra encima.
func escribirSeguro(p string, b []byte) error {
	tmp := fmt.Sprintf("%s.%d-%d.tmp", p, os.Getpid(), time.Now().UnixNano())
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	_, err = f.Write(b)
	if err == nil {
		err = f.Sync()
	}
	if e := f.Close(); err == nil {
		err = e
	}
	if err == nil {
		err = renombrar(tmp, p)
	}
	if err != nil {
		os.Remove(tmp)
	}
	return err
}

// comprobarStaging: cada parte preparada tiene EXACTAMENTE los ficheros del manifiesto (ni uno de más ni de menos) con
// su sha256 firmado; los sueltos también; el exe nuevo lleva su .firma de editor válida y arranca.
func (i *Instalacion) comprobarStaging(pv *PaqueteVerificado, p *Pendiente, stg string) error {
	if b, err := os.ReadFile(filepath.Join(stg, NombrePaquete)); err != nil {
		return err
	} else if _, err := VerificarPaquete(b); err != nil {
		return err
	}
	var comprobar []aComprobar
	for _, parte := range p.Partes {
		esperados := map[string]ArchivoPaquete{}
		for _, f := range pv.Man.Ficheros {
			if f.Parte == parte {
				esperados[f.Ruta] = f
			}
		}
		raizParte := filepath.Join(stg, parte)
		vistos := 0
		err := filepath.WalkDir(raizParte, func(ruta string, d os.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if d.IsDir() {
				return nil
			}
			if !d.Type().IsRegular() {
				return fmt.Errorf("%s: no es un fichero normal", ruta)
			}
			rel, _ := filepath.Rel(stg, ruta)
			rel = filepath.ToSlash(rel)
			f, ok := esperados[rel]
			if !ok {
				return fmt.Errorf("fichero de más en la versión preparada: %s", rel)
			}
			comprobar = append(comprobar, aComprobar{ruta, f})
			vistos++
			return nil
		})
		if err != nil {
			return err
		}
		if vistos != len(esperados) {
			return fmt.Errorf("parte %s incompleta (%d de %d ficheros)", parte, vistos, len(esperados))
		}
	}
	if err := shaEnParalelo(comprobar); err != nil {
		return err
	}
	for _, r := range p.Raiz {
		f := pv.Fichero(r)
		if f == nil || !shaIgual(filepath.Join(stg, "raiz", filepath.FromSlash(r)), *f) {
			return fmt.Errorf("%s: el sha256 no es el firmado", r)
		}
	}
	// el exe que quedará instalado y su .firma (nuevos o los de ahora) tienen que casar con la firma de editor
	exe, firma := i.rutaLocal(ExeLanzadorApp), i.rutaLocal(ExeLanzadorApp+ExtFirma)
	if p.Exe {
		exe = filepath.Join(stg, "raiz", ExeLanzadorApp)
	}
	for _, r := range p.Raiz {
		if r == ExeLanzadorApp+ExtFirma {
			firma = filepath.Join(stg, "raiz", r)
		}
	}
	fi, err := LeerFirma(firma)
	if err != nil {
		return fmt.Errorf("firma del lanzador: %w", err)
	}
	if err := fi.VerificarPara(NombreLanzadorApp); err != nil {
		return fmt.Errorf("firma del lanzador: %w", err)
	}
	fe := pv.Fichero(ExeLanzadorApp)
	if fi.SHA256 != fe.SHA256 || fi.Tam != fe.Tam || fi.Version != pv.Man.VersionLanzador {
		return errors.New("la .firma del lanzador no es la de su exe")
	}
	if p.Exe {
		comprobar := i.ComprobarExe
		if comprobar == nil {
			comprobar = versionDeExe
		}
		if err := comprobar(exe, pv.Man.VersionLanzador); err != nil {
			return fmt.Errorf("el lanzador nuevo no arranca: %w", err)
		}
	}
	return nil
}

type aComprobar struct {
	ruta string
	f    ArchivoPaquete
}

// shaEnParalelo: cada fichero tiene su sha256 firmado (con varios a la vez: con ~23 000 ficheros pequeños lo que
// cuesta es abrirlos, y así el arranque con una versión pendiente tarda bastante menos).
func shaEnParalelo(fs []aComprobar) error {
	trabajos := make(chan aComprobar)
	malos := make(chan string, len(fs))
	var wg sync.WaitGroup
	for range 8 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for x := range trabajos {
				if !shaIgual(x.ruta, x.f) {
					malos <- x.f.Ruta
				}
			}
		}()
	}
	for _, x := range fs {
		trabajos <- x
	}
	close(trabajos)
	wg.Wait()
	close(malos)
	if m, ok := <-malos; ok {
		return fmt.Errorf("%s: el sha256 no es el firmado", m)
	}
	return nil
}

// versionDeExe: «<exe> -version» tiene que imprimir esa versión (y arrancar: si el antivirus lo bloquea, aquí se ve).
func versionDeExe(exe string, v int64) error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	c := exec.CommandContext(ctx, exe, "-version")
	OcultarVentana(c)
	out, err := c.Output()
	if err != nil {
		return err
	}
	if got := strings.TrimSpace(string(out)); got != strconv.FormatInt(v, 10) {
		return fmt.Errorf("dice ser la versión %q y la firmada es %d", got, v)
	}
	return nil
}

// ---------------------------------------------------------------- aplicar

type paso struct{ De, A string }

type diario struct {
	Version int64  `json:"version"`
	Pasos   []paso `json:"pasos"`
}

// Recuperar: si un Aplicar se cortó a medias (corte de luz, proceso matado), lo deshace. Se llama al arrancar.
func (i *Instalacion) Recuperar() {
	b, err := os.ReadFile(i.dir("aplicando.json"))
	if err != nil {
		return
	}
	var d diario
	if json.Unmarshal(b, &d) != nil {
		os.Remove(i.dir("aplicando.json"))
		return
	}
	if _, v := i.Instalada(); v == d.Version {
		// paquete.json es el último paso: si ya es el de esa versión, se aplicó entera y solo quedó el diario
		borrar(i.dir("aplicando.json"))
		return
	}
	i.Logf("[paquete] la versión %d se quedó a medio aplicar: deshaciendo", d.Version)
	deshacer(d.Pasos, i.Logf)
	borrar(i.dir("aplicando.json"))
	if i.contarIntento(d.Version) >= maxIntentosAplicar {
		i.marcarFallida(d.Version, "se cortó al aplicarla varias veces")
		os.Remove(i.dir("listo.json"))
	}
}

func (i *Instalacion) contarIntento(v int64) int {
	p := i.dir("intentos-" + strconv.FormatInt(v, 10) + ".txt")
	b, _ := os.ReadFile(p)
	n, _ := strconv.Atoi(strings.TrimSpace(string(b)))
	n++
	os.WriteFile(p, []byte(strconv.Itoa(n)), 0o644)
	return n
}

// deshacer: los pasos al revés; cada uno solo si está hecho (el destino existe y el origen no).
func deshacer(ps []paso, logf func(string, ...any)) {
	for k := len(ps) - 1; k >= 0; k-- {
		x := ps[k]
		if Existe(x.A) && !Existe(x.De) {
			if err := renombrar(x.A, x.De); err != nil {
				logf("[paquete] ¡no pude devolver %s a %s!: %v", x.A, x.De, err)
			}
		}
	}
}

// borrar: un fichero, con unos reintentos (antivirus).
func borrar(p string) {
	for k := 0; k < 5; k++ {
		if err := os.Remove(p); err == nil || os.IsNotExist(err) {
			return
		}
		time.Sleep(time.Duration(100*(k+1)) * time.Millisecond)
	}
}

// renombrar: con unos reintentos (un antivirus que está mirando el fichero lo bloquea un momento).
func renombrar(de, a string) error {
	var err error
	for k := 0; k < 5; k++ {
		if err = os.Rename(de, a); err == nil {
			return nil
		}
		time.Sleep(time.Duration(100*(k+1)) * time.Millisecond)
	}
	return err
}

// ResultadoAplicar: qué se instaló.
type ResultadoAplicar struct {
	Version     int64
	ExeCambiado bool // el exe de este proceso ya es el nuevo: hay que relanzarlo
}

// Aplicar instala lo preparado (nada si no hay). servidorEnMarcha (puede ser nil): si dice que sí, no se toca nada.
func (i *Instalacion) Aplicar(servidorEnMarcha func() bool) (*ResultadoAplicar, error) {
	i.Recuperar()
	p := i.Pendiente()
	if p == nil {
		return nil, nil
	}
	if servidorEnMarcha != nil && servidorEnMarcha() {
		return nil, ErrServidorEnMarcha
	}
	stg := i.dir("v" + strconv.FormatInt(p.Version, 10))
	b, err := os.ReadFile(filepath.Join(stg, NombrePaquete))
	if err != nil {
		os.Remove(i.dir("listo.json"))
		return nil, fmt.Errorf("la versión preparada está incompleta: %w", err)
	}
	pv, err := VerificarPaquete(b)
	if err == nil && pv.Man.Version != p.Version {
		err = errors.New("la versión preparada no es la anunciada")
	}
	t0 := time.Now()
	if err == nil {
		// antes de instalar, otra vez: firma y sha256 de todo lo que va a entrar (y que el exe nuevo arranca)
		err = i.comprobarStaging(pv, p, stg)
	}
	tComprobar := time.Since(t0)
	if err != nil {
		i.marcarFallida(p.Version, err.Error())
		os.Remove(i.dir("listo.json"))
		os.RemoveAll(stg)
		return nil, err
	}

	viejo := i.dir("viejo-" + strconv.FormatInt(p.Version, 10))
	os.RemoveAll(viejo)
	if err := os.MkdirAll(filepath.Join(viejo, "raiz"), 0o755); err != nil {
		return nil, err
	}
	var plan []paso
	for _, parte := range p.Partes {
		vivo := filepath.Join(i.Raiz, parte)
		if Existe(vivo) {
			plan = append(plan, paso{vivo, filepath.Join(viejo, parte)})
		}
		plan = append(plan, paso{filepath.Join(stg, parte), vivo})
	}
	for k, r := range append(append([]string{}, p.Raiz...), p.Borrar...) {
		vivo := i.rutaLocal(r)
		if Existe(vivo) {
			plan = append(plan, paso{vivo, filepath.Join(viejo, "raiz", strconv.Itoa(k)+"-"+filepath.Base(vivo))})
		}
		if k < len(p.Raiz) {
			plan = append(plan, paso{filepath.Join(stg, "raiz", filepath.FromSlash(r)), vivo})
		}
	}
	vivo := filepath.Join(i.Raiz, NombrePaquete)
	if Existe(vivo) {
		plan = append(plan, paso{vivo, filepath.Join(viejo, NombrePaquete)})
	}
	plan = append(plan, paso{filepath.Join(stg, NombrePaquete), vivo})

	bd, _ := json.Marshal(diario{Version: p.Version, Pasos: plan})
	if err := escribirSeguro(i.dir("aplicando.json"), bd); err != nil {
		return nil, err
	}
	for k, x := range plan {
		if cortePrueba != nil && cortePrueba(k) {
			return nil, errors.New("corte de prueba (como si se fuera la luz)")
		}
		if err := renombrar(x.De, x.A); err != nil {
			i.Logf("[paquete] no se puede aplicar la versión %d (%s -> %s: %v): vuelta atrás", p.Version, x.De, x.A, err)
			deshacer(plan[:k], i.Logf)
			borrar(i.dir("aplicando.json"))
			if i.contarIntento(p.Version) >= maxIntentosAplicar {
				i.marcarFallida(p.Version, err.Error())
				os.Remove(i.dir("listo.json"))
			}
			return nil, fmt.Errorf("no se pudo cambiar %s (¿abierto por otro programa?): %w", filepath.Base(x.De), err)
		}
	}
	borrar(i.dir("aplicando.json"))
	borrar(i.dir("listo.json"))
	i.Logf("[paquete] versión %d aplicada (partes %v, sueltos %v; comprobada en %v, cambiada en %v)", p.Version, p.Partes,
		p.Raiz, tComprobar.Round(time.Millisecond), (time.Since(t0) - tComprobar).Round(time.Millisecond))
	return &ResultadoAplicar{Version: p.Version, ExeCambiado: p.Exe}, nil
}

// Limpiar: lo que ya no hace falta de actualizaciones anteriores (Aplicar no lo hace: borrar una vendor\ vieja de
// ~23 000 ficheros tarda segundos y no debe retrasar el arranque; AplicarAlArrancar lo lanza aparte) (lo que siga en uso, p. ej. el exe viejo hasta que
// ese proceso se cierre, se queda para la próxima). Nunca con una versión preparada a medio aplicar.
func (i *Instalacion) Limpiar() {
	if Existe(i.dir("aplicando.json")) {
		return
	}
	_, inst := i.Instalada()
	ents, _ := os.ReadDir(i.dir())
	pend := i.Pendiente()
	for _, e := range ents {
		n := e.Name()
		switch {
		case strings.HasPrefix(n, "viejo-"):
			os.RemoveAll(i.dir(n))
		case n == "objetos" && pend == nil:
			os.RemoveAll(i.dir(n))
		case strings.HasPrefix(n, "v") && e.IsDir():
			if v, err := strconv.ParseInt(n[1:], 10, 64); err == nil && v <= inst {
				os.RemoveAll(i.dir(n))
			}
		case strings.HasPrefix(n, "intentos-") || strings.HasPrefix(n, "fallida-"):
			if v, err := strconv.ParseInt(strings.TrimSuffix(strings.SplitN(n, "-", 2)[1], ".txt"), 10, 64); err == nil && v <= inst {
				os.Remove(i.dir(n))
			}
		}
	}
}

// PartesDeManifiesto: resumen por partes (ficheros y bytes) para el log y las pruebas.
func PartesDeManifiesto(m *ManifiestoPaquete) map[string][2]int64 {
	r := map[string][2]int64{}
	for _, f := range m.Ficheros {
		x := r[f.Parte]
		x[0]++
		x[1] += f.Tam
		r[f.Parte] = x
	}
	return r
}

// ordenadas: claves de un mapa en orden (log estable).
func ordenadas[V any](m map[string]V) []string {
	r := make([]string, 0, len(m))
	for k := range m {
		r = append(r, k)
	}
	sort.Strings(r)
	return r
}
