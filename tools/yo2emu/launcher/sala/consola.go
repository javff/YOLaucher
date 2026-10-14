package sala

// Consola: escritura en la ventana que NUNCA bloquea a quien avisa.
//
// Incidente del 07-10: el owner hizo clic en la ventana de OpenServer, la consola clásica de Windows entró en modo
// «Seleccionar» (QuickEdit) y desde ese momento cualquier escritura en ella se queda parada hasta que se pulsa Esc o
// Intro. El aviso «Invitado … conectado» se escribía en la consola ANTES de aceptar los streams del invitado, así que
// durante 35 min ningún invitado pudo entrar (el log lo delata: 9 túneles del mismo invitado apuntados a la vez a las
// 12:36:10, cuando la consola se soltó).
//
// Arreglo: (1) al arrancar se quita QuickEdit (consola_windows.go: DesactivarQuickEdit); (2) aun así (el modo
// «Marcar» del menú de la ventana también congela), los avisos van por un canal con búfer a UNA goroutine que es la
// única que escribe en la ventana. Si la ventana está congelada y el búfer se llena, los avisos se descartan (ya están
// en el log, que se escribe ANTES) y, cuando la ventana vuelve, se dice cuántos se perdieron.

import (
	"fmt"
	"io"
	"sync/atomic"
	"time"
)

type Consola struct {
	w           io.Writer
	ch          chan string
	descartadas atomic.Int64
	pendientes  atomic.Int64 // líneas en el canal o escribiéndose (para Vaciar)
}

// NuevaConsola: buf = cuántas líneas se guardan mientras la ventana no deja escribir.
func NuevaConsola(w io.Writer, buf int) *Consola {
	c := &Consola{w: w, ch: make(chan string, buf)}
	go c.escritor()
	return c
}

func (c *Consola) escritor() {
	for m := range c.ch {
		io.WriteString(c.w, m)
		// Los descartados son los MÁS NUEVOS (llegaron con el búfer lleno): se dice cuántos cuando ya ha salido todo lo
		// que había antes que ellos.
		if len(c.ch) == 0 {
			if n := c.descartadas.Swap(0); n > 0 {
				fmt.Fprintf(c.w, "(… %d aviso(s) no se pudieron enseñar porque la ventana estaba en pausa: están en el log)\n", n)
			}
		}
		c.pendientes.Add(-1)
	}
}

// Aviso: «[hh:mm:ss] m». No bloquea nunca: si el búfer está lleno, se descarta (el llamante ya lo ha escrito en el log).
func (c *Consola) Aviso(m string) bool {
	return c.Linea(fmt.Sprintf("[%s] %s", time.Now().Format("15:04:05"), m))
}

// Linea: como Aviso, sin hora. No bloquea nunca.
func (c *Consola) Linea(m string) bool { return c.Escribir(m + "\n") }

// Escribir: el texto tal cual (sin salto de línea; p. ej. una barra de progreso con \r). No bloquea nunca.
func (c *Consola) Escribir(m string) bool {
	c.pendientes.Add(1)
	select {
	case c.ch <- m:
		return true
	default:
		c.pendientes.Add(-1)
		c.descartadas.Add(1)
		return false
	}
}

// Imprimir: para el hilo principal (cabeceras, el cuadro del código…): ESPERA a que haya sitio en el búfer, así no se
// pierde y sale en orden con los avisos. Nunca usarlo desde el camino de un invitado.
func (c *Consola) Imprimir(m string) {
	c.pendientes.Add(1)
	c.ch <- m
}

// Println: Imprimir con salto de línea.
func (c *Consola) Println(a ...any) { c.Imprimir(fmt.Sprintln(a...)) }

// Vaciar espera (como mucho d) a que lo pendiente esté escrito: antes de leer del teclado o de cerrar.
func (c *Consola) Vaciar(d time.Duration) bool {
	fin := time.Now().Add(d)
	for c.pendientes.Load() > 0 {
		if time.Now().After(fin) {
			return false
		}
		time.Sleep(10 * time.Millisecond)
	}
	return true
}

// Descartadas: avisos perdidos desde la última vez que se escribió en la ventana (para las pruebas).
func (c *Consola) Descartadas() int64 { return c.descartadas.Load() }
