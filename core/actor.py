"""Who is making the change that is being saved right now.

A model signal does not know the signed-in user, so each request records it here (the API puts the authenticated user in
after it has authenticated, the admin site through the middleware below) and clears it afterwards. A change made by a
command or a script has no actor, and the audit entry says so."""

from contextvars import ContextVar

_current = ContextVar("current_actor", default=None)


def set_actor(user):
    _current.set(user if getattr(user, "is_authenticated", False) else None)


def get_actor():
    return _current.get()


class ActorMiddleware:
    """Clears the recorded actor around every request, and records the session user for the admin site."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current.set(None)
        try:
            set_actor(getattr(request, "user", None))
            return self.get_response(request)
        finally:
            _current.reset(token)
