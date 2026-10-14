package nucleo

// Mensajes para el usuario con clave estable (ver sala.Msg). La ventana (frontend/dist/i18n/en.json y es.json) los
// traduce por la clave y los parámetros; aquí no se escribe ningún texto de interfaz que dependa del idioma, salvo Es,
// que es el español de siempre para el log y para las pruebas.
//
// Wails solo entrega a la página el TEXTO de un error devuelto por un método. Por eso un error con clave (ErrUI) lleva
// como texto su JSON {"k":"msg...","p":{...}}: la página lo reconoce y lo traduce; si no es JSON lo enseña tal cual.

import (
	"encoding/json"
	"errors"

	"yo2online/sala"
)

// Msg: ver sala.Msg.
type Msg = sala.Msg

// ErrUI: un error con clave para la ventana.
type ErrUI struct {
	Msg
	Causa error // el error original (para errors.Is y el log), si lo hay
}

// Error es el JSON del mensaje: lo que recibe la página. Para el log, usar Msg.Es.
func (e *ErrUI) Error() string {
	b, err := json.Marshal(e.Msg)
	if err != nil {
		return e.Msg.Es
	}
	return string(b)
}

func (e *ErrUI) Unwrap() error { return e.Causa }

// errM crea un error con clave: clave, texto en español y parámetros (pares nombre, valor).
func errM(k, es string, kv ...any) *ErrUI { return &ErrUI{Msg: sala.M(k, es, kv...)} }

// errMc: lo mismo guardando el error original.
func errMc(causa error, k, es string, kv ...any) *ErrUI {
	return &ErrUI{Msg: sala.M(k, es, kv...), Causa: causa}
}

// MsgDe devuelve el mensaje de un error con clave; si es otro error, uno genérico con el texto del error como parámetro.
func MsgDe(err error) Msg {
	var e *ErrUI
	if errors.As(err, &e) {
		return e.Msg
	}
	return sala.M("msg.err.generico", err.Error(), "error", err.Error())
}

// ErrGenerico: lo mismo que errGen, para el paquete main (la ventana).
func ErrGenerico(err error) error { return errGen(err) }

// errGen deja pasar un error que ya tiene clave y envuelve cualquier otro en uno genérico (nunca llega a la página
// un texto de error suelto sin clave).
func errGen(err error) error {
	if err == nil {
		return nil
	}
	var e *ErrUI
	if errors.As(err, &e) {
		return err
	}
	return errMc(err, "msg.err.generico", err.Error(), "error", err.Error())
}
