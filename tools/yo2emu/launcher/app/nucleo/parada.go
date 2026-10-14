package nucleo

// Parar run.py con orden (decisión del owner): por la consola de run.py (127.0.0.1:5799+offset), primera línea
// «AUTH <token>» con el token de %LOCALAPPDATA%\YO2Online\consola_<puerto>.txt (yo2srv/admin/consola.py), y luego
// «DEADLINE = 1»: el bucle principal de run.py acaba en el siguiente segundo y escribe «fin de la captura». Se espera
// hasta 30 s a que el proceso termine; si no termina, se mata como último recurso y se dice.
//
// Solo se llama con el run.py que arrancó ESTE lanzador. El token nunca se escribe en el log ni en mensajes.

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"yo2online/sala"
)

// PuertoConsola: el de la consola de run.py (5799 + YO2_PORT_OFFSET).
func PuertoConsola(offset int) int { return 5799 + offset }

// ParadaOpc: qué proceso parar y dónde está su consola.
type ParadaOpc struct {
	Offset int
	Datos  string // carpeta de datos del lanzador (con YO2_LANZADOR_DATOS NO es la de run.py: ver dirsToken)
	Local  string // donde run.py deja consola_<puerto>.txt: %LOCALAPPDATA%\YO2Online (vacío = se saca del entorno)
	Proc   *os.Process
	Hecho  <-chan struct{} // se cierra cuando el proceso termina (nil = no se sabe: se mira Proc)
	Espera time.Duration   // cuánto esperar a que termine solo (por defecto 30 s)
	Logf   func(string, ...any)
}

// ResultadoParada: cómo acabó.
type ResultadoParada struct {
	Limpia  bool          // run.py terminó solo tras la orden
	Forzada bool          // no terminó a tiempo: se mató
	Tarda   time.Duration // desde la orden hasta que terminó
	Mensaje string        // el texto en español de siempre (log y pruebas; sin secretos)
	Partes  []Msg         // lo mismo con claves, para la ventana: se enseñan una detrás de otra
}

func (o ParadaOpc) logf(f string, a ...any) {
	if o.Logf != nil {
		o.Logf(f, a...)
	}
}

// dirsToken: dónde puede estar el token de la consola. run.py lo escribe SIEMPRE en %LOCALAPPDATA%\YO2Online (consola.py,
// guardar_token); la carpeta de datos del lanzador solo coincide si no se cambió con YO2_LANZADOR_DATOS (pruebas), así que
// se mira primero la de run.py y luego la del lanzador. (run.py no tiene ninguna variable de entorno para cambiar esa
// carpeta, así que no hay nada que pasarle al arrancarlo: se busca en los dos sitios.)
func dirsToken(o ParadaOpc) []string {
	local := o.Local
	if local == "" {
		if la := os.Getenv("LOCALAPPDATA"); la != "" {
			local = filepath.Join(la, "YO2Online")
		}
	}
	var r []string
	for _, d := range []string{local, o.Datos} {
		if d != "" && (len(r) == 0 || r[0] != d) {
			r = append(r, d)
		}
	}
	return r
}

func leerToken(o ParadaOpc, puerto int) ([]byte, error) {
	var ult error
	for _, d := range dirsToken(o) {
		b, err := os.ReadFile(filepath.Join(d, fmt.Sprintf("consola_%d.txt", puerto)))
		if err == nil {
			return b, nil
		}
		if ult == nil {
			ult = err
		}
	}
	if ult == nil {
		ult = os.ErrNotExist
	}
	return nil, ult
}

// ordenarFin: abre la consola, se identifica y manda «DEADLINE = 1». Devuelve ok=true si run.py contestó ok; si no, por
// qué no pudo (motivo, con clave).
func ordenarFin(o ParadaOpc) (motivo Msg, ok bool) {
	p := PuertoConsola(o.Offset)
	b, err := leerToken(o, p)
	if err != nil {
		return sala.M("msg.parar.m_sin_token", fmt.Sprintf("no encuentro el token de la consola de run.py (%v)", err), "error", err.Error()), false
	}
	tok := strings.TrimSpace(string(b))
	if tok == "" || strings.ContainsAny(tok, "\r\n") {
		return sala.M("msg.parar.m_token_malo", "el token de la consola de run.py no es válido"), false
	}
	c, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(p)), 3*time.Second)
	if err != nil {
		return sala.M("msg.parar.m_sin_consola", fmt.Sprintf("la consola de run.py no contesta (%v)", err), "error", err.Error()), false
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(8 * time.Second))
	// AUTH y la orden juntas: run.py contesta una línea («ok» / «error: ...») por cada línea DESPUÉS del AUTH.
	if _, err := c.Write([]byte("AUTH " + tok + "\nDEADLINE = 1\n")); err != nil {
		return sala.M("msg.parar.m_no_habla", fmt.Sprintf("no pude hablar con la consola de run.py (%v)", err), "error", err.Error()), false
	}
	resp, err := bufio.NewReader(c).ReadString('\n')
	resp = strings.TrimSpace(resp)
	switch {
	case resp == "ok":
		return Msg{}, true
	case resp != "":
		return sala.M("msg.parar.m_respuesta", fmt.Sprintf("run.py contestó %q a la orden de cerrar", resp), "respuesta", resp), false
	case err != nil:
		return sala.M("msg.parar.m_cierra_sin_contestar", "la consola de run.py cerró sin contestar (¿token viejo?)"), false
	}
	return sala.M("msg.parar.m_no_contesto", "la consola de run.py no contestó"), false
}

// PararRunPy manda la orden de cerrar y espera. Bloquea hasta 30 s (o Espera) + 5 s si hay que matarlo.
func PararRunPy(o ParadaOpc) ResultadoParada {
	espera := o.Espera
	if espera <= 0 {
		espera = 30 * time.Second
	}
	t0 := time.Now()
	hecho := o.Hecho
	if hecho == nil && o.Proc != nil {
		ch := make(chan struct{})
		go func() { o.Proc.Wait(); close(ch) }()
		hecho = ch
	}
	if hecho == nil {
		m := sala.M("msg.parar.ajeno", "run.py no lo arrancó este lanzador: no se toca.")
		return ResultadoParada{Mensaje: m.Es, Partes: []Msg{m}}
	}
	var motivo Msg
	if m, ok := ordenarFin(o); !ok {
		motivo = m
		o.logf("[parar] orden de cierre fallida: %s", m.Es)
	} else {
		o.logf("[parar] orden de cierre enviada a run.py; espero hasta %v", espera)
	}
	select {
	case <-hecho:
		d := time.Since(t0).Round(100 * time.Millisecond)
		o.logf("[parar] run.py terminó en %v", d)
		m := sala.M("msg.parar.con_orden", fmt.Sprintf("run.py se cerró con orden (guardó y salió) en %v.", d), "seg", d.Seconds())
		if !motivo.Vacio() {
			m = sala.M("msg.parar.solo", fmt.Sprintf("run.py se cerró solo en %v.", d), "seg", d.Seconds())
		}
		return ResultadoParada{Limpia: true, Tarda: d, Mensaje: m.Es, Partes: []Msg{m}}
	case <-time.After(espera):
	}
	// Último recurso.
	o.logf("[parar] run.py no terminó en %v: se mata", espera)
	if o.Proc != nil {
		o.Proc.Kill()
	}
	select {
	case <-hecho:
	case <-time.After(5 * time.Second):
	}
	m := sala.M("msg.parar.forzado", fmt.Sprintf("run.py no se cerró en %v y hubo que matarlo a la fuerza: lo último que no hubiera guardado puede haberse perdido.",
		espera.Round(time.Second)), "seg", int(espera.Round(time.Second).Seconds()))
	partes, es := []Msg{m}, m.Es
	if !motivo.Vacio() {
		pm := sala.M("msg.parar.motivo", "Motivo de que no se pudiera cerrar con orden: "+motivo.Es+".", "motivo", motivo)
		partes, es = append(partes, pm), es+" "+pm.Es
	}
	return ResultadoParada{Forzada: true, Tarda: time.Since(t0), Mensaje: es, Partes: partes}
}
