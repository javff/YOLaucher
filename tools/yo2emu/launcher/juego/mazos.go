package juego

// Mazos premontados (11-10): el lanzador copia 15 mazos (8 Structure Decks oficiales + 7 populares de 2008) a
// profiles\<personaje>\ de cada perfil que YA exista. Los mazos son del cliente (el editor lista
// profiles\<personaje>\*.ydc), no del servidor. Solo valen con el baúl inicial all3 (TRUNK_START=all3, el de por defecto).
// Los 7 populares son listas propias y van incrustados (mazos/*.ydc); los 8 oficiales se sacan del merge.dat del propio
// jugador (mazos_cliente.go, PUB 12-10): el lanzador no lleva datos del juego.
//
// Reglas:
//   - solo carpetas de perfil que ya existan (el nombre es el del personaje: el lanzador no lo sabe antes);
//   - si el .ydc ya existe, no se pisa;
//   - marca por perfil (MarcaMazos): tras copiar, no se vuelve a copiar nada en ese perfil, así un mazo que el jugador
//     borra desde el editor no reaparece cada 5 s. Un juego de mazos nuevo en el futuro = marca -v2;
//   - se escribe a <nombre>.ydc.tmp y se renombra (el cliente solo lista *.ydc: nunca ve uno a medias);
//   - NUNCA se toca deck.ydc (el mazo activo; el cliente lo tiene en memoria y lo reescribe).

import (
	"embed"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
)

//go:embed mazos/*.ydc
var mazosFS embed.FS

// MarcaMazos: fichero por perfil que dice «los premontados ya se copiaron aquí». No acaba en .ydc: el cliente lo ignora.
const MarcaMazos = ".mazos-oficiales-v1"

// MazosPremontados: nombres de los .ydc incrustados (los populares; para las pruebas y el log).
func MazosPremontados() []string {
	ents, _ := fs.ReadDir(mazosFS, "mazos")
	var r []string
	for _, e := range ents {
		if !e.IsDir() && strings.HasSuffix(strings.ToLower(e.Name()), ".ydc") && !strings.EqualFold(e.Name(), "deck.ydc") {
			r = append(r, e.Name())
		}
	}
	return r
}

// InstalarMazos copia los premontados a cada profiles\<personaje>\ existente que no tenga la marca. Devuelve cuántos
// ficheros ha escrito (0 casi siempre). Nunca crea la carpeta de un perfil ni la de profiles\.
func InstalarMazos(juego string) int { return InstalarMazosLog(juego, nil) }

// InstalarMazosLog: InstalarMazos con log (una vez por sesión: los Structure Decks que el cliente no tiene como se
// esperaba).
func InstalarMazosLog(juego string, logf func(string, ...any)) int {
	raiz := filepath.Join(juego, "profiles")
	ents, err := os.ReadDir(raiz)
	if err != nil {
		return 0
	}
	var perfiles []string
	for _, e := range ents {
		if e.IsDir() && !Existe(filepath.Join(raiz, e.Name(), MarcaMazos)) { // en profiles\ hay también un .bin suelto
			perfiles = append(perfiles, filepath.Join(raiz, e.Name()))
		}
	}
	if len(perfiles) == 0 {
		return 0 // lo normal: nada que hacer (y sin leer merge.dat)
	}
	mazos, fallo := mazosAInstalar(juego, logf)
	n := 0
	for _, dir := range perfiles {
		n += instalarEnPerfil(dir, mazos, fallo)
	}
	return n
}

// mazosAInstalar: los incrustados + los oficiales del cliente. fallo = los oficiales no se pudieron leer ahora (merge.dat
// bloqueado...): se copia lo demás pero sin marca, para reintentar.
func mazosAInstalar(juego string, logf func(string, ...any)) (map[string][]byte, bool) {
	mazos := map[string][]byte{}
	fallo := false
	for _, nombre := range MazosPremontados() {
		if datos, err := mazosFS.ReadFile(path.Join("mazos", nombre)); err == nil {
			mazos[nombre] = datos
		} else {
			fallo = true
		}
	}
	r, err := oficiales(juego)
	if err != nil {
		if logf != nil {
			logf("mazos oficiales: no se pudo leer merge.dat (%v); se reintenta", err)
		}
		return mazos, true
	}
	for nombre, datos := range r.mazos {
		mazos[nombre] = datos
	}
	cacheOficialesMu.Lock()
	if !r.avisado && len(r.avisos) > 0 && logf != nil {
		logf("mazos oficiales que no se instalan (cliente distinto del Ver.080912): %s", strings.Join(r.avisos, "; "))
	}
	r.avisado = r.avisado || logf != nil
	cacheOficialesMu.Unlock()
	return mazos, fallo
}

func instalarEnPerfil(dir string, mazos map[string][]byte, fallo bool) int {
	if Existe(filepath.Join(dir, MarcaMazos)) {
		return 0
	}
	n := 0
	nombres := make([]string, 0, len(mazos))
	for nombre := range mazos {
		nombres = append(nombres, nombre)
	}
	sort.Strings(nombres)
	for _, nombre := range nombres {
		dst := filepath.Join(dir, nombre)
		if _, err := os.Lstat(dst); err == nil {
			continue // ya lo tiene (suyo o de una copia anterior a medias): no se pisa
		}
		escrito, err := escribirNuevo(dst, mazos[nombre])
		if err != nil {
			fallo = true
			continue
		}
		if escrito {
			n++
		}
	}
	if !fallo { // si algo falló (disco lleno, perfil de solo lectura...), sin marca: se reintenta en la próxima pasada
		os.WriteFile(filepath.Join(dir, MarcaMazos), []byte("Pre-built decks copied by YO2OnlineLauncher. Delete this file to get the missing ones back.\r\n"), 0o644)
	}
	return n
}

// escribirNuevo: a dst+".tmp" y luego renombrar, sin pisar nunca un dst que haya aparecido entretanto.
func escribirNuevo(dst string, datos []byte) (bool, error) {
	tmp := dst + ".tmp"
	if err := os.WriteFile(tmp, datos, 0o644); err != nil {
		os.Remove(tmp)
		return false, err
	}
	if _, err := os.Lstat(dst); err == nil { // el jugador acaba de guardar uno con ese nombre: es suyo
		os.Remove(tmp)
		return false, nil
	}
	if err := os.Rename(tmp, dst); err != nil {
		os.Remove(tmp)
		return false, err
	}
	return true, nil
}
