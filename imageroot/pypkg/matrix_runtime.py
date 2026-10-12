#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Write the configuration files of the Matrix module and talk to Synapse.

matrix_setup builds the content; this module writes it below the state
directory, keeps the registrations of bridge modules and
creates the admin account.
"""

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import matrix_setup as setup

PORTS_ENV = "ports.env"
POSTGRES_ENV = "postgres.env"
ADMIN_FLAG = "admin-created"


def write_public(path, content):
    """Write a file without secrets that a non-root process in a container
    must read (nginx, static-web-server); return True if it changed."""
    changed = setup.write_private(path, content)
    os.chmod(path, 0o644)
    return changed


def ldap_settings(domain):
    """Connection settings of the bound user domain, or None."""
    if not domain:
        return None
    from agent.ldapproxy import Ldapproxy

    proxy = Ldapproxy()
    found = proxy.get_domain(domain)
    if not found:
        # During a restore the domain can be missing; the user-domain-changed
        # event re-renders the configuration when it appears.
        return None
    found = dict(found)
    found["hidden_users_clause"] = proxy.get_ldap_users_search_filter_clause(domain)
    return found


def smarthost_settings():
    import agent

    try:
        # The local replica works also when the leader node is unreachable
        rdb = agent.redis_connect(use_replica=True)
        return agent.get_smarthost_settings(rdb)
    except Exception:
        return None


def write_ports(port_map, state_dir="."):
    lines = [f"{name.upper()}_PORT={port}" for name, port in port_map.items()]
    return write_public(os.path.join(state_dir, PORTS_ENV), "\n".join(lines) + "\n")


def read_appservices(state_dir="."):
    try:
        with open(os.path.join(state_dir, setup.APPSERVICES), "r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {}


def add_appservice(owner, registration, state_dir="."):
    """Store the registration of a bridge module (replacing its earlier one);
    raise setup.SettingsError for an invalid one. Call with the settings lock."""
    settings = setup.ensure_settings(state_dir)
    appservices = read_appservices(state_dir)
    clean = setup.validate_registration(registration, settings, owner, appservices)
    appservices[clean["id"]] = {"module": owner, "registration": clean}
    setup.write_json(os.path.join(state_dir, setup.APPSERVICES), appservices)
    return clean


def remove_appservice(owner, appservice_id, state_dir="."):
    """Remove a registration; a module can remove only its own. Return True
    if it existed. Call with the settings lock."""
    appservices = read_appservices(state_dir)
    entry = appservices.get(appservice_id)
    if entry is None:
        return False
    if owner is not None and entry["module"] != owner:
        raise setup.SettingsError("id", appservice_id, "appservice_of_other_module")
    del appservices[appservice_id]
    setup.write_json(os.path.join(state_dir, setup.APPSERVICES), appservices)
    return True


def render_appservices(settings, state_dir="."):
    """Write the registration files Synapse loads; return their IDs."""
    directory = os.path.join(state_dir, setup.APPSERVICE_DIR)
    setup.private_dir(directory)
    appservices = read_appservices(state_dir)
    changed = setup.write_json(
        os.path.join(directory, setup.DOUBLE_PUPPET_ID + ".yaml"),
        setup.double_puppet_registration(settings),
    )
    for appservice_id, entry in appservices.items():
        changed |= setup.write_json(os.path.join(directory, appservice_id + ".yaml"), entry["registration"])
    # Bridges that were removed, also the built-in bridges before 0.3.0
    keep = {setup.DOUBLE_PUPPET_ID + ".yaml"} | {key + ".yaml" for key in appservices}
    for name in os.listdir(directory):
        if name not in keep:
            os.remove(os.path.join(directory, name))
            changed = True
    if changed:
        setup.flag_restart("synapse", state_dir)
    return sorted(appservices)


def render_synapse(settings, port_map, ldap=None, smarthost=None, state_dir=".", udp_map=None):
    """Write the Synapse configuration; flag a restart if it changed."""
    setup.private_dir(os.path.join(state_dir, setup.SYNAPSE_DIR))
    appservice_ids = render_appservices(settings, state_dir)
    changed = setup.write_json(
        os.path.join(state_dir, setup.SYNAPSE_DIR, "homeserver.yaml"),
        setup.homeserver_config(settings, port_map, ldap, smarthost, udp_map, appservice_ids),
    )
    changed |= setup.write_json(os.path.join(state_dir, setup.SYNAPSE_DIR, "log.config"), setup.log_config())
    if changed:
        setup.flag_restart("synapse", state_dir)
    return changed


def render_web(settings, state_dir="."):
    directory = os.path.join(state_dir, setup.WEB_DIR)
    os.makedirs(directory, exist_ok=True)
    os.chmod(directory, 0o755)
    builders = {
        "element": setup.element_config,
        "cinny": setup.cinny_config,
        "ketesa": setup.ketesa_config,
    }
    for app, build in builders.items():
        content = json.dumps(build(settings), indent=2, sort_keys=True) + "\n"
        if write_public(os.path.join(directory, app + ".json"), content):
            setup.flag_restart(app, state_dir)
    # Served by wellknown.service when the server name is delegated; the
    # files are read at each request, no restart needed.
    wellknown = os.path.join(directory, "wellknown", ".well-known", "matrix")
    os.makedirs(wellknown, exist_ok=True)
    for path in (os.path.join(directory, "wellknown"), os.path.dirname(wellknown), wellknown):
        os.chmod(path, 0o755)
    for name, document in setup.wellknown_documents(settings).items():
        write_public(os.path.join(wellknown, name), json.dumps(document) + "\n")


CALLS_DIR = "calls"


def render_calls(settings, port_map, udp_map, state_dir="."):
    """Write the coturn, LiveKit and JWT service configuration. Without
    UDP ports nothing is written; the services stay disabled."""
    if not udp_map:
        return
    directory = os.path.join(state_dir, CALLS_DIR)
    setup.private_dir(directory)
    files = {
        "coturn": ("turnserver.conf", setup.coturn_config(settings, udp_map)),
        "livekit": ("livekit.yaml", json.dumps(setup.livekit_config(settings, port_map, udp_map), indent=2) + "\n"),
        "lk-jwt": ("lk-jwt.env", "".join(f"{k}={v}\n" for k, v in setup.lk_jwt_env(settings, port_map).items())),
    }
    unit_env = "".join(f"{k}={v}\n" for k, v in setup.lk_jwt_unit_env(settings).items())
    if write_public(os.path.join(directory, "lk-jwt-unit.env"), unit_env):
        setup.flag_restart("lk-jwt", state_dir)
    for service, (name, content) in files.items():
        if setup.write_private(os.path.join(directory, name), content):
            setup.flag_restart(service, state_dir)


def write_postgres_env(settings, state_dir="."):
    # Used by initdb only; changing it later has no effect on the database
    content = f"POSTGRES_PASSWORD={settings['secrets']['postgres_password']}\n"
    return setup.write_private(os.path.join(state_dir, POSTGRES_ENV), content)


def render_all(settings, port_map, ldap=None, smarthost=None, state_dir=".", udp_map=None):
    if write_ports(port_map, state_dir):
        # Every service reads its port at start
        for service in ["postgresql", "synapse", "wellknown", "livekit", "lk-jwt", *setup.WEB_APPS]:
            setup.flag_restart(service, state_dir)
    write_postgres_env(settings, state_dir)
    render_synapse(settings, port_map, ldap, smarthost, state_dir, udp_map)
    render_web(settings, state_dir)
    render_calls(settings, port_map, udp_map, state_dir)
    env = "".join(f"{k}={v}\n" for k, v in setup.compressor_env(settings, port_map).items())
    setup.write_private(os.path.join(state_dir, "compressor.env"), env)


# --- Synapse API -------------------------------------------------------------

def http_json(url, data=None, timeout=10, token=None, method=None):
    body = None if data is None else json.dumps(data).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.load(error)
        except ValueError:
            return error.code, {}


def wait_for_synapse(port, timeout=300, opener=urllib.request.urlopen, sleep=time.sleep):
    deadline = time.monotonic() + timeout
    while True:
        try:
            with opener(f"http://127.0.0.1:{port}/health", timeout=5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        if time.monotonic() > deadline:
            raise TimeoutError(f"Synapse did not answer on port {port}")
        sleep(2)


def registration_mac(secret, nonce, user, password, admin=True):
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha1)
    for part in (nonce, user, password):
        mac.update(part.encode())
        mac.update(b"\x00")
    mac.update(b"admin" if admin else b"notadmin")
    return mac.hexdigest()


def register_user(settings, port_map, localpart, password, admin, request=http_json):
    """Register a local account with the shared-secret registration API;
    an existing account is fine."""
    base = f"http://127.0.0.1:{port_map['synapse']}/_synapse/admin/v1/register"
    status, answer = request(base)
    if status != 200:
        raise RuntimeError(f"Synapse registration nonce request failed: HTTP {status}")
    body = {
        "nonce": answer["nonce"],
        "username": localpart,
        "password": password,
        "admin": admin,
        "mac": registration_mac(
            settings["secrets"]["registration_shared_secret"], answer["nonce"], localpart, password, admin
        ),
    }
    status, answer = request(base, body)
    if status != 200 and answer.get("errcode") != "M_USER_IN_USE":
        raise RuntimeError(f"Creating the account {localpart} failed: HTTP {status} {answer.get('errcode', '')}")


DRAUPNIR_STATE = os.path.join(setup.DRAUPNIR_DIR, "provisioned.json")


def read_draupnir_state(state_dir="."):
    try:
        with open(os.path.join(state_dir, DRAUPNIR_STATE), "r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {}


def provision_draupnir(settings, port_map, state_dir=".", request=http_json):
    """Create the Draupnir bot account (server admin, for its admin
    commands), lift its rate limits, create its management room with the
    module admin as manager and write its configuration. Each step is kept
    and done only once."""
    setup.private_dir(os.path.join(state_dir, setup.DRAUPNIR_DIR))
    setup.private_dir(os.path.join(state_dir, setup.DRAUPNIR_DIR, "config"))
    state = read_draupnir_state(state_dir)
    base = f"http://127.0.0.1:{port_map['synapse']}"
    server_name = settings["server_name"]
    bot = setup.user_id(setup.DRAUPNIR_LOCALPART, server_name)
    password = settings["secrets"]["draupnir_password"]

    def save():
        setup.write_json(os.path.join(state_dir, DRAUPNIR_STATE), state)

    if not state.get("access_token"):
        register_user(settings, port_map, setup.DRAUPNIR_LOCALPART, password, True, request=request)
        status, answer = request(f"{base}/_matrix/client/v3/login", {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": setup.DRAUPNIR_LOCALPART},
            "password": password,
            "initial_device_display_name": "Draupnir",
        })
        if status != 200:
            raise RuntimeError(f"Draupnir login failed: HTTP {status} {answer.get('errcode', '')}")
        state["access_token"] = answer["access_token"]
        save()
    token = state["access_token"]
    if not state.get("ratelimit_override"):
        # Required: Draupnir acts on many rooms at once and crashes when
        # Synapse rate-limits it
        quoted = urllib.parse.quote(bot)
        status, answer = request(f"{base}/_synapse/admin/v1/users/{quoted}/override_ratelimit",
                                 {"messages_per_second": 0, "burst_count": 0}, token=token)
        if status != 200:
            raise RuntimeError(f"Draupnir rate limit override failed: HTTP {status} {answer.get('errcode', '')}")
        state["ratelimit_override"] = True
        save()
    if not state.get("management_room"):
        status, answer = request(f"{base}/_matrix/client/v3/createRoom", {
            "preset": "private_chat",
            "name": "Draupnir management",
            "topic": "Commands for the Draupnir moderation bot, see !draupnir help",
            "invite": [setup.user_id(setup.ADMIN_LOCALPART, server_name)],
        }, token=token)
        if status != 200:
            raise RuntimeError(f"Creating the Draupnir management room failed: HTTP {status}")
        state["management_room"] = answer["room_id"]
        save()
    config = setup.draupnir_config(settings, port_map, token, state["management_room"])
    if setup.write_json(os.path.join(state_dir, setup.DRAUPNIR_DIR, "config", "production.yaml"), config):
        setup.flag_restart("draupnir", state_dir)
    return state


def publish_changed(settings):
    """Tell bridge modules to read get-matrix-info again (event
    matrix-changed): the Synapse port or server settings may have changed."""
    import agent

    rdb = agent.redis_connect(privileged=True)
    rdb.publish(f"{os.environ['AGENT_ID']}/event/matrix-changed", json.dumps({
        "module_id": os.environ["MODULE_ID"],
        "node": int(os.environ.get("NODE_ID", "0") or 0),
        "server_name": settings.get("server_name", ""),
    }))


def ensure_admin(settings, port_map, state_dir=".", request=http_json):
    """Create the local server admin account once, with the shared-secret
    registration API (no password on any command line)."""
    flag = os.path.join(state_dir, ADMIN_FLAG)
    if os.path.exists(flag):
        return False
    base = f"http://127.0.0.1:{port_map['synapse']}/_synapse/admin/v1/register"
    status, answer = request(base)
    if status != 200:
        raise RuntimeError(f"Synapse registration nonce request failed: HTTP {status}")
    password = settings["secrets"]["admin_password"]
    body = {
        "nonce": answer["nonce"],
        "username": setup.ADMIN_LOCALPART,
        "password": password,
        "admin": True,
        "mac": registration_mac(
            settings["secrets"]["registration_shared_secret"],
            answer["nonce"], setup.ADMIN_LOCALPART, password,
        ),
    }
    status, answer = request(base, body)
    if status != 200 and answer.get("errcode") != "M_USER_IN_USE":
        raise RuntimeError(f"Creating the admin account failed: HTTP {status} {answer.get('errcode', '')}")
    with open(flag, "w", encoding="utf-8") as stream:
        stream.write(setup.user_id(setup.ADMIN_LOCALPART, settings["server_name"]) + "\n")
    return True
