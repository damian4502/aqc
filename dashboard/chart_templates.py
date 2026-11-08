"""Named selection templates stored in the Django session.

A template keeps rooms, parameters, individual series, and differences.
It does not store the time range.
"""

from __future__ import annotations

MAX_TEMPLATES = 30


def clean_template_name(name) -> str:
    return " ".join(str(name or "").split())[:60]


def list_templates(session, key) -> list:
    raw = session.get(key) or []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("name")]


def save_named_template(session, key, name, payload) -> str:
    name = clean_template_name(name)
    if not name:
        return ""
    items = [item for item in list_templates(session, key) if item.get("name") != name]
    items.insert(0, {"name": name, **payload})
    session[key] = items[:MAX_TEMPLATES]
    session.modified = True
    return name


def delete_named_template(session, key, name) -> None:
    name = clean_template_name(name)
    session[key] = [item for item in list_templates(session, key) if item.get("name") != name]
    session.modified = True


def find_named_template(session, key, name):
    name = clean_template_name(name)
    if not name:
        return None
    for item in list_templates(session, key):
        if item.get("name") == name:
            return item
    return None


def url_without(request, extra_drop=()):
    """Current URL without template action parameters."""
    query = request.GET.copy()
    for key in (
        "template_action",
        "template_name",
        "template_delete",
        "load_template",
        *extra_drop,
    ):
        query.pop(key, None)
    encoded = query.urlencode()
    return request.path + (f"?{encoded}" if encoded else "")


def url_for_selection(request, rooms, parameters, series_values, diff_values, keep_keys):
    """URL that restores a saved selection and keeps the current time controls."""
    from django.http import QueryDict

    query = QueryDict(mutable=True)
    for room_id in rooms:
        query.appendlist("room", str(room_id))
    for parameter_id in parameters:
        query.appendlist("parameter", str(parameter_id))
    for value in series_values:
        query.appendlist("series", value)
    for value in diff_values:
        query.appendlist("diff", value)
    for key in keep_keys:
        value = request.GET.get(key)
        if value not in (None, ""):
            query[key] = value
    query["selection"] = "1"
    encoded = query.urlencode()
    return request.path + (f"?{encoded}" if encoded else "")


def remember_active_template(session, key, name) -> str:
    """Remember which template name is being edited on this page."""
    name = clean_template_name(name)
    session[key] = name
    session.modified = True
    return name


def active_template_name(request, key) -> str:
    """Name to show in the template field.

    A submitted field wins, including a cleared one. Otherwise the last
    loaded or saved name is restored.
    """
    if "template_name" in request.GET:
        return remember_active_template(request.session, key, request.GET.get("template_name"))
    return clean_template_name(request.session.get(key))

