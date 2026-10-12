# ns8-matrix-reworked

A [NethServer 8](https://github.com/NethServer/ns8-core) module for a complete
[Matrix](https://matrix.org/) server:

- **Synapse** homeserver with PostgreSQL, federation over port 443
- login with the users of an NS8 **user domain** (OpenLDAP or Active Directory),
  or local accounts only
- web apps, each on its own host name and optional: **Element Web**, **Cinny**
  and the **Ketesa** Synapse admin UI (formerly synapse-admin)
- **bridges**, each enabled with one switch: WhatsApp, Signal, Telegram and
  Discord (mautrix, with double puppeting and end-to-bridge encryption
  allowed), Steam (matrix-steam-bridge) and IRC (Heisenbridge)
- **Draupnir** moderation bot with its own management room
- nightly **synapse_auto_compressor** run against the room state tables
- optional **matrix-synapse-shared-secret-auth** login for trusted tools
- **calls**: **Element Call** (MatrixRTC with a LiveKit media server, embedded in
  Element Web) and a **coturn** TURN server for classic 1:1 calls and LiveKit
- **server name delegation** (user IDs `@alice:example.org` with the homeserver
  on `matrix.example.org`), optionally served by the module
- notification mails through the cluster smarthost
- backup and restore of the databases, bridge state and media

It builds on ideas of [NethServer/ns8-matrix](https://github.com/NethServer/ns8-matrix)
(a proof of concept) and [matrix-docker-ansible-deploy](https://github.com/spantaleev/matrix-docker-ansible-deploy).

## Install

    add-module ghcr.io/platypuschan/matrix-reworked:latest 1

## Configure

Open the module in the cluster admin UI, or use `configure-module`:

    api-cli run module/matrix-reworked1/configure-module --data '{
      "host": "matrix.example.org",
      "server_name": "example.org",
      "wellknown_on_server_name": true,
      "lets_encrypt": true,
      "http2https": true,
      "user_domain": "ad.example.org",
      "federation": true,
      "element_host": "chat.example.org",
      "cinny_host": "",
      "ketesa_host": "matrix-admin.example.org",
      "bridges": ["whatsapp", "signal"]
    }'

| Field | Meaning |
| --- | --- |
| `host` | Host name of Synapse (client and federation API, port 443). Required. |
| `server_name` | Domain in user IDs. Empty uses `host`. **Cannot be changed later.** |
| `wellknown_on_server_name` | If `server_name` differs from `host`: route `https://<server_name>/.well-known/matrix/*` to the module. Otherwise publish the two documents shown in the UI on that domain yourself. |
| `lets_encrypt`, `http2https` | Traefik settings for all host names of the module. |
| `user_domain` | NS8 user domain whose users log in with their password. Empty: local accounts only. |
| `federation` | Talk to other Matrix servers. |
| `element_host`, `cinny_host`, `ketesa_host` | Host name of each web app; empty disables it. |
| `bridges` | Enabled bridges: `whatsapp`, `signal`, `telegram`, `discord`, `steam`, `heisenbridge`. |
| `heisenbridge_owner` | Matrix ID that owns Heisenbridge; empty: the first local user who talks to it. |
| `draupnir` | Run the Draupnir moderation bot. |
| `state_compressor` | Compress the Synapse room state tables every night (default on). |
| `shared_secret_auth` | Load matrix-synapse-shared-secret-auth; the secret is shown in the Settings page. |
| `telegram_api_id`, `telegram_api_hash` | Required for the Telegram bridge, from <https://my.telegram.org/apps>. An empty hash keeps the stored one. |
| `mail_from` | Sender of notification mails (default `noreply@<server_name>`). |
| `max_upload_mb` | Maximum upload size, default 100. |
| `element_call` | Element Call backend: LiveKit SFU and LiveKit JWT service below `https://<host>/livekit/`. |
| `turn` | coturn TURN server (UDP). |
| `calls_public_ip` | Public IP address of the node if it is behind NAT; empty: LiveKit finds it with STUN, coturn announces the node addresses. |

Fields missing from a request keep their stored value.

### Admin account

The module creates the local server admin `@matrixadmin:<server_name>`. Its
initial password is shown in the Settings page (`get-configuration`); change it
after the first login. Use it in Ketesa or any Matrix client.

### Users

With a user domain, users log in with their NS8 user name and password; the
Matrix account is created at the first login. Public registration is always
disabled. Without a user domain, the admin creates local accounts (for example
in Ketesa).

### Bridges

All users of the server can use the enabled bridges: start a chat with the bridge
bot (`@whatsappbot`, `@signalbot`, `@telegrambot`, `@discordbot`, `@steambot`) and send `login`. The admin
account has bridge admin rights.

A bridge image is pulled the first time the bridge is enabled. Each bridge keeps
its configuration in `state/bridges/<bridge>/config.yaml`. The module sets only
the addresses, ports, database, permissions, double puppeting, encryption
permission and logging there; other options can be edited in that file and
are kept (restart the bridge with `runagent -m <module> systemctl --user restart bridge@<bridge>`).
Disabling a bridge stops it and removes it from Synapse; its configuration and
database are kept for a later re-enable.

**Steam** (matrix-steam-bridge) starts a SteamKit helper that always listens
on port 50051 without authentication. The module therefore runs this bridge
in its own network namespace (pasta, with the host loopback mapped to
169.254.1.3) and publishes only its appservice port on 127.0.0.1.

**IRC** (Heisenbridge) is a bouncer: write to `@heisenbridge`, then use
`ADDNETWORK`, `OPEN` and `JOIN` to connect to IRC networks of your choice.
Its owner (see `heisenbridge_owner`) can allow more users.

Releases before 0.3.0 also had bridges for Messenger/Instagram, Google
Messages, Slack, Bluesky, X and LinkedIn. An update stops them and removes
them from Synapse; their `state/bridges/<bridge>` directory and
`mautrix_<bridge>` database are kept.

### Draupnir

The module creates the bot account `@draupnir` (server admin, for its admin
commands, without rate limits) and a private management room, and invites
`@matrixadmin` there; the Settings page shows its room ID. Invite
`@draupnir` to rooms it should protect and control it with `!draupnir`
commands in the management room (`!draupnir help`).

### Maintenance and shared-secret login

`synapse-compressor.timer` runs synapse_auto_compressor every night (500
state groups per chunk, 100 chunks) while Synapse keeps running.

With `shared_secret_auth`, Synapse loads
[matrix-synapse-shared-secret-auth](https://github.com/devture/matrix-synapse-shared-secret-auth)
2.0.3 (AGPL-3.0, downloaded at build time and checked by its SHA-256). A
tool that knows the secret logs in as `@user:server` with the token
`HMAC-SHA512(secret, "@user:server")` (login type
`com.devture.shared_secret_auth`, or as the password of `m.login.password`).

### Calls

**Element Call** needs no extra host name: LiveKit (`/livekit/sfu`) and the
JWT service (`/livekit/jwt`) are routed below the homeserver host. Synapse
announces them through its RTC transports API and the client well-known
(`org.matrix.msc4143.rtc_foci`) and enables the experimental features Element
Call needs (delayed events, `state_after`, sticky events). Element Web uses
its embedded Element Call; Element X finds the backend on its own.

The JWT service checks the OpenID tokens of callers with the federation API of
the server name, so `https://<server_name>/.well-known/matrix/server` must
point to the homeserver (with a delegated server name: the module's
well-known option, or your own documents). The module's own host names are
resolved to this node, so no hairpin NAT is needed. Only users of this server
can start calls.

The **TURN server** listens on UDP only. Synapse hands out short-lived
credentials (shared secret); LiveKit uses the same TURN server. Relaying to
private, loopback and link-local addresses is denied.

Calls use a UDP port range allocated by NS8 (64 ports: TURN, LiveKit media,
62 TURN relay ports) and one TCP port for LiveKit (ICE over TCP). The module
opens them in the node firewall; the Settings page lists them. Behind a
router, forward them to the node. Instances installed before 0.2.0 get their
UDP ports when calls are enabled for the first time.

## Services

All containers run rootless as the module user:

| Unit | Content |
| --- | --- |
| `postgresql.service` | PostgreSQL 17, databases `synapse` and `mautrix_<bridge>` |
| `synapse.service` | Synapse, configuration rendered into `state/synapse/` |
| `element.service`, `cinny.service`, `ketesa.service` | web apps |
| `wellknown.service` | `/.well-known/matrix` for a delegated server name |
| `bridge@<bridge>.service` | one bridge |
| `draupnir.service` | Draupnir moderation bot |
| `synapse-compressor.timer` | nightly synapse_auto_compressor run |
| `coturn.service` | coturn TURN server |
| `livekit.service`, `lk-jwt.service` | LiveKit SFU and LiveKit JWT service for Element Call |

## Backup and restore

`module-dump-state` writes all databases with `pg_dumpall`. The backup contains
the settings (`state/matrix.json`), the dump, the bridge configurations and the
`synapse-data` volume (media and the server signing key). Restore loads the dump
into a new database volume and configures the module with the stored settings.

## Uninstall

    remove-module --no-preserve matrix-reworked1

## Tests

`validate.yml` runs the unit tests (`tests/test_*.py`) and the UI build. The
`Publish images` workflow builds the module and runs the Robot Framework suite
in `tests/` on NS8 QEMU virtual machines (Rocky Linux 9 and Debian 13, install
and update scenarios).
