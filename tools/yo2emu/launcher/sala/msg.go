package sala

// Msg: un mensaje para el usuario con CLAVE estable (K) y parámetros (P). La ventana (frontend/dist/i18n/*.json) lo
// traduce por la clave; así no se traduce comparando textos. Es (el texto en español de siempre) lo siguen usando la
// consola de JoinServer/OpenServer y el log, que no cambian; no viaja a la página.
//
// Las claves que emite Go empiezan por «msg.»: una prueba (app/nucleo/i18n_test.go) las busca en el código y comprueba
// que existen en los dos idiomas. Un parámetro puede ser a su vez un Msg o una lista de Msg (la página los traduce
// recursivamente).
type Msg struct {
	K  string         `json:"k"`
	P  map[string]any `json:"p,omitempty"`
	Es string         `json:"-"`
}

// M crea un Msg: clave, texto en español y parámetros como pares nombre, valor.
func M(k, es string, kv ...any) Msg {
	m := Msg{K: k, Es: es}
	for i := 0; i+1 < len(kv); i += 2 {
		if m.P == nil {
			m.P = map[string]any{}
		}
		m.P[kv[i].(string)] = kv[i+1]
	}
	return m
}

// String: el texto en español (consola y log).
func (m Msg) String() string { return m.Es }

// Vacio: sin clave ni texto.
func (m Msg) Vacio() bool { return m.K == "" && m.Es == "" }

// Con devuelve una copia con un parámetro más.
func (m Msg) Con(nombre string, valor any) Msg {
	p := make(map[string]any, len(m.P)+1)
	for k, v := range m.P {
		p[k] = v
	}
	p[nombre] = valor
	m.P = p
	return m
}
