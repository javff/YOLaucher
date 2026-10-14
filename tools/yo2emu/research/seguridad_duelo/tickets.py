"""Reservas de duelo de un solo uso (opción A de PROPUESTA.md con los ajustes de OFICIAL.md §3). Lo usa run.py.

Idea (GameLift «player session RESERVED -> ACTIVE/TIMEDOUT», Cosmic/MapleStory «IP -> charId en tránsito», y lo que
dice el propio cliente: 予約確認 = «comprobación de la reserva»): la reserva nace en el servidor cuando ÉL manda 0x4306
DUELSERVERNOTICE por la conexión de una sesión con login, y guarda {sesión, pid, IP de esa conexión, conexión, tipo,
duelo concreto, hora}. El 0x6001 del puerto de duelo solo vale si hay una reserva para ese pid, sin usar, de menos de
TTL_S y desde la MISMA IP. El cliente no manda nada más que el pid (FUN_006917b0), así que la IP (la 127.0.0.X que
pone el túnel por invitado) es lo único que liga las dos conexiones.

Reglas (OFICIAL.md §3 «Añadiría»):
  - un intento desde OTRA IP no gasta la reserva (si no, cualquiera podría «quemar» la de la víctima);
  - la reserva muere si se cierra la conexión por la que se mandó el 0x4306, si la sesión ya no es la viva, o si
    ese duelo se anula (el que llama lo dice con `alive`);
  - un 0x6001 repetido desde la IP buena tras usarla se apunta aparte («reconexion»), no como ataque.

Este módulo NO decide qué hacer: check() solo dice si la reserva vale y por qué no. El modo (off / vigilar / on) lo
aplica run.py (duel_reserva_6001) con decide().
"""
import collections
import threading
import time

TTL_S = 90.0              # = ATTACH_TIMEOUT de pvp_match; GameLift usa 60 s
INSIST_N = 5              # rechazos sospechosos de una IP ...
INSIST_S = 300.0          # ... en esta ventana -> aviso (nunca veto)
MODES = ("off", "vigilar", "on")

# Categorías de check(): las de SOSPECHA cuentan para el aviso de IP insistente; las demás son fallos de un jugador
# legítimo (tardó, reconectó, se canceló el duelo) y solo se apuntan.
SUSPECT = ("sin_reserva", "ip", "espectador")

Result = collections.namedtuple("Result", "ok ticket why cat fails alert")


class Ticket:
    __slots__ = ("sess", "pid", "peer", "kind", "ref", "t", "used", "conn")

    def __init__(self, sess, pid, peer, kind, ref, t, conn=None):
        self.sess, self.pid, self.peer, self.kind, self.ref, self.t = sess, pid, peer, kind, ref, t
        self.conn = conn
        self.used = False

    def __repr__(self):
        return f"<reserva {self.kind} pid={self.pid} ip={self.peer}{' usada' if self.used else ''}>"


def norm_mode(v, default="vigilar"):
    v = str(v if v is not None else "").strip().lower()
    return v if v in MODES else default


def decide(mode, res):
    """¿Se deja seguir el 0x6001? off/vigilar: siempre (como antes del 07-10); on: solo si la reserva vale."""
    return res.ok or norm_mode(mode) != "on"


class DuelTickets:
    def __init__(self, ttl=TTL_S, clock=time.time, insist_n=INSIST_N, insist_s=INSIST_S):
        self.ttl, self.clock, self.insist_n, self.insist_s = ttl, clock, insist_n, insist_s
        self.lock = threading.Lock()
        self.by_pid = {}                # pid -> Ticket (la última: el cliente solo usa la del último 0x4306)
        self.fails = {}                 # IP -> [horas de rechazos sospechosos] (ventana insist_s)
        self.alerted = {}               # IP -> hora del último aviso de «IP insistente»
        self.stats = collections.Counter()

    def adopt(self, old):
        """Recarga en caliente: se queda con las reservas y los contadores de la instancia vieja."""
        for k in ("by_pid", "fails", "alerted"):
            v = getattr(old, k, None)
            if isinstance(v, dict):
                getattr(self, k).update(v)
        st = getattr(old, "stats", None)
        if isinstance(st, collections.Counter):
            self.stats.update(st)
        return self

    def issue(self, sess, peer, kind="duelo", ref=None, conn=None):
        """Al mandar 0x4306 por la conexión `conn` de `sess` (peer = IP de ESA conexión). Sin sesión, sin pid o sin
        IP no hay reserva (su 0x6001 se rechazará). Devuelve la reserva o None."""
        pid = getattr(sess, "pid", None)
        if sess is None or pid is None or not peer or peer == "?":
            return None
        now = self.clock()
        t = Ticket(sess, pid, peer, kind, ref, now, conn)
        with self.lock:
            self.by_pid[pid] = t
            self.stats["emitidas"] += 1
            self._sweep(now)
        return t

    def check(self, pid, peer, alive=None, spectator=False):
        """0x6001 en el puerto de duelo. alive(ticket) -> None si la reserva sigue valiendo o el motivo si no
        (conexión de lobby cerrada, sesión liberada, duelo anulado). spectator: la conexión ya entró como
        espectador (0x6700): no puede pasar a jugador."""
        now = self.clock()
        with self.lock:
            t = self.by_pid.get(pid)
            if spectator:
                why, cat = "conexión de espectador (0x6700) que manda 0x6001", "espectador"
            elif t is None:
                why, cat = "sin reserva", "sin_reserva"
            elif t.peer != peer:                     # NO la gasta: si no, el atacante quemaría la de la víctima
                why, cat = f"IP distinta (la reserva es de {t.peer})", "ip"
            elif now - t.t > self.ttl:
                why, cat = f"reserva caducada ({int(now - t.t)} s > {int(self.ttl)} s)", "caducada"
            elif t.used:
                why, cat = "reserva ya usada (¿reconexión al mismo duelo?)", "reconexion"
            else:
                why, cat = None, None
        if why is None and alive is not None:        # fuera del candado: alive mira el hub y pvp_match
            try:
                gone = alive(t)
            except Exception as e:                   # noqa: BLE001
                gone = f"no se pudo comprobar la reserva ({e!r})"
            if gone:
                why, cat = str(gone), "anulada"
        with self.lock:
            if why is None:
                if t.used or self.by_pid.get(pid) is not t:   # otro 0x6001 se la llevó entretanto
                    why, cat = "reserva ya usada (¿reconexión al mismo duelo?)", "reconexion"
                else:
                    t.used = True
                    self.stats["ok"] += 1
                    return Result(True, t, "ok", "ok", 0, False)
            if cat == "anulada" and self.by_pid.get(pid) is t:
                del self.by_pid[pid]
            self.stats[cat] += 1
            n, alert = 0, False
            if cat in SUSPECT:
                lst = [x for x in self.fails.get(peer, []) if now - x < self.insist_s] + [now]
                self.fails[peer] = lst
                n = len(lst)
                last = self.alerted.get(peer)
                if n >= self.insist_n and (last is None or now - last >= self.insist_s):
                    self.alerted[peer] = now
                    alert = True
            return Result(False, t, why, cat, n, alert)

    def fails_of(self, peer):
        now = self.clock()
        with self.lock:
            return len([x for x in self.fails.get(peer, []) if now - x < self.insist_s])

    def drop_conn(self, conn):
        """La conexión por la que se mandó el 0x4306 se ha cerrado: fuera sus reservas sin usar."""
        with self.lock:
            for pid in [p for p, t in self.by_pid.items() if t.conn is conn and not t.used]:
                del self.by_pid[pid]

    def _sweep(self, now):
        for pid in [p for p, t in self.by_pid.items() if now - t.t > self.ttl]:
            del self.by_pid[pid]
        for ip in [ip for ip, lst in self.fails.items() if not lst or now - lst[-1] >= self.insist_s]:
            del self.fails[ip]
        for ip in [ip for ip, x in self.alerted.items() if now - x >= self.insist_s]:
            del self.alerted[ip]

    def sweep(self):
        with self.lock:
            self._sweep(self.clock())

    def snapshot(self):
        """Para la consola o el panel: reservas vivas, IPs con rechazos y contadores. Sin nada sensible."""
        now = self.clock()
        with self.lock:
            return {"reservas": [{"pid": t.pid, "ip": t.peer, "tipo": t.kind, "edad_s": int(now - t.t),
                                  "usada": t.used} for t in self.by_pid.values() if now - t.t <= self.ttl],
                    "rechazos_ip": {ip: len([x for x in lst if now - x < self.insist_s])
                                    for ip, lst in self.fails.items()},
                    "contadores": dict(self.stats)}
