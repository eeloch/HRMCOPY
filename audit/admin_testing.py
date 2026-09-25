"""Helpers for the admin tests of the money apps (not a test module itself)."""

from django.contrib.auth import get_user_model


def superuser(name="root-admin"):
    return get_user_model().objects.create_superuser(name, f"{name}@example.com", "pw")


def admin_messages(response):
    """The messages shown on a followed response, as plain text."""
    return [str(message) for message in response.context["messages"]]


def run_admin_action(client, url, action, ids, reason=None, confirm=True):
    """Drive a confirm-then-run admin action the way a browser does. Returns (confirmation_page, final_response)
    (final_response is None when confirm=False)."""
    ids = [str(pk) for pk in ids]
    page = client.post(url, {"action": action, "_selected_action": ids, "index": 0})
    if not confirm:
        return page, None
    data = {"action": action, "_selected_action": ids, "select_across": "0", "confirm": "yes"}
    if reason is not None:
        data["reason"] = reason
    return page, client.post(url, data, follow=True)
