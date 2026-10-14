//go:build !windows

package nucleo

import "errors"

// Fuera de Windows no hay DPAPI: no se guarda nada protegido (el anfitrión vuelve a escribir la contraseña y el jugador
// no puede marcar «Remember password»).
var errSinDPAPI = errors.New("sin DPAPI en este sistema")

func proteger([]byte) ([]byte, error)    { return nil, errSinDPAPI }
func desproteger([]byte) ([]byte, error) { return nil, errSinDPAPI }
