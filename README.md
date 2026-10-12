# ns8-matrix-reworked

A [NethServer 8](https://github.com/NethServer/ns8-core) module for a complete
[Matrix](https://matrix.org/) server:

- **Synapse** homeserver with PostgreSQL, federation over port 443
- login with the users of an NS8 **user domain** (OpenLDAP or Active Directory),
  or local accounts only
- web apps, each on its own host name and optional: **Element Web**, **Cinny**
  and the **Ketesa** Synapse admin UI (formerly synapse-admin)
- **mautrix bridges**, each enabled with one switch: WhatsApp, Signal, Telegram,
  Messenger/Instagram, Google Messages, Slack, Discord, Bluesky, X (Twitter) and
  LinkedIn, with double puppeting and end-to-bridge encryption allowed
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
| `bridges` | Enabled bridges: `whatsapp`, `signal`, `telegram`, `meta`, `gmessages`, `slack`, `discord`, `bluesky`, `twitter`, `linkedin`. |
| `telegram_api_id`, `telegram_api_hash` | Required for the Telegram bridge, from <https://my.telegram.org/apps>. An empty hash keeps the stored one. |
| `mail_from` | Sender of notification mails (default `noreply@<server_name>`). |
| `max_upload_mb` | Maximum upload size, default 100. |

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
bot (`@whatsappbot`, `@signalbot`, `@telegrambot`, `@facebookbot` for
Messenger/Instagram, `@gmessagesbot`, `@slackbot`, `@discordbot`,
`@blueskybot`, `@twitterbot`, `@linkedinbot`) and send `login`. The admin
account has bridge admin rights.

A bridge image is pulled the first time the bridge is enabled. Each bridge keeps
its configuration in `state/bridges/<bridge>/config.yaml`. The module sets only
the addresses, ports, database, permissions, double puppeting, encryption
permission and logging there; other options can be edited in that file and
are kept (restart the bridge with `runagent -m <module> systemctl --user restart bridge@<bridge>`).
Disabling a bridge stops it and removes it from Synapse; its configuration and
database are kept for a later re-enable.

## Services

All containers run rootless as the module user:

| Unit | Content |
| --- | --- |
| `postgresql.service` | PostgreSQL 17, databases `synapse` and `mautrix_<bridge>` |
| `synapse.service` | Synapse, configuration rendered into `state/synapse/` |
| `element.service`, `cinny.service`, `ketesa.service` | web apps |
| `wellknown.service` | `/.well-known/matrix` for a delegated server name |
| `bridge@<bridge>.service` | one mautrix bridge |

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
