#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Settings, secrets and generated configuration of the Matrix module.

The module keeps its settings and secrets in state/matrix.json (mode 600)
and renders every configuration file from them:

- synapse/homeserver.yaml, synapse/log.config and synapse/appservices/*.yaml
  (JSON is valid YAML, so Synapse reads the JSON output directly),
- web/element.json, web/cinny.json, web/ketesa.json for the web apps,
- calls/ for coturn, LiveKit and the LiveKit JWT service,
- draupnir/config/production.yaml for the Draupnir bot.

Bridges are modules of their own. They register their appservice with the
add-appservice action (state/appservices.json) and get the homeserver
details from get-matrix-info.
"""

import contextlib
import copy
import ipaddress
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
    # Bridges were part of the module before 0.3.0; they are modules of
    # their own now. Their ports stay reserved: the positions of the
    # services after them must not change.
    "reserved_whatsapp",
    "reserved_signal",
    "reserved_telegram",
    "reserved_meta",
    "reserved_gmessages",
    "reserved_slack",
    "reserved_discord",
    "reserved_bluesky",
    "reserved_twitter",
    "reserved_linkedin",
    "wellknown",
    # Element Call: LiveKit signalling (through Traefik), LiveKit ICE/TCP
    # (public) and the LiveKit JWT service (through Traefik)
    "livekit",
    "livekit_tcp",
    "lk_jwt",
]

# Allocated UDP ports (UDP_PORTS_RANGE): the TURN listener, the LiveKit
# media port (all calls share it) and the TURN relay range.
UDP_PORTS_DEMAND = 64
UDP_TURN = 0
UDP_LIVEKIT = 1
UDP_RELAY_FIRST = 2

# Path prefixes of the Element Call backend on the homeserver host
LIVEKIT_PATH = "/livekit/sfu"
LK_JWT_PATH = "/livekit/jwt"

DRAUPNIR_LOCALPART = "draupnir"
SHARED_SECRET_MODULES = "/modules"

# Settings of releases before 0.3.0, when the bridges were part of the
# module; ensure_settings() removes them from stored settings and backups.
LEGACY_SETTINGS = ("bridges", "telegram_api_id", "telegram_api_hash", "heisenbridge_owner")
LEGACY_SECRETS = ("heisenbridge_as_token", "heisenbridge_hs_token")

# Appservices that bridge modules register (add-appservice action)
APPSERVICES = "appservices.json"
APPSERVICE_API_VERSION = 1

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
    "turn": False,
    "element_call": False,
    "calls_public_ip": "",
    "draupnir": False,
    "state_compressor": True,
    "shared_secret_auth": False,
}

SECRET_KEYS = (
    "postgres_password",
    "registration_shared_secret",
    "macaroon_secret_key",
    "form_secret",
    "doublepuppet_as_token",
    "doublepuppet_hs_token",
    "admin_password",
    "turn_shared_secret",
    "livekit_key",
    "livekit_secret",
    "draupnir_password",
    "shared_secret_auth_secret",
)

MXID_PATTERN = re.compile(r"^@[a-z0-9._=/+-]+:[A-Za-z0-9.-]+(:[0-9]{1,5})?$")


def ensure_settings(state_dir="."):
    """Return the settings with all defaults and secrets; secrets are created
    once and kept, also across restores and updates."""
    settings = read_settings(state_dir)
    changed = False
    for key, value in DEFAULTS.items():
        if key not in settings:
            settings[key] = copy.deepcopy(value)
            changed = True
    for key in LEGACY_SETTINGS:
        if key in settings:
            del settings[key]
            changed = True
    stored = settings.setdefault("secrets", {})
    for key in LEGACY_SECRETS:
        if key in stored:
            del stored[key]
            changed = True
    for key in SECRET_KEYS:
        if not stored.get(key):
            if key == "admin_password":
                stored[key] = new_password()
            elif key == "livekit_key":
                # An API key name, sent in clear in LiveKit tokens
                stored[key] = "lk" + new_password(14)
            else:
                stored[key] = new_secret()
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
        if key in data:
            new[key] = data[key]

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

    new["calls_public_ip"] = str(new["calls_public_ip"] or "").strip()
    if new["calls_public_ip"]:
        try:
            address = ipaddress.ip_address(new["calls_public_ip"])
        except ValueError:
            raise SettingsError("calls_public_ip", new["calls_public_ip"], "invalid_ip_address") from None
        if address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
            raise SettingsError("calls_public_ip", new["calls_public_ip"], "invalid_ip_address")
        new["calls_public_ip"] = str(address)

    for key in ("lets_encrypt", "http2https", "federation", "wellknown_on_server_name", "turn", "element_call",
                "draupnir", "state_compressor", "shared_secret_auth"):
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


def udp_ports(environ=os.environ):
    """Return the UDP ports for calls, or None if the module has none (an
    instance installed before 0.2.0 gets them when calls are enabled)."""
    span = environ.get("UDP_PORTS_RANGE", "")
    if not span:
        return None
    first, last = (int(item) for item in span.split("-"))
    if last - first + 1 < UDP_PORTS_DEMAND:
        raise RuntimeError(f"{UDP_PORTS_DEMAND} UDP ports needed, {span} allocated")
    return {
        "turn": first + UDP_TURN,
        "livekit": first + UDP_LIVEKIT,
        "relay_first": first + UDP_RELAY_FIRST,
        "relay_last": first + UDP_PORTS_DEMAND - 1,
    }


def calls_enabled(settings):
    return bool(settings.get("turn") or settings.get("element_call"))


def user_id(localpart, server_name):
    return f"@{localpart}:{server_name}"


def regex_quote(text):
    return re.escape(text)


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


def shared_secret_module(settings):
    """matrix-synapse-shared-secret-auth: lets trusted tools log in as any
    local user with a token derived from the shared secret."""
    return {
        "module": "shared_secret_authenticator.SharedSecretAuthProvider",
        "config": {
            "shared_secret": settings["secrets"]["shared_secret_auth_secret"],
            "m_login_password_support_enabled": True,
            "com_devture_shared_secret_auth_support_enabled": True,
        },
    }


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
    client = {"m.homeserver": {"base_url": f"https://{settings['host']}"}}
    if settings.get("element_call"):
        client["org.matrix.msc4143.rtc_foci"] = rtc_foci(settings)
    return {
        "server": {"m.server": f"{settings['host']}:443"},
        "client": client,
    }


def appservice_files(appservice_ids=()):
    files = [f"{SYNAPSE_CONFIG}/appservices/{DOUBLE_PUPPET_ID}.yaml"]
    files += [f"{SYNAPSE_CONFIG}/appservices/{key}.yaml" for key in sorted(appservice_ids)]
    return files


def livekit_service_url(settings):
    return f"https://{settings['host']}{LK_JWT_PATH}"


def rtc_foci(settings):
    """MatrixRTC backend for the client well-known. Element Call running as a
    widget (Element Web, Element Desktop) has no access token for the
    homeserver's RTC transports API and still reads this property."""
    return [{"type": "livekit", "livekit_service_url": livekit_service_url(settings)}]


def turn_uris(settings, udp_map):
    return [f"turn:{settings['host']}:{udp_map['turn']}?transport=udp"]


def calls_config(settings, udp_map):
    """Synapse settings for TURN (classic 1:1 calls) and MatrixRTC."""
    config = {}
    if settings.get("turn") and udp_map:
        config.update({
            "turn_uris": turn_uris(settings, udp_map),
            "turn_shared_secret": settings["secrets"]["turn_shared_secret"],
            "turn_user_lifetime": "1d",
            "turn_allow_guests": False,
        })
    if settings.get("element_call"):
        config.update({
            "experimental_features": {
                # Room summaries, RTC transports API, state_after in sync
                # and sticky events, as used by Element Call
                "msc3266_enabled": True,
                "msc4143_enabled": True,
                "msc4222_enabled": True,
                "msc4354_enabled": True,
            },
            "matrix_rtc": {"transports": rtc_foci(settings)},
            # Delayed events (MSC4140) end a call membership when a client
            # disappears; without them calls stay stuck in rooms.
            "max_event_delay_duration": "24h",
            "rc_delayed_event_mgmt": {"per_second": 1, "burst_count": 20},
            # Call signalling sends bursts of events
            "rc_message": {"per_second": 0.5, "burst_count": 30},
            "extra_well_known_client_content": {"org.matrix.msc4143.rtc_foci": rtc_foci(settings)},
        })
    return config


def homeserver_config(settings, port_map, ldap=None, smarthost=None, udp_map=None, appservice_ids=()):
    stored = settings["secrets"]
    if settings["federation"]:
        resources = ["client", "federation"]
    elif settings.get("element_call"):
        # The LiveKit JWT service checks OpenID tokens with the federation
        # userinfo endpoint, which "openid" serves alone
        resources = ["client", "openid"]
    else:
        resources = ["client"]
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
        "app_service_config_files": appservice_files(appservice_ids),
        "suppress_key_server_warning": True,
    }
    if not delegated(settings) and (settings["federation"] or settings.get("element_call")):
        # Synapse answers {"m.server": "<server_name>:443"}: other servers,
        # and the LiveKit JWT service, find the federation API (OpenID) on
        # port 443 of the host. A delegated server name gets its own
        # document, see wellknown_documents().
        config["serve_server_wellknown"] = True
    if settings["federation"]:
        config["trusted_key_servers"] = [{"server_name": "matrix.org"}]
    else:
        config["federation_domain_whitelist"] = []
        config["trusted_key_servers"] = []
    modules = []
    if ldap:
        modules.append(ldap_module(ldap))
    if settings.get("shared_secret_auth"):
        modules.append(shared_secret_module(settings))
    if modules:
        config["modules"] = modules
    email = email_section(settings, smarthost)
    if email:
        config["email"] = email
    config.update(calls_config(settings, udp_map))
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
    config = {
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
    if settings.get("element_call"):
        # Element Web embeds Element Call; no separate Element Call app
        config["features"] = {
            "feature_group_calls": True,
            "feature_video_rooms": True,
            "feature_element_call_video_rooms": True,
        }
        config["element_call"] = {"use_exclusively": True, "participant_limit": 8, "brand": "Element Call"}
    return config


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


# --- Calls ---------------------------------------------------------------------

PRIVATE_PEERS = (
    # Do not relay to the local networks of the node (RFC 1918, loopback,
    # link-local, CGNAT, multicast, reserved): TURN users could reach
    # internal services otherwise.
    "0.0.0.0-0.255.255.255",
    "10.0.0.0-10.255.255.255",
    "100.64.0.0-100.127.255.255",
    "127.0.0.0-127.255.255.255",
    "169.254.0.0-169.254.255.255",
    "172.16.0.0-172.31.255.255",
    "192.0.0.0-192.0.0.255",
    "192.168.0.0-192.168.255.255",
    "198.18.0.0-198.19.255.255",
    "224.0.0.0-255.255.255.255",
    "::1",
    "fc00::-fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
    "fe80::-febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
)


def coturn_config(settings, udp_map):
    """turnserver.conf: UDP only, credentials from the shared secret (the
    same secret Synapse and LiveKit use for short-lived credentials)."""
    lines = [
        f"listening-port={udp_map['turn']}",
        "no-tcp",
        "no-tls",
        "no-dtls",
        f"min-port={udp_map['relay_first']}",
        f"max-port={udp_map['relay_last']}",
        "use-auth-secret",
        f"static-auth-secret={settings['secrets']['turn_shared_secret']}",
        f"realm={settings['server_name']}",
        "fingerprint",
        "no-cli",
        "no-multicast-peers",
        "no-software-attribute",
        "stale-nonce=600",
        "log-file=stdout",
        "simple-log",
        "pidfile=/tmp/turnserver.pid",
    ]
    if settings.get("calls_public_ip"):
        lines.append(f"external-ip={settings['calls_public_ip']}")
    lines += [f"denied-peer-ip={peer}" for peer in PRIVATE_PEERS]
    return "\n".join(lines) + "\n"


def livekit_config(settings, port_map, udp_map):
    stored = settings["secrets"]
    rtc = {
        "tcp_port": port_map["livekit_tcp"],
        "udp_port": udp_map["livekit"],
    }
    if settings.get("calls_public_ip"):
        rtc["node_ip"] = settings["calls_public_ip"]
        rtc["use_external_ip"] = False
    else:
        # Find the public address with STUN (servers behind NAT)
        rtc["use_external_ip"] = True
    if settings.get("turn"):
        rtc["turn_servers"] = [{
            "host": settings["host"],
            "port": udp_map["turn"],
            "protocol": "udp",
            "secret": stored["turn_shared_secret"],
        }]
    return {
        "port": port_map["livekit"],
        # Signalling only through Traefik; the media ports bind everywhere
        "bind_addresses": ["127.0.0.1"],
        "rtc": rtc,
        "room": {"auto_create": False},
        "turn": {"enabled": False},
        "keys": {stored["livekit_key"]: stored["livekit_secret"]},
        # Lets the JWT service end call memberships of clients that vanish
        "webhook": {
            "api_key": stored["livekit_key"],
            "urls": [f"http://127.0.0.1:{port_map['lk_jwt']}/sfu_webhook"],
        },
        "logging": {"level": "info", "pion_level": "error", "json": False},
    }


def lk_jwt_local_hosts(settings):
    """Names the JWT service reaches through Traefik on this node: the
    homeserver host (OpenID userinfo, LiveKit API) and the server name if
    this module answers its /.well-known/matrix/server."""
    hosts = [settings["host"]]
    if delegated(settings) and serves_wellknown(settings):
        hosts.append(settings["server_name"])
    return hosts


def lk_jwt_unit_env(settings):
    # $LK_JWT_HOST_ARGS in lk-jwt.service: systemd splits it into words
    args = " ".join(f"--add-host={host}:127.0.0.1" for host in lk_jwt_local_hosts(settings))
    return {"LK_JWT_HOST_ARGS": args}


def lk_jwt_env(settings, port_map):
    stored = settings["secrets"]
    env = {
        "LIVEKIT_JWT_BIND": f"127.0.0.1:{port_map['lk_jwt']}",
        "LIVEKIT_URL": f"wss://{settings['host']}{LIVEKIT_PATH}",
        "LIVEKIT_KEY": stored["livekit_key"],
        "LIVEKIT_SECRET": stored["livekit_secret"],
        # Only users of this server may start calls
        "LIVEKIT_FULL_ACCESS_HOMESERVERS": settings["server_name"],
        # Check OpenID tokens with the local Synapse directly, also without
        # federation or public DNS
        "LIVEKIT_CS_API_URL_OVERRIDES": f"{settings['server_name']}=http://127.0.0.1:{port_map['synapse']}",
        "LIVEKIT_SANITY_CHECK_INTERVAL_SECONDS": "30",
    }
    if not settings["lets_encrypt"]:
        # Traefik has a self-signed certificate; the local names above go
        # to 127.0.0.1, so the unverified connections stay on the node
        env["LIVEKIT_INSECURE_SKIP_VERIFY_TLS"] = "YES_I_KNOW_WHAT_I_AM_DOING"
    return env


def firewall_ports(settings, port_map, udp_map):
    """Ports that clients and other TURN/WebRTC peers must reach directly."""
    ports = []
    if not udp_map:
        return ports
    if settings.get("turn"):
        ports.append(f"{udp_map['turn']}/udp")
        ports.append(f"{udp_map['relay_first']}-{udp_map['relay_last']}/udp")
    if settings.get("element_call"):
        ports.append(f"{udp_map['livekit']}/udp")
        ports.append(f"{port_map['livekit_tcp']}/tcp")
    return ports


# --- Bridges -----------------------------------------------------------------

# --- Appservices of bridge modules -----------------------------------------

APPSERVICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
LOCALPART_PATTERN = re.compile(r"^[a-z0-9._=/+-]{1,255}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~+/=-]{16,512}$")
LOOPBACK_URL_PATTERN = re.compile(r"^http://127\.0\.0\.1:[0-9]{1,5}(/[^\s]*)?$")
# Registration keys passed on to Synapse; anything else is dropped
REGISTRATION_KEYS = (
    "id", "url", "as_token", "hs_token", "sender_localpart", "rate_limited", "namespaces", "protocols",
    "receive_ephemeral", "push_ephemeral", "de.sorunome.msc2409.push_ephemeral",
    "org.matrix.msc3202", "io.element.msc4190",
)
NAMESPACE_KINDS = ("users", "aliases", "rooms")


def validate_registration(registration, settings, owner, existing):
    """Check an appservice registration of a bridge module; return the
    cleaned registration or raise SettingsError.

    The registration lets its holder act as every user of its exclusive
    namespace, so it must stay on this node (loopback URL), must not reuse
    tokens or IDs of other appservices and must not claim the users of this
    server."""
    if not isinstance(registration, dict):
        raise SettingsError("registration", "", "invalid_registration")
    reg = {key: registration[key] for key in REGISTRATION_KEYS if key in registration}
    appservice_id = str(reg.get("id", ""))
    if not APPSERVICE_ID_PATTERN.fullmatch(appservice_id) or appservice_id == DOUBLE_PUPPET_ID:
        raise SettingsError("registration.id", appservice_id, "invalid_appservice_id")
    if appservice_id in existing and existing[appservice_id]["module"] != owner:
        raise SettingsError("registration.id", appservice_id, "appservice_id_in_use")
    url = reg.get("url")
    if url is not None and not LOOPBACK_URL_PATTERN.fullmatch(str(url)):
        raise SettingsError("registration.url", url, "url_not_on_this_node")
    for key in ("as_token", "hs_token"):
        if not TOKEN_PATTERN.fullmatch(str(reg.get(key, ""))):
            raise SettingsError(f"registration.{key}", "", "invalid_token")
    if reg["as_token"] == reg["hs_token"]:
        raise SettingsError("registration.hs_token", "", "invalid_token")
    used = {settings["secrets"]["doublepuppet_as_token"], settings["secrets"]["doublepuppet_hs_token"]}
    for other_id, other in existing.items():
        if other_id != appservice_id:
            used |= {other["registration"]["as_token"], other["registration"]["hs_token"]}
    if reg["as_token"] in used or reg["hs_token"] in used:
        raise SettingsError("registration.as_token", "", "token_in_use")
    if not LOCALPART_PATTERN.fullmatch(str(reg.get("sender_localpart", ""))):
        raise SettingsError("registration.sender_localpart", reg.get("sender_localpart", ""), "invalid_localpart")
    namespaces = reg.get("namespaces") or {}
    if not isinstance(namespaces, dict):
        raise SettingsError("registration.namespaces", "", "invalid_namespaces")
    server_name = settings["server_name"]
    protected = [
        user_id(ADMIN_LOCALPART, server_name),
        user_id(DRAUPNIR_LOCALPART, server_name),
        # Stands for any ordinary user of this server
        user_id("ns8-namespace-probe-7f3a", server_name),
    ]
    clean = {}
    for kind in NAMESPACE_KINDS:
        entries = namespaces.get(kind) or []
        if not isinstance(entries, list):
            raise SettingsError(f"registration.namespaces.{kind}", "", "invalid_namespaces")
        clean[kind] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("regex"), str):
                raise SettingsError(f"registration.namespaces.{kind}", "", "invalid_namespaces")
            try:
                pattern = re.compile(entry["regex"])
            except re.error:
                raise SettingsError(f"registration.namespaces.{kind}", entry["regex"], "invalid_regex") from None
            exclusive = bool(entry.get("exclusive", False))
            if kind == "users" and exclusive and any(pattern.match(mxid) for mxid in protected):
                raise SettingsError("registration.namespaces.users", entry["regex"], "namespace_too_wide")
            clean[kind].append({"regex": entry["regex"], "exclusive": exclusive})
    reg["namespaces"] = clean
    reg["rate_limited"] = bool(reg.get("rate_limited", False))
    return reg


def matrix_info(settings, port_map):
    """What a bridge module needs to connect to this homeserver (output of
    add-appservice and get-matrix-info)."""
    return {
        "api_version": APPSERVICE_API_VERSION,
        "server_name": settings["server_name"],
        # Bridge modules run on this node and reach Synapse directly
        "homeserver_url": f"http://127.0.0.1:{port_map['synapse']}",
        "public_url": f"https://{settings['host']}",
        "federation": settings["federation"],
        "admin_user": user_id(ADMIN_LOCALPART, settings["server_name"]),
        # For double puppeting (mautrix: double_puppet.secrets)
        "double_puppet_secret": "as_token:" + settings["secrets"]["doublepuppet_as_token"],
    }


# --- Draupnir and maintenance ------------------------------------------------

DRAUPNIR_DIR = "draupnir"


def draupnir_config(settings, port_map, access_token, management_room):
    homeserver = f"http://127.0.0.1:{port_map['synapse']}"
    return {
        "homeserverUrl": homeserver,
        "rawHomeserverUrl": homeserver,
        "accessToken": access_token,
        "pantalaimon": {"use": False},
        "experimentalRustCrypto": False,
        "dataPath": "/data",
        # Only the managers in the management room can invite the bot
        "autojoinOnlyIfManager": True,
        "recordIgnoredInvites": False,
        "managementRoom": management_room,
        "logLevel": "INFO",
        "verifyPermissionsOnStartup": True,
        "noop": False,
        "protectAllJoinedRooms": False,
        "backgroundDelayMS": 500,
        "admin": {"enableMakeRoomAdminCommand": False},
        "commands": {"allowNoPrefix": False, "symbolPrefixes": ["!"], "additionalPrefixes": ["draupnir"]},
        "web": {"enabled": False},
    }


def compressor_env(settings, port_map):
    password = settings["secrets"]["postgres_password"]
    return {"POSTGRES_LOCATION": f"postgresql://postgres:{password}@127.0.0.1:{port_map['postgres']}/synapse"}


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


def configuration(settings, domains, admin_ready, port_map=None, udp_map=None, draupnir_room="", appservices=None):
    """Output of get-configuration."""
    result = {key: settings.get(key, value) for key, value in DEFAULTS.items()}
    result["configured"] = bool(settings.get("host"))
    result["user_domains"] = domains
    documents = wellknown_documents(settings) if settings.get("host") and delegated(settings) else {}
    result["wellknown_server"] = json.dumps(documents["server"]) if documents else ""
    result["wellknown_client"] = json.dumps(documents["client"]) if documents else ""
    result["appservices"] = [
        {"id": key, "module": value["module"]} for key, value in sorted((appservices or {}).items())
    ]
    result["draupnir_management_room"] = draupnir_room if settings.get("draupnir") else ""
    result["shared_secret_auth_secret"] = (
        settings.get("secrets", {}).get("shared_secret_auth_secret", "") if settings.get("shared_secret_auth") else ""
    )
    result["calls_ports"] = firewall_ports(settings, port_map, udp_map) if port_map and udp_map else []
    result["admin_user"] = user_id(ADMIN_LOCALPART, settings["server_name"]) if settings.get("server_name") else ""
    result["admin_password"] = settings.get("secrets", {}).get("admin_password", "") if admin_ready else ""
    result.update(urls(settings))
    return result
