#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Settings, secrets and generated configuration of the Matrix module.

The module keeps its settings and secrets in state/matrix.json (mode 600)
and renders every configuration file from them:

- synapse/homeserver.yaml, synapse/log.config and synapse/appservices/*.yaml
  (JSON is valid YAML, so Synapse reads the JSON output directly),
- web/element.json, web/cinny.json, web/ketesa.json for the web apps,
- bridges/<key>/config.yaml, the configuration of each mautrix bridge.

The bridges own most of their configuration: they complete a small seed
file with their defaults and generated secrets on the first start, and keep
it up to date when a new version adds options. The module only sets the
keys it is responsible for (addresses, ports, database, permissions, ...)
and leaves everything else as the bridge wrote it.
"""

import contextlib
import copy
import fcntl
import json
import os
import re
import secrets
import string

SETTINGS = "matrix.json"
SYNAPSE_DIR = "synapse"
APPSERVICE_DIR = os.path.join(SYNAPSE_DIR, "appservices")
WEB_DIR = "web"
BRIDGES_DIR = "bridges"
# Set when a file that a service reads only at start changed; 80start_services
# and update-module.d restart the service and remove the flag. A flag stays
# across a failed task, so the next run still restarts the service.
RESTART_FLAG = "restart-needed-{}"

# Local Synapse account with server admin rights, created by the module.
ADMIN_LOCALPART = "matrixadmin"
DOUBLE_PUPPET_ID = "doublepuppet"

# Paths inside the Synapse container
SYNAPSE_CONFIG = "/data/config"
SYNAPSE_DATA = "/data"

# Allocated TCP ports (TCP_PORTS_RANGE), by position. New services must be
# appended: the positions of existing services must never change.
PORT_NAMES = [
    "synapse",
    "postgres",
    "element",
    "cinny",
    "ketesa",
    "whatsapp",
    "signal",
    "telegram",
    "meta",
    "gmessages",
    "slack",
    "discord",
    "bluesky",
    "twitter",
    "linkedin",
    "wellknown",
]

# mautrix bridges. "legacy" bridges still use the configuration layout of
# mautrix-go before bridgev2 (database below appservice, double puppeting
# and encryption below bridge).
BRIDGES = {
    "whatsapp": {"name": "WhatsApp", "image": "dock.mau.dev/mautrix/whatsapp:v0.2609.0"},
    "signal": {"name": "Signal", "image": "dock.mau.dev/mautrix/signal:v0.2609.0"},
    "telegram": {"name": "Telegram", "image": "dock.mau.dev/mautrix/telegram:v0.2609.0"},
    "meta": {"name": "Messenger / Instagram", "image": "dock.mau.dev/mautrix/meta:v0.2609.0"},
    "gmessages": {"name": "Google Messages", "image": "dock.mau.dev/mautrix/gmessages:v0.2609.0"},
    "slack": {"name": "Slack", "image": "dock.mau.dev/mautrix/slack:v0.2609.1"},
    "discord": {"name": "Discord", "image": "dock.mau.dev/mautrix/discord:v0.7.7", "legacy": True},
    "bluesky": {"name": "Bluesky", "image": "dock.mau.dev/mautrix/bluesky:v0.2510.0"},
    "twitter": {"name": "X (Twitter)", "image": "dock.mau.dev/mautrix/twitter:v0.2609.0"},
    "linkedin": {"name": "LinkedIn", "image": "dock.mau.dev/mautrix/linkedin:v0.2609.0"},
}

WEB_APPS = ("element", "cinny", "ketesa")

HOST_PATTERN = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SettingsError(Exception):
    def __init__(self, field, value, error):
        super().__init__(f"{field}: {error}")
        self.field = field
        self.value = value
        self.error = error


def new_secret(length=48):
    return secrets.token_urlsafe(length)


def new_password(length=20):
    """Letters and digits only: typed by hand, never mistaken for an option."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def write_private(path, content):
    """Atomically write a file readable by the module user only; return
    True if its content changed."""
    try:
        with open(path, "r", encoding="utf-8") as stream:
            if stream.read() == content:
                os.chmod(path, 0o600)
                return False
    except FileNotFoundError:
        pass
    temporary = path + ".tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return True


def write_json(path, data):
    return write_private(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def flag_restart(service, state_dir="."):
    with open(os.path.join(state_dir, RESTART_FLAG.format(service)), "w", encoding="utf-8"):
        pass


@contextlib.contextmanager
def settings_lock(state_dir="."):
    """Serialize settings changes between concurrent tasks."""
    descriptor = os.open(os.path.join(state_dir, SETTINGS + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def read_settings(state_dir="."):
    try:
        with open(os.path.join(state_dir, SETTINGS), "r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {}


DEFAULTS = {
    "server_name": "",
    "host": "",
    "lets_encrypt": False,
    "http2https": True,
    "user_domain": "",
    "federation": True,
    "wellknown_on_server_name": False,
    "element_host": "",
    "cinny_host": "",
    "ketesa_host": "",
    "mail_from": "",
    "max_upload_mb": 100,
    "bridges": [],
    "telegram_api_id": "",
    "telegram_api_hash": "",
}

SECRET_KEYS = (
    "postgres_password",
    "registration_shared_secret",
    "macaroon_secret_key",
    "form_secret",
    "doublepuppet_as_token",
    "doublepuppet_hs_token",
    "admin_password",
)


def ensure_settings(state_dir="."):
    """Return the settings with all defaults and secrets; secrets are created
    once and kept, also across restores and updates."""
    settings = read_settings(state_dir)
    changed = False
    for key, value in DEFAULTS.items():
        if key not in settings:
            settings[key] = copy.deepcopy(value)
            changed = True
    stored = settings.setdefault("secrets", {})
    for key in SECRET_KEYS:
        if not stored.get(key):
            stored[key] = new_password() if key == "admin_password" else new_secret()
            changed = True
    path = os.path.join(state_dir, SETTINGS)
    if changed:
        write_json(path, settings)
    else:
        os.chmod(path, 0o600)
    return settings


def check_host(field, value, required=False):
    if not value:
        if required:
            raise SettingsError(field, value, "required")
        return ""
    if not HOST_PATTERN.fullmatch(value):
        raise SettingsError(field, value, "invalid_hostname")
    return value


def own_hosts(settings):
    """Host names that the module routes already."""
    return {settings.get(field) for field in ("host",) + tuple(f"{app}_host" for app in WEB_APPS)} - {"", None}


def apply_request(data, state_dir=".", write=True):
    """Validate a configure-module request and store the settings.

    Optional fields missing from the request keep their value: clone and
    restore send the stored settings, the UI always sends all of them.
    Raises SettingsError for invalid input without changing anything.
    """
    settings = ensure_settings(state_dir)
    new = copy.deepcopy(settings)
    for key in DEFAULTS:
        if key in data and key != "telegram_api_hash":
            new[key] = data[key]
    if data.get("telegram_api_hash"):
        new["telegram_api_hash"] = data["telegram_api_hash"]

    new["host"] = check_host("host", (new["host"] or "").strip().lower(), required=True)
    server_name = (new["server_name"] or "").strip().lower() or new["host"]
    check_host("server_name", server_name)
    if settings["server_name"] and server_name != settings["server_name"]:
        # User IDs, rooms and the database belong to the server name; Synapse
        # refuses to start with a different one.
        raise SettingsError("server_name", server_name, "server_name_immutable")
    new["server_name"] = server_name

    hosts = {"host": new["host"]}
    for app in WEB_APPS:
        field = f"{app}_host"
        new[field] = check_host(field, (new[field] or "").strip().lower())
        if new[field]:
            if new[field] in hosts.values():
                raise SettingsError(field, new[field], "hostname_already_used")
            hosts[field] = new[field]

    new["mail_from"] = (new["mail_from"] or "").strip()
    if new["mail_from"] and not EMAIL_PATTERN.fullmatch(new["mail_from"]):
        raise SettingsError("mail_from", new["mail_from"], "invalid_email")

    try:
        new["max_upload_mb"] = int(new["max_upload_mb"])
    except (TypeError, ValueError):
        raise SettingsError("max_upload_mb", new["max_upload_mb"], "invalid_number") from None
    if not 1 <= new["max_upload_mb"] <= 10240:
        raise SettingsError("max_upload_mb", new["max_upload_mb"], "invalid_number")

    bridges = []
    for key in new["bridges"] or []:
        if key not in BRIDGES:
            raise SettingsError("bridges", key, "unknown_bridge")
        if key not in bridges:
            bridges.append(key)
    # Keep the order of BRIDGES, so equal selections give equal files
    new["bridges"] = [key for key in BRIDGES if key in bridges]

    new["telegram_api_id"] = str(new["telegram_api_id"] or "").strip()
    if new["telegram_api_id"] and not new["telegram_api_id"].isdigit():
        raise SettingsError("telegram_api_id", new["telegram_api_id"], "invalid_number")
    if "telegram" in new["bridges"]:
        if not new["telegram_api_id"]:
            raise SettingsError("telegram_api_id", "", "telegram_credentials_required")
        if not new["telegram_api_hash"]:
            raise SettingsError("telegram_api_hash", "", "telegram_credentials_required")

    for key in ("lets_encrypt", "http2https", "federation", "wellknown_on_server_name"):
        new[key] = bool(new[key])
    new["user_domain"] = (new["user_domain"] or "").strip()

    if write:
        write_json(os.path.join(state_dir, SETTINGS), new)
    return new


def ports(environ=os.environ):
    """Return the allocated ports by service name."""
    allocated = []
    span = environ.get("TCP_PORTS_RANGE", "")
    if span:
        first, last = (int(item) for item in span.split("-"))
        allocated = list(range(first, last + 1))
    elif environ.get("TCP_PORTS"):
        allocated = [int(item) for item in environ["TCP_PORTS"].split(",")]
    elif environ.get("TCP_PORT"):
        allocated = [int(environ["TCP_PORT"])]
    if len(allocated) < len(PORT_NAMES):
        raise RuntimeError(f"{len(PORT_NAMES)} TCP ports needed, {len(allocated)} allocated")
    return dict(zip(PORT_NAMES, allocated))


def user_id(localpart, server_name):
    return f"@{localpart}:{server_name}"


def regex_quote(text):
    return re.escape(text)


def database_uri(settings, port_map, database):
    password = settings["secrets"]["postgres_password"]
    return f"postgres://postgres:{password}@127.0.0.1:{port_map['postgres']}/{database}?sslmode=disable"


def bridge_database(key):
    return f"mautrix_{key}"


# --- Synapse -----------------------------------------------------------------

def ldap_module(ldap):
    """Synapse LDAP login through the NS8 LDAP proxy of the bound domain."""
    if ldap["schema"] == "ad":
        base = ldap["base_dn"]
        attributes = {"uid": "sAMAccountName", "mail": "mail", "name": "displayName"}
        # Enabled user accounts only
        user_filter = (
            "(&(objectCategory=person)(objectClass=user)"
            "(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
            + ldap.get("hidden_users_clause", "") + ")"
        )
    else:
        base = "ou=People," + ldap["base_dn"]
        attributes = {"uid": "uid", "mail": "mail", "name": "cn"}
        user_filter = ldap.get("hidden_users_clause", "")
    config = {
        "enabled": True,
        "uri": f"ldap://{ldap['host']}:{ldap['port']}",
        "start_tls": False,
        "base": base,
        "attributes": attributes,
        "bind_dn": ldap["bind_dn"],
        "bind_password": ldap["bind_password"],
    }
    if user_filter:
        config["filter"] = user_filter
    return {"module": "ldap_auth_provider.LdapAuthProviderModule", "config": config}


def email_section(settings, smarthost):
    if not smarthost or not smarthost.get("enabled") or not smarthost.get("host"):
        return None
    sender = settings["mail_from"] or f"noreply@{settings['server_name']}"
    section = {
        "smtp_host": smarthost["host"],
        "smtp_port": int(smarthost["port"]),
        "notif_from": f"%(app)s <{sender}>",
        "app_name": "Matrix",
        "enable_notifs": True,
        "notif_for_new_users": True,
    }
    if smarthost.get("username"):
        section["smtp_user"] = smarthost["username"]
        section["smtp_pass"] = smarthost.get("password", "")
    encrypt = smarthost.get("encrypt_smtp", "none")
    if encrypt == "tls":
        section["force_tls"] = True
    elif encrypt == "starttls":
        section["require_transport_security"] = True
    else:
        section["enable_tls"] = False
    if settings["element_host"]:
        section["client_base_url"] = f"https://{settings['element_host']}"
    return section


def delegated(settings):
    """True if the server name differs from the homeserver host: other
    servers and clients find the host through /.well-known/matrix on the
    server name domain."""
    return settings["server_name"] != settings["host"]


def serves_wellknown(settings):
    """True if the module routes /.well-known/matrix of the server name."""
    return delegated(settings) and settings["wellknown_on_server_name"]


def wellknown_documents(settings):
    """/.well-known/matrix/server and /client for the server name domain."""
    return {
        "server": {"m.server": f"{settings['host']}:443"},
        "client": {"m.homeserver": {"base_url": f"https://{settings['host']}"}},
    }


def appservice_files(settings):
    files = [f"{SYNAPSE_CONFIG}/appservices/{DOUBLE_PUPPET_ID}.yaml"]
    files += [f"{SYNAPSE_CONFIG}/appservices/{key}.yaml" for key in settings["bridges"]]
    return files


def homeserver_config(settings, port_map, ldap=None, smarthost=None):
    stored = settings["secrets"]
    resources = ["client", "federation"] if settings["federation"] else ["client"]
    config = {
        "server_name": settings["server_name"],
        "public_baseurl": f"https://{settings['host']}/",
        "pid_file": f"{SYNAPSE_DATA}/homeserver.pid",
        "listeners": [{
            "port": port_map["synapse"],
            "bind_addresses": ["127.0.0.1"],
            "type": "http",
            "tls": False,
            # Traefik terminates TLS and sets X-Forwarded-For
            "x_forwarded": True,
            "resources": [{"names": resources, "compress": False}],
        }],
        "database": {
            "name": "psycopg2",
            "txn_limit": 10000,
            "args": {
                "user": "postgres",
                "password": stored["postgres_password"],
                "dbname": "synapse",
                "host": "127.0.0.1",
                "port": port_map["postgres"],
                "cp_min": 5,
                "cp_max": 10,
            },
        },
        "log_config": f"{SYNAPSE_CONFIG}/log.config",
        "media_store_path": f"{SYNAPSE_DATA}/media_store",
        "max_upload_size": f"{settings['max_upload_mb']}M",
        "signing_key_path": f"{SYNAPSE_DATA}/signing.key",
        "report_stats": False,
        "enable_registration": False,
        "registration_shared_secret": stored["registration_shared_secret"],
        "macaroon_secret_key": stored["macaroon_secret_key"],
        "form_secret": stored["form_secret"],
        "url_preview_enabled": False,
        "presence": {"enabled": True},
        "password_config": {"enabled": True, "localdb_enabled": True},
        "app_service_config_files": appservice_files(settings),
        "suppress_key_server_warning": True,
    }
    if settings["federation"]:
        if not delegated(settings):
            # Synapse answers {"m.server": "<server_name>:443"}: other
            # servers find the federation API on port 443 of the host. A
            # delegated server name gets its own document, see
            # wellknown_documents().
            config["serve_server_wellknown"] = True
        config["trusted_key_servers"] = [{"server_name": "matrix.org"}]
    else:
        config["federation_domain_whitelist"] = []
        config["trusted_key_servers"] = []
    if ldap:
        config["modules"] = [ldap_module(ldap)]
    email = email_section(settings, smarthost)
    if email:
        config["email"] = email
    return config


def log_config():
    return {
        "version": 1,
        "formatters": {"precise": {"format": "%(name)s - %(lineno)d - %(levelname)s - %(request)s - %(message)s"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "precise"}},
        "loggers": {"synapse.storage.SQL": {"level": "INFO"}},
        "root": {"level": "INFO", "handlers": ["console"]},
        "disable_existing_loggers": False,
    }


def double_puppet_registration(settings):
    stored = settings["secrets"]
    return {
        "id": DOUBLE_PUPPET_ID,
        # No URL: Synapse never sends events to this appservice. It only
        # lets the bridges log in as local users for double puppeting.
        "url": None,
        "as_token": stored["doublepuppet_as_token"],
        "hs_token": stored["doublepuppet_hs_token"],
        "sender_localpart": "_doublepuppet",
        "rate_limited": False,
        "namespaces": {
            "users": [{"regex": f"@.*:{regex_quote(settings['server_name'])}", "exclusive": False}],
            "rooms": [],
            "aliases": [],
        },
    }


# --- Web apps ------------------------------------------------------------------

def element_config(settings):
    return {
        "default_server_name": settings["server_name"],
        "default_server_config": {
            "m.homeserver": {
                "base_url": f"https://{settings['host']}",
                "server_name": settings["server_name"],
            },
        },
        "disable_custom_urls": False,
        "disable_guests": True,
        "brand": "Element",
        "default_federate": settings["federation"],
        "default_theme": "light",
        "room_directory": {"servers": [settings["server_name"]]},
        "setting_defaults": {"UIFeature.registration": False},
        "show_labs_settings": False,
    }


def cinny_config(settings):
    return {
        "defaultHomeserver": 0,
        # Cinny looks up /.well-known/matrix/client on the given name; the
        # homeserver host always answers it.
        "homeserverList": [settings["host"]],
        "allowCustomHomeservers": True,
        "featuredCommunities": {"openAsDefault": False, "spaces": [], "rooms": [], "servers": []},
        "hashRouter": {"enabled": False, "basename": "/"},
    }


def ketesa_config(settings):
    # Only the own homeserver can be administered from this instance
    return {"restrictBaseUrl": f"https://{settings['host']}"}


# --- Bridges -----------------------------------------------------------------

def set_path(document, path, value):
    """Set document[a][b]... = value, creating missing mappings."""
    node = document
    for key in path[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    node[path[-1]] = value


def bridge_settings(key, settings, port_map):
    """The (path, value) pairs of a bridge configuration owned by the module."""
    server_name = settings["server_name"]
    legacy = BRIDGES[key].get("legacy", False)
    database = {"type": "postgres", "uri": database_uri(settings, port_map, bridge_database(key))}
    permissions = {
        server_name: "user",
        user_id(ADMIN_LOCALPART, server_name): "admin",
    }
    double_puppet = {server_name: "as_token:" + settings["secrets"]["doublepuppet_as_token"]}
    values = [
        (("homeserver", "address"), f"http://127.0.0.1:{port_map['synapse']}"),
        (("homeserver", "domain"), server_name),
        (("appservice", "address"), f"http://127.0.0.1:{port_map[key]}"),
        (("appservice", "hostname"), "127.0.0.1"),
        (("appservice", "port"), port_map[key]),
        (("bridge", "permissions"), permissions),
        # The journal adds time stamps; no log files in the volume
        (("logging",), {"min_level": "info", "writers": [{"type": "stdout", "format": "pretty"}]}),
    ]
    if legacy:
        values += [
            (("appservice", "database", "type"), database["type"]),
            (("appservice", "database", "uri"), database["uri"]),
            (("bridge", "login_shared_secret_map"), double_puppet),
            (("bridge", "double_puppet_server_map"), {}),
            # Element creates encrypted direct chats; the bot must read them
            (("bridge", "encryption", "allow"), True),
            (("bridge", "federate_rooms"), settings["federation"]),
        ]
    else:
        values += [
            (("database", "type"), database["type"]),
            (("database", "uri"), database["uri"]),
            (("double_puppet", "secrets"), double_puppet),
            (("double_puppet", "servers"), {}),
            (("encryption", "allow"), True),
            (("matrix", "federate_rooms"), settings["federation"]),
        ]
    if key == "telegram":
        values += [
            (("network", "api_id"), int(settings["telegram_api_id"])),
            (("network", "api_hash"), settings["telegram_api_hash"]),
        ]
    return values


def patch_bridge_config(document, key, settings, port_map):
    """Apply the module-owned settings to a bridge configuration."""
    document = copy.deepcopy(document) if document else {}
    for path, value in bridge_settings(key, settings, port_map):
        set_path(document, path, value)
    return document


def patch_registration(document, key, port_map):
    """Ports can change (restore, clone): keep the push URL current."""
    document = copy.deepcopy(document)
    document["url"] = f"http://127.0.0.1:{port_map[key]}"
    return document


def bridge_unit_env(key):
    image = BRIDGES[key]["image"]
    return {
        "BRIDGE_IMAGE": image,
        "BRIDGE_BINARY": f"/usr/bin/mautrix-{key}",
        "BRIDGE_DATABASE": bridge_database(key),
    }


# --- Output --------------------------------------------------------------------

def urls(settings):
    def url(host):
        return f"https://{host}/" if host else ""

    return {
        "homeserver_url": url(settings.get("host")),
        "element_url": url(settings.get("element_host")),
        "cinny_url": url(settings.get("cinny_host")),
        "ketesa_url": url(settings.get("ketesa_host")),
    }


def configuration(settings, domains, admin_ready):
    """Output of get-configuration."""
    result = {key: settings.get(key, value) for key, value in DEFAULTS.items() if key != "telegram_api_hash"}
    result["telegram_api_hash_set"] = bool(settings.get("telegram_api_hash"))
    result["configured"] = bool(settings.get("host"))
    result["user_domains"] = domains
    result["available_bridges"] = [
        {"id": key, "name": info["name"], "image": info["image"]} for key, info in BRIDGES.items()
    ]
    documents = wellknown_documents(settings) if settings.get("host") and delegated(settings) else {}
    result["wellknown_server"] = json.dumps(documents["server"]) if documents else ""
    result["wellknown_client"] = json.dumps(documents["client"]) if documents else ""
    result["admin_user"] = user_id(ADMIN_LOCALPART, settings["server_name"]) if settings.get("server_name") else ""
    result["admin_password"] = settings.get("secrets", {}).get("admin_password", "") if admin_ready else ""
    result.update(urls(settings))
    return result
