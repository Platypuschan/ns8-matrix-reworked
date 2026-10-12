#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Write the configuration files of the Matrix module and talk to Synapse.

matrix_setup builds the content; this module writes it below the state
directory, runs the bridge images to complete their configuration and
creates the admin account.
"""

import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

import matrix_setup as setup

PORTS_ENV = "ports.env"
POSTGRES_ENV = "postgres.env"
ADMIN_FLAG = "admin-created"


def load_yaml(path):
    import yaml

    try:
        with open(path, "r", encoding="utf-8") as stream:
            return yaml.safe_load(stream) or {}
    except FileNotFoundError:
        return None


def dump_yaml(document):
    import yaml

    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000)


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


def render_synapse(settings, port_map, ldap=None, smarthost=None, state_dir="."):
    """Write the Synapse configuration; flag a restart if it changed."""
    setup.private_dir(os.path.join(state_dir, setup.SYNAPSE_DIR))
    setup.private_dir(os.path.join(state_dir, setup.APPSERVICE_DIR))
    changed = setup.write_json(
        os.path.join(state_dir, setup.SYNAPSE_DIR, "homeserver.yaml"),
        setup.homeserver_config(settings, port_map, ldap, smarthost),
    )
    changed |= setup.write_json(os.path.join(state_dir, setup.SYNAPSE_DIR, "log.config"), setup.log_config())
    changed |= setup.write_json(
        os.path.join(state_dir, setup.APPSERVICE_DIR, setup.DOUBLE_PUPPET_ID + ".yaml"),
        setup.double_puppet_registration(settings),
    )
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


def podman(*args, runner=subprocess.run):
    return runner(("podman",) + args, check=True)


def ensure_image(image, runner=subprocess.run):
    if runner(("podman", "image", "exists", image)).returncode != 0:
        podman("pull", image, runner=runner)


def generate_registration(key, state_dir=".", runner=subprocess.run):
    """Let the bridge complete its configuration and write its appservice
    registration (new as_token and hs_token)."""
    env = setup.bridge_unit_env(key)
    ensure_image(env["BRIDGE_IMAGE"], runner=runner)
    directory = os.path.abspath(os.path.join(state_dir, setup.BRIDGES_DIR, key))
    podman(
        "run", "--rm", "--network=none",
        "--volume", f"{directory}:/data:Z",
        "--entrypoint", env["BRIDGE_BINARY"],
        env["BRIDGE_IMAGE"],
        "-g", "-c", "/data/config.yaml", "-r", "/data/registration.yaml",
        runner=runner,
    )


def prepare_bridge(key, settings, port_map, state_dir=".", runner=subprocess.run):
    """Write the configuration of an enabled bridge and its registration
    for Synapse."""
    directory = os.path.join(state_dir, setup.BRIDGES_DIR, key)
    setup.private_dir(os.path.join(state_dir, setup.BRIDGES_DIR))
    setup.private_dir(directory)
    env = "".join(f"{name}={value}\n" for name, value in setup.bridge_unit_env(key).items())
    if setup.write_private(os.path.join(directory, "unit.env"), env):
        setup.flag_restart(f"bridge-{key}", state_dir)

    config_path = os.path.join(directory, "config.yaml")
    current = load_yaml(config_path)
    patched = setup.patch_bridge_config(current, key, settings, port_map)
    if patched != current:
        setup.write_private(config_path, dump_yaml(patched))
        setup.flag_restart(f"bridge-{key}", state_dir)

    registration_path = os.path.join(directory, "registration.yaml")
    if not os.path.exists(registration_path):
        generate_registration(key, state_dir, runner=runner)
        setup.flag_restart(f"bridge-{key}", state_dir)
    registration = setup.patch_registration(load_yaml(registration_path), key, port_map)
    content = dump_yaml(registration)
    setup.write_private(registration_path, content)
    if setup.write_private(os.path.join(state_dir, setup.APPSERVICE_DIR, key + ".yaml"), content):
        setup.flag_restart("synapse", state_dir)


def remove_disabled_registrations(settings, state_dir="."):
    """Synapse must not push to bridges that are not running. Their
    configuration and database are kept for a later re-enable."""
    directory = os.path.join(state_dir, setup.APPSERVICE_DIR)
    keep = {setup.DOUBLE_PUPPET_ID + ".yaml"} | {key + ".yaml" for key in settings["bridges"]}
    for name in os.listdir(directory):
        if name not in keep:
            os.remove(os.path.join(directory, name))
            setup.flag_restart("synapse", state_dir)


def write_postgres_env(settings, state_dir="."):
    # Used by initdb only; changing it later has no effect on the database
    content = f"POSTGRES_PASSWORD={settings['secrets']['postgres_password']}\n"
    return setup.write_private(os.path.join(state_dir, POSTGRES_ENV), content)


def render_all(settings, port_map, ldap=None, smarthost=None, state_dir=".", runner=subprocess.run):
    if write_ports(port_map, state_dir):
        # Every service reads its port at start
        for service in ["postgresql", "synapse", "wellknown", *setup.WEB_APPS]:
            setup.flag_restart(service, state_dir)
    write_postgres_env(settings, state_dir)
    render_synapse(settings, port_map, ldap, smarthost, state_dir)
    render_web(settings, state_dir)
    for key in settings["bridges"]:
        prepare_bridge(key, settings, port_map, state_dir, runner=runner)
    remove_disabled_registrations(settings, state_dir)


# --- Synapse API -------------------------------------------------------------

def http_json(url, data=None, timeout=10):
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
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
