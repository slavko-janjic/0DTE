"""Single-instance guard via an exclusive localhost socket bind.

Why a socket and not a lock file: the worker was orphaned repeatedly (a task
stop that left the python child alive), and every "restart" then ran a SECOND
worker beside the ghost. A PID/lock file survives a crash and needs cleanup
logic that itself can be wrong. A bound socket is released by the OS the instant
the process dies - crash, kill, anything - so there is never a stale lock to
reap, and a second instance simply fails to bind and exits.

Bind (no SO_REUSEADDR) is exclusive on both Windows and Linux: the second
process gets an "address in use" error rather than silently sharing the port.
"""
import socket

# Arbitrary high port in the dynamic range, unlikely to collide. Only ever bound,
# never connected to - the bind itself is the mutex.
_DEFAULT_PORT = 47632


class AlreadyRunning(RuntimeError):
    """Raised when another instance already holds the lock."""


def acquire(port: int = _DEFAULT_PORT) -> socket.socket:
    """Acquire the single-instance lock, returning the socket that holds it.

    The caller MUST keep the returned socket referenced for the whole process
    lifetime (garbage-collecting it releases the lock). Raises AlreadyRunning if
    another instance holds it.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError as exc:
        sock.close()
        raise AlreadyRunning(
            f"another instance already holds 127.0.0.1:{port} ({exc})"
        ) from exc
    return sock
