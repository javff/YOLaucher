//go:build !windows

package sala

// DesactivarQuickEdit: solo existe en la consola de Windows.
func DesactivarQuickEdit() (restaurar func(), err error) { return func() {}, nil }
