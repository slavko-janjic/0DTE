"""Single-instance socket lock: the second worker must be refused."""
import socket

import pytest

import single_instance

# use a distinct test port so a running real worker can't interfere
_PORT = 47988


def test_second_acquire_is_refused_while_first_holds():
    first = single_instance.acquire(_PORT)
    try:
        with pytest.raises(single_instance.AlreadyRunning):
            single_instance.acquire(_PORT)
    finally:
        first.close()


def test_lock_is_released_when_the_socket_closes():
    """A crash frees the OS socket instantly - no stale lock to clean up. Closing
    the socket models the process dying."""
    first = single_instance.acquire(_PORT)
    first.close()
    # must be re-acquirable immediately
    second = single_instance.acquire(_PORT)
    second.close()


def test_acquire_returns_a_socket_that_holds_the_port():
    sock = single_instance.acquire(_PORT)
    try:
        assert isinstance(sock, socket.socket)
        # the port really is bound - a fresh bind fails
        clash = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(OSError):
            clash.bind(("127.0.0.1", _PORT))
        clash.close()
    finally:
        sock.close()
