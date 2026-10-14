//go:build !windows

package nucleo

import "errors"

// moverAPapelera: fuera de Windows no hay papelera conocida; aPapelera aparta la carpeta a servidores\_borrados.
func moverAPapelera(string) error { return errors.New("sin papelera en este sistema") }
