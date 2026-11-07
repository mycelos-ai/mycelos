from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from mycelos.chat.service import ChatService


def test_concurrent_requests_keep_their_context():
    service = ChatService(SimpleNamespace())
    barrier = Barrier(2)
    def request(session_id):
        service._current_session_id = session_id
        service._current_user_id = session_id + "-user"
        service._pending_events = [session_id]
        barrier.wait(timeout=5)
        return service._current_session_id, service._current_user_id, service._pending_events
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(request, "a")
        b = pool.submit(request, "b")
        assert a.result() == ("a", "a-user", ["a"])
        assert b.result() == ("b", "b-user", ["b"])


def test_permission_prompts_remain_with_their_chat():
    service = ChatService(SimpleNamespace())
    service._current_session_id = "a"
    service._pending_permission = {"session_id": "a", "tool": "first"}
    service._current_session_id = "b"
    assert service._pending_permission is None
    service._pending_permission = {"session_id": "b", "tool": "second"}
    service._current_session_id = "a"
    assert service._pending_permission["tool"] == "first"
