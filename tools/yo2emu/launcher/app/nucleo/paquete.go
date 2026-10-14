package nucleo

// «Host a server» desde el PAQUETE del Discord (P5, 11-10; sala/paquete.go): si junto al exe no hay run.py del repo
// pero sí el paquete (server\, python\, node\, engine\, vendor\), se preparan los datos del juego desde la carpeta de
// Ajustes y run.py arranca con el Python de dentro y YO2_PAQUETE=1. Con el exe del repo de desarrollo (el servidor vivo
// del owner) no cambia nada: ahí BuscarRunPy ya encuentra run.py y esto no se mira.

import (
	"context"
	"errors"
	"os"

	"yo2online/juego"
	"yo2online/sala"
)

// paqueteSiFalta: con runpy ya encontrado (desarrollo) devuelve (nil, runpy, nil). Si no, busca el paquete junto al
// exe; si está, prepara los datos del juego (primer_arranque.py) y devuelve el paquete y su run.py. Sin paquete:
// (nil, "", nil) y ArrancarServidor sigue con su «No encuentro run.py» de siempre.
func (n *Nucleo) paqueteSiFalta(runpy string) (*sala.Paquete, string, error) {
	if runpy != "" {
		return nil, runpy, nil
	}
	pq := sala.BuscarPaquete(n.cfg.ExeDir)
	if pq == nil {
		return nil, "", nil
	}
	c := juego.BuscarCarpeta(n.aj.leer().CarpetaJuego, n.cfg.ExeDir, n.cfg.Datos)
	if c == "" {
		return nil, "", errM("msg.paquete.sin_juego", "Para crear un servidor hace falta la carpeta del juego (yo2.exe Ver.080912): elígela en Ajustes.")
	}
	n.logf("[servidor] paquete: preparando los datos del juego desde %s", c)
	out, err := pq.PrepararDatos(context.Background(), c, n.cfg.Datos)
	if out != "" {
		n.logf("[servidor] paquete: %s", out)
	}
	if err != nil {
		var ep *sala.ErrPreparar
		detalle := err.Error()
		if errors.As(err, &ep) {
			detalle = ep.Texto
		}
		return nil, "", errMc(err, "msg.paquete.datos_fallo", "No se pudieron preparar los datos del juego para el servidor: "+detalle,
			"detalle", detalle)
	}
	return pq, pq.RunPy, nil
}

// runPyVisible: el run.py que enseña «Comprobar instalación»: el del repo (o YO2_RUNPY) o, si no, el del paquete.
func (n *Nucleo) runPyVisible() string {
	if rp := sala.BuscarRunPy(os.Getenv("YO2_RUNPY"), n.cfg.ExeDir); rp != "" {
		return rp
	}
	return sala.RunPyPaquete(n.cfg.ExeDir)
}
