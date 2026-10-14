package sala

import (
	"bytes"
	"io"
	"sync"
)

// FiltroLog: para log.SetOutput. Quita el ruido repetido de las librerías: el mDNS de libp2p escribe «Failed to set
// multicast interface» por CADA interfaz sin multicast (VPN, Tailscale, Hyper-V...) cada pocos segundos; el 06-10 eran
// 7 000 de las 7 700 líneas del log del anfitrión y tapaban lo importante. Se deja pasar la primera vez.
func FiltroLog(w io.Writer) io.Writer { return &filtroLog{w: w, vistos: map[string]bool{}} }

type filtroLog struct {
	w      io.Writer
	mu     sync.Mutex
	vistos map[string]bool
}

var ruidoLib = []string{"Failed to set multicast interface", "Failed to join multicast group"}

func (f *filtroLog) Write(p []byte) (int, error) {
	for _, r := range ruidoLib {
		if bytes.Contains(p, []byte(r)) {
			f.mu.Lock()
			visto := f.vistos[r]
			f.vistos[r] = true
			f.mu.Unlock()
			if visto {
				return len(p), nil
			}
			f.w.Write(append(append([]byte{}, bytes.TrimRight(p, "\r\n")...), []byte(" (no se repite más en este log)\n")...))
			return len(p), nil
		}
	}
	return f.w.Write(p)
}
