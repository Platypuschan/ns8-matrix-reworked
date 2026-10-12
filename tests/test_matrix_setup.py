#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import hashlib
import hmac
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "imageroot" / "pypkg"))
import matrix_runtime  # noqa: E402
import matrix_setup  # noqa: E402

ENV = {"TCP_PORTS_RANGE": "20001-20020"}
UDP_ENV = {"UDP_PORTS_RANGE": "40001-40064"}
BASE = {"host": "matrix.example.org", "lets_encrypt": False, "http2https": True}


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, **extra):
        return matrix_setup.apply_request({**BASE, **extra}, self.state)

    def mode(self, *parts):
        return stat.S_IMODE(os.stat(os.path.join(self.state, *parts)).st_mode)


class SettingsTest(StateTest):
    def test_defaults_and_secrets_are_kept(self):
        first = self.apply()
        self.assertEqual(first["server_name"], "matrix.example.org")
        self.assertEqual(set(first["secrets"]), set(matrix_setup.SECRET_KEYS))
        second = self.apply(element_host="chat.example.org")
        self.assertEqual(first["secrets"], second["secrets"])
        self.assertEqual(self.mode(matrix_setup.SETTINGS), 0o600)
        self.assertRegex(first["secrets"]["admin_password"], r"^[A-Za-z0-9]{20}$")

    def test_missing_fields_keep_their_value(self):
        self.apply(element_host="chat.example.org", bridges=["signal"], max_upload_mb=20)
        settings = self.apply()
        self.assertEqual(settings["element_host"], "chat.example.org")
        self.assertEqual(settings["bridges"], ["signal"])
        self.assertEqual(settings["max_upload_mb"], 20)

    def test_server_name_cannot_change(self):
        self.apply(server_name="example.org")
        with self.assertRaises(matrix_setup.SettingsError) as raised:
            self.apply(server_name="other.org")
        self.assertEqual(raised.exception.error, "server_name_immutable")
        # Omitted or equal (any case) is fine
        self.assertEqual(self.apply()["server_name"], "example.org")
        self.assertEqual(self.apply(server_name="Example.ORG")["server_name"], "example.org")

    def test_invalid_input_changes_nothing(self):
        self.apply()
        before = Path(self.state, matrix_setup.SETTINGS).read_text()
        cases = [
            ({"host": "nodot"}, "invalid_hostname"),
            ({"element_host": "matrix.example.org"}, "hostname_already_used"),
            ({"mail_from": "nobody"}, "invalid_email"),
            ({"max_upload_mb": 0}, "invalid_number"),
            ({"bridges": ["irc"]}, "unknown_bridge"),
            ({"bridges": ["telegram"]}, "telegram_credentials_required"),
            ({"telegram_api_id": "12a"}, "invalid_number"),
        ]
        for extra, error in cases:
            with self.subTest(extra=extra):
                with self.assertRaises(matrix_setup.SettingsError) as raised:
                    self.apply(**extra)
                self.assertEqual(raised.exception.error, error)
        self.assertEqual(Path(self.state, matrix_setup.SETTINGS).read_text(), before)

    def test_validation_only_does_not_write(self):
        self.apply()
        result = matrix_setup.apply_request({**BASE, "element_host": "chat.example.org"}, self.state, write=False)
        self.assertEqual(result["element_host"], "chat.example.org")
        self.assertEqual(matrix_setup.read_settings(self.state)["element_host"], "")

    def test_bridges_are_ordered_and_unique(self):
        settings = self.apply(bridges=["slack", "whatsapp", "slack"])
        self.assertEqual(settings["bridges"], ["whatsapp", "slack"])

    def test_telegram_hash_is_kept_when_empty(self):
        self.apply(bridges=["telegram"], telegram_api_id="123", telegram_api_hash="abc")
        settings = self.apply(telegram_api_hash="")
        self.assertEqual(settings["telegram_api_hash"], "abc")

    def test_own_hosts(self):
        settings = self.apply(cinny_host="cinny.example.org")
        self.assertEqual(matrix_setup.own_hosts(settings), {"matrix.example.org", "cinny.example.org"})


class PortsTest(unittest.TestCase):
    def test_range(self):
        ports = matrix_setup.ports(ENV)
        self.assertEqual(ports["synapse"], 20001)
        self.assertEqual(ports["postgres"], 20002)
        self.assertEqual(ports["linkedin"], 20015)
        self.assertEqual(ports["wellknown"], 20016)

    def test_positions_never_change(self):
        # Ports of existing installations depend on these positions
        self.assertEqual(matrix_setup.PORT_NAMES[:5], ["synapse", "postgres", "element", "cinny", "ketesa"])
        self.assertEqual(matrix_setup.PORT_NAMES[5:15], list(matrix_setup.BRIDGES))

    def test_build_demands_enough_ports(self):
        build = (Path(__file__).resolve().parents[1] / "build-images.sh").read_text()
        demand = int(build.split("org.nethserver.tcp-ports-demand=")[1].split('"')[0])
        self.assertGreaterEqual(demand, len(matrix_setup.PORT_NAMES))

    def test_too_few_ports(self):
        with self.assertRaises(RuntimeError):
            matrix_setup.ports({"TCP_PORT": "20001"})


class SynapseConfigTest(StateTest):
    def config(self, ldap=None, smarthost=None, **extra):
        return matrix_setup.homeserver_config(self.apply(**extra), matrix_setup.ports(ENV), ldap, smarthost)

    def test_basic(self):
        config = self.config(bridges=["whatsapp"])
        self.assertEqual(config["server_name"], "matrix.example.org")
        self.assertEqual(config["public_baseurl"], "https://matrix.example.org/")
        listener = config["listeners"][0]
        self.assertEqual(listener["port"], 20001)
        self.assertEqual(listener["bind_addresses"], ["127.0.0.1"])
        self.assertEqual(listener["resources"][0]["names"], ["client", "federation"])
        self.assertEqual(config["database"]["args"]["port"], 20002)
        self.assertTrue(config["serve_server_wellknown"])
        self.assertFalse(config["enable_registration"])
        self.assertEqual(config["app_service_config_files"], [
            "/data/config/appservices/doublepuppet.yaml",
            "/data/config/appservices/whatsapp.yaml",
        ])
        self.assertNotIn("modules", config)
        self.assertNotIn("email", config)

    def test_no_federation(self):
        config = self.config(federation=False)
        self.assertEqual(config["listeners"][0]["resources"][0]["names"], ["client"])
        self.assertEqual(config["federation_domain_whitelist"], [])
        self.assertNotIn("serve_server_wellknown", config)

    def test_delegated_server_name(self):
        config = self.config(server_name="example.org")
        # Synapse would announce example.org:443; wellknown.service does it
        self.assertNotIn("serve_server_wellknown", config)
        settings = matrix_setup.read_settings(self.state)
        self.assertEqual(matrix_setup.wellknown_documents(settings)["server"], {"m.server": "matrix.example.org:443"})
        self.assertFalse(matrix_setup.serves_wellknown(settings))
        settings = self.apply(wellknown_on_server_name=True)
        self.assertTrue(matrix_setup.serves_wellknown(settings))

    def test_ldap_ad(self):
        ldap = {
            "schema": "ad", "host": "127.0.0.1", "port": 20005, "base_dn": "dc=ad,dc=example,dc=org",
            "bind_dn": "ldapservice@AD", "bind_password": "pw", "hidden_users_clause": "(!(|(sAMAccountName=krbtgt)))",
        }
        module = self.config(ldap=ldap)["modules"][0]
        self.assertEqual(module["module"], "ldap_auth_provider.LdapAuthProviderModule")
        config = module["config"]
        self.assertEqual(config["uri"], "ldap://127.0.0.1:20005")
        self.assertEqual(config["base"], "dc=ad,dc=example,dc=org")
        self.assertEqual(config["attributes"]["uid"], "sAMAccountName")
        self.assertEqual(config["bind_password"], "pw")
        self.assertTrue(config["filter"].startswith("(&(objectCategory=person)"))
        self.assertIn("(!(|(sAMAccountName=krbtgt)))", config["filter"])
        self.assertTrue(config["filter"].endswith(")"))

    def test_ldap_openldap(self):
        ldap = {"schema": "rfc2307", "host": "127.0.0.1", "port": 20006, "base_dn": "dc=dir,dc=test",
                "bind_dn": "cn=ldapservice,dc=dir,dc=test", "bind_password": "pw", "hidden_users_clause": ""}
        config = self.config(ldap=ldap)["modules"][0]["config"]
        self.assertEqual(config["base"], "ou=People,dc=dir,dc=test")
        self.assertEqual(config["attributes"]["uid"], "uid")
        self.assertNotIn("filter", config)

    def test_email(self):
        smarthost = {"enabled": True, "host": "smtp.example.org", "port": 587, "username": "u",
                     "password": "p", "encrypt_smtp": "starttls", "tls_verify": True}
        email = self.config(smarthost=smarthost, element_host="chat.example.org")["email"]
        self.assertEqual(email["smtp_host"], "smtp.example.org")
        self.assertTrue(email["require_transport_security"])
        self.assertEqual(email["notif_from"], "%(app)s <noreply@matrix.example.org>")
        self.assertEqual(email["client_base_url"], "https://chat.example.org")
        smarthost.update(encrypt_smtp="tls", username="")
        email = self.config(smarthost=smarthost, mail_from="chat@example.org")["email"]
        self.assertTrue(email["force_tls"])
        self.assertNotIn("smtp_user", email)
        self.assertIn("<chat@example.org>", email["notif_from"])
        smarthost.update(enabled=False)
        self.assertNotIn("email", self.config(smarthost=smarthost))


class BridgeConfigTest(StateTest):
    def test_bridgev2(self):
        settings = self.apply(server_name="example.org", bridges=["whatsapp"])
        original = {"appservice": {"as_token": "keep", "bot": {"username": "whatsappbot"}}, "network": {"x": 1}}
        config = matrix_setup.patch_bridge_config(original, "whatsapp", settings, matrix_setup.ports(ENV))
        self.assertEqual(original["appservice"], {"as_token": "keep", "bot": {"username": "whatsappbot"}})
        self.assertEqual(config["appservice"]["as_token"], "keep")
        self.assertEqual(config["network"], {"x": 1})
        self.assertEqual(config["homeserver"], {"address": "http://127.0.0.1:20001", "domain": "example.org"})
        self.assertEqual(config["appservice"]["port"], 20006)
        self.assertEqual(config["database"]["uri"].split("@")[1], "127.0.0.1:20002/mautrix_whatsapp?sslmode=disable")
        self.assertEqual(config["bridge"]["permissions"], {"example.org": "user", "@matrixadmin:example.org": "admin"})
        self.assertEqual(config["double_puppet"]["secrets"]["example.org"],
                         "as_token:" + settings["secrets"]["doublepuppet_as_token"])
        self.assertTrue(config["encryption"]["allow"])
        self.assertEqual(config["logging"]["writers"], [{"type": "stdout", "format": "pretty"}])

    def test_legacy_discord(self):
        settings = self.apply(bridges=["discord"])
        config = matrix_setup.patch_bridge_config(None, "discord", settings, matrix_setup.ports(ENV))
        self.assertNotIn("database", config)
        self.assertEqual(config["appservice"]["database"]["type"], "postgres")
        self.assertIn("matrix.example.org", config["bridge"]["login_shared_secret_map"])
        self.assertTrue(config["bridge"]["encryption"]["allow"])

    def test_telegram_credentials(self):
        settings = self.apply(bridges=["telegram"], telegram_api_id="42", telegram_api_hash="h")
        config = matrix_setup.patch_bridge_config({}, "telegram", settings, matrix_setup.ports(ENV))
        self.assertEqual(config["network"]["api_id"], 42)
        self.assertEqual(config["network"]["api_hash"], "h")

    def test_registration_url_follows_port(self):
        registration = {"id": "signal", "url": "http://127.0.0.1:1", "as_token": "a"}
        patched = matrix_setup.patch_registration(registration, "signal", matrix_setup.ports(ENV))
        self.assertEqual(patched["url"], "http://127.0.0.1:20007")
        self.assertEqual(registration["url"], "http://127.0.0.1:1")

    def test_double_puppet_registration(self):
        settings = self.apply(server_name="example.org")
        registration = matrix_setup.double_puppet_registration(settings)
        self.assertIsNone(registration["url"])
        self.assertEqual(registration["namespaces"]["users"], [{"regex": "@.*:example\\.org", "exclusive": False}])


class CallsTest(StateTest):
    def maps(self):
        return matrix_setup.ports(ENV), matrix_setup.udp_ports(UDP_ENV)

    def test_udp_ports(self):
        udp = matrix_setup.udp_ports(UDP_ENV)
        self.assertEqual(udp, {"turn": 40001, "livekit": 40002, "relay_first": 40003, "relay_last": 40064})
        self.assertIsNone(matrix_setup.udp_ports({}))
        with self.assertRaises(RuntimeError):
            matrix_setup.udp_ports({"UDP_PORTS_RANGE": "40001-40010"})

    def test_build_demands_udp_ports_and_roles(self):
        build = (Path(__file__).resolve().parents[1] / "build-images.sh").read_text()
        demand = int(build.split("org.nethserver.udp-ports-demand=")[1].split('"')[0])
        self.assertEqual(demand, matrix_setup.UDP_PORTS_DEMAND)
        self.assertIn("node:fwadm,portsadm", build)
        self.assertEqual(matrix_setup.PORT_NAMES[16:], ["livekit", "livekit_tcp", "lk_jwt"])

    def test_disabled_by_default(self):
        settings = self.apply()
        self.assertFalse(matrix_setup.calls_enabled(settings))
        port_map, udp_map = self.maps()
        config = matrix_setup.homeserver_config(settings, port_map, udp_map=udp_map)
        for key in ("turn_uris", "matrix_rtc", "experimental_features", "max_event_delay_duration"):
            self.assertNotIn(key, config)
        self.assertEqual(matrix_setup.firewall_ports(settings, *self.maps()), [])
        self.assertNotIn("element_call", matrix_setup.element_config(settings))

    def test_turn(self):
        settings = self.apply(turn=True)
        port_map, udp_map = self.maps()
        config = matrix_setup.homeserver_config(settings, port_map, udp_map=udp_map)
        self.assertEqual(config["turn_uris"], ["turn:matrix.example.org:40001?transport=udp"])
        self.assertEqual(config["turn_shared_secret"], settings["secrets"]["turn_shared_secret"])
        self.assertFalse(config["turn_allow_guests"])
        conf = matrix_setup.coturn_config(settings, matrix_setup.udp_ports(UDP_ENV))
        self.assertIn("listening-port=40001\n", conf)
        self.assertIn("min-port=40003\nmax-port=40064\n", conf)
        self.assertIn("static-auth-secret=" + settings["secrets"]["turn_shared_secret"], conf)
        self.assertIn("denied-peer-ip=192.168.0.0-192.168.255.255", conf)
        self.assertIn("no-tcp\n", conf)
        self.assertNotIn("external-ip", conf)
        self.assertEqual(matrix_setup.firewall_ports(settings, *self.maps()), ["40001/udp", "40003-40064/udp"])
        # TURN needs UDP ports
        self.assertNotIn("turn_uris", matrix_setup.homeserver_config(settings, matrix_setup.ports(ENV)))

    def test_element_call(self):
        settings = self.apply(element_call=True, turn=True, calls_public_ip="93.184.216.34")
        port_map, udp_map = self.maps()
        config = matrix_setup.homeserver_config(settings, port_map, udp_map=udp_map)
        foci = [{"type": "livekit", "livekit_service_url": "https://matrix.example.org/livekit/jwt"}]
        self.assertEqual(config["matrix_rtc"], {"transports": foci})
        self.assertEqual(config["extra_well_known_client_content"], {"org.matrix.msc4143.rtc_foci": foci})
        self.assertTrue(config["experimental_features"]["msc4143_enabled"])
        self.assertTrue(config["experimental_features"]["msc4222_enabled"])
        self.assertEqual(config["max_event_delay_duration"], "24h")
        livekit = matrix_setup.livekit_config(settings, port_map, udp_map)
        self.assertEqual(livekit["port"], 20017)
        self.assertEqual(livekit["bind_addresses"], ["127.0.0.1"])
        self.assertEqual(livekit["rtc"]["tcp_port"], 20018)
        self.assertEqual(livekit["rtc"]["udp_port"], 40002)
        self.assertEqual(livekit["rtc"]["node_ip"], "93.184.216.34")
        self.assertFalse(livekit["rtc"]["use_external_ip"])
        self.assertEqual(livekit["rtc"]["turn_servers"][0]["secret"], settings["secrets"]["turn_shared_secret"])
        self.assertEqual(livekit["keys"], {settings["secrets"]["livekit_key"]: settings["secrets"]["livekit_secret"]})
        self.assertEqual(livekit["webhook"]["urls"], ["http://127.0.0.1:20019/sfu_webhook"])
        env = matrix_setup.lk_jwt_env(settings, port_map)
        self.assertEqual(env["LIVEKIT_URL"], "wss://matrix.example.org/livekit/sfu")
        self.assertEqual(env["LIVEKIT_JWT_BIND"], "127.0.0.1:20019")
        self.assertEqual(env["LIVEKIT_FULL_ACCESS_HOMESERVERS"], "matrix.example.org")
        self.assertIn("LIVEKIT_INSECURE_SKIP_VERIFY_TLS", env)
        self.assertNotIn("LIVEKIT_INSECURE_SKIP_VERIFY_TLS", matrix_setup.lk_jwt_env(self.apply(lets_encrypt=True), port_map))
        self.assertEqual(matrix_setup.lk_jwt_unit_env(settings), {"LK_JWT_HOST_ARGS": "--add-host=matrix.example.org:127.0.0.1"})
        self.assertEqual(matrix_setup.firewall_ports(settings, port_map, udp_map),
                         ["40001/udp", "40003-40064/udp", "40002/udp", "20018/tcp"])
        element = matrix_setup.element_config(settings)
        self.assertTrue(element["features"]["feature_group_calls"])
        self.assertTrue(element["element_call"]["use_exclusively"])
        self.assertNotIn("url", element["element_call"])

    def test_element_call_without_federation(self):
        port_map, udp_map = self.maps()
        config = matrix_setup.homeserver_config(self.apply(element_call=True, federation=False), port_map, udp_map=udp_map)
        # The JWT service checks OpenID tokens with the federation API
        self.assertEqual(config["listeners"][0]["resources"][0]["names"], ["client", "openid"])
        self.assertTrue(config["serve_server_wellknown"])
        self.assertEqual(config["federation_domain_whitelist"], [])

    def test_element_call_delegated(self):
        settings = self.apply(server_name="example.org", element_call=True, wellknown_on_server_name=True)
        client = matrix_setup.wellknown_documents(settings)["client"]
        self.assertEqual(client["org.matrix.msc4143.rtc_foci"][0]["livekit_service_url"], "https://matrix.example.org/livekit/jwt")
        self.assertEqual(matrix_setup.lk_jwt_local_hosts(settings), ["matrix.example.org", "example.org"])
        settings = self.apply(wellknown_on_server_name=False)
        # Someone else serves example.org: resolve it normally
        self.assertEqual(matrix_setup.lk_jwt_local_hosts(settings), ["matrix.example.org"])

    def test_public_ip_validation(self):
        for value in ("10.1.2.3", "127.0.0.1", "not-an-ip", "fd00::1"):
            with self.subTest(value=value):
                with self.assertRaises(matrix_setup.SettingsError) as raised:
                    self.apply(calls_public_ip=value)
                self.assertEqual(raised.exception.error, "invalid_ip_address")
        self.assertEqual(self.apply(calls_public_ip=" 2001:db8::1 ".replace("2001:db8::1", "2a00:1450::1"))["calls_public_ip"], "2a00:1450::1")

    def test_render_calls(self):
        settings = self.apply(turn=True, element_call=True)
        port_map, udp_map = self.maps()
        matrix_runtime.render_calls(settings, port_map, udp_map, self.state)
        self.assertEqual(self.mode("calls"), 0o700)
        for name in ("turnserver.conf", "livekit.yaml", "lk-jwt.env"):
            self.assertEqual(self.mode("calls", name), 0o600)
        flags = {n for n in os.listdir(self.state) if n.startswith("restart-needed-")}
        self.assertEqual(flags, {"restart-needed-coturn", "restart-needed-livekit", "restart-needed-lk-jwt"})
        # Nothing without UDP ports (instance before 0.2.0, calls disabled)
        other = tempfile.mkdtemp(dir=self.state)
        matrix_runtime.render_calls(settings, port_map, None, other)
        self.assertEqual(os.listdir(other), [])


class OutputTest(StateTest):
    def test_configuration_hides_secrets(self):
        settings = self.apply(bridges=["telegram"], telegram_api_id="1", telegram_api_hash="secret-hash")
        output = matrix_setup.configuration(settings, ["ad.example.org"], admin_ready=True)
        text = json.dumps(output)
        self.assertNotIn("secret-hash", text)
        self.assertNotIn(settings["secrets"]["postgres_password"], text)
        self.assertTrue(output["telegram_api_hash_set"])
        self.assertEqual(output["admin_user"], "@matrixadmin:matrix.example.org")
        self.assertEqual(output["admin_password"], settings["secrets"]["admin_password"])
        self.assertEqual(output["homeserver_url"], "https://matrix.example.org/")
        self.assertEqual(len(output["available_bridges"]), len(matrix_setup.BRIDGES))
        self.assertEqual(output["wellknown_server"], "")
        self.assertEqual(output["calls_ports"], [])
        self.assertNotIn(settings["secrets"]["turn_shared_secret"], text)
        self.assertNotIn(settings["secrets"]["livekit_secret"], text)
        self.assertEqual(matrix_setup.configuration(settings, [], admin_ready=False)["admin_password"], "")

    def test_output_schema_covers_all_keys(self):
        schema_path = Path(__file__).resolve().parents[1] / "imageroot/actions/get-configuration/validate-output.json"
        schema = json.loads(schema_path.read_text())
        output = matrix_setup.configuration(self.apply(), [], admin_ready=False)
        self.assertEqual(set(schema["required"]), set(output))

    def test_input_schema_lists_all_bridges(self):
        schema_path = Path(__file__).resolve().parents[1] / "imageroot/actions/configure-module/validate-input.json"
        schema = json.loads(schema_path.read_text())
        self.assertEqual(schema["properties"]["bridges"]["items"]["enum"], list(matrix_setup.BRIDGES))
        self.assertEqual(set(schema["properties"]), set(matrix_setup.DEFAULTS))


class RuntimeTest(StateTest):
    def fake_bridge(self, calls):
        def runner(args, **kwargs):
            calls.append(args)

            class Result:
                returncode = 0
            if args[:2] == ("podman", "run"):
                # The bridge writes its registration (like -g does)
                data = args[args.index("--volume") + 1].split(":")[0]
                Path(data, "registration.yaml").write_text("id: x\nurl: http://localhost:1\nas_token: t\n")
            return Result()
        return runner

    def test_render_all(self):
        settings = self.apply(bridges=["whatsapp", "discord"], element_host="chat.example.org")
        calls = []
        ports = matrix_setup.ports(ENV)
        matrix_runtime.render_all(settings, ports, state_dir=self.state, runner=self.fake_bridge(calls))
        self.assertEqual(len([c for c in calls if c[:2] == ("podman", "run")]), 2)
        self.assertEqual(self.mode("synapse", "homeserver.yaml"), 0o600)
        self.assertEqual(self.mode("bridges", "whatsapp", "config.yaml"), 0o600)
        self.assertEqual(self.mode("web", "element.json"), 0o644)
        self.assertEqual(self.mode("postgres.env"), 0o600)
        appservices = sorted(os.listdir(os.path.join(self.state, "synapse", "appservices")))
        self.assertEqual(appservices, ["discord.yaml", "doublepuppet.yaml", "whatsapp.yaml"])
        registration = Path(self.state, "synapse/appservices/whatsapp.yaml").read_text()
        self.assertIn("url: http://127.0.0.1:20006", registration)
        flags = {name for name in os.listdir(self.state) if name.startswith("restart-needed-")}
        self.assertIn("restart-needed-synapse", flags)
        self.assertIn("restart-needed-bridge-discord", flags)

        # A second run changes nothing and runs no container
        for name in flags:
            os.remove(os.path.join(self.state, name))
        calls.clear()
        matrix_runtime.render_all(settings, ports, state_dir=self.state, runner=self.fake_bridge(calls))
        self.assertEqual([c for c in calls if c[:2] == ("podman", "run")], [])
        self.assertEqual([n for n in os.listdir(self.state) if n.startswith("restart-needed-")], [])

        # Disabling a bridge removes its registration only
        settings = self.apply(bridges=["discord"])
        matrix_runtime.render_all(settings, ports, state_dir=self.state, runner=self.fake_bridge(calls))
        self.assertFalse(Path(self.state, "synapse/appservices/whatsapp.yaml").exists())
        self.assertTrue(Path(self.state, "bridges/whatsapp/registration.yaml").exists())

    def test_wellknown_files(self):
        settings = self.apply(server_name="example.org")
        matrix_runtime.render_web(settings, self.state)
        server = json.loads(Path(self.state, "web/wellknown/.well-known/matrix/server").read_text())
        self.assertEqual(server, {"m.server": "matrix.example.org:443"})

    def test_registration_mac(self):
        # Reference implementation from the Synapse admin API documentation
        mac = hmac.new(b"secret", digestmod=hashlib.sha1)
        mac.update(b"nonce\x00user\x00pass\x00admin")
        self.assertEqual(matrix_runtime.registration_mac("secret", "nonce", "user", "pass"), mac.hexdigest())

    def test_ensure_admin(self):
        settings = self.apply()
        requests = []

        def request(url, data=None):
            requests.append((url, data))
            return (200, {"nonce": "n"}) if data is None else (400, {"errcode": "M_USER_IN_USE"})

        self.assertTrue(matrix_runtime.ensure_admin(settings, matrix_setup.ports(ENV), self.state, request=request))
        self.assertEqual(requests[1][1]["username"], "matrixadmin")
        self.assertTrue(requests[1][1]["admin"])
        # Once created, nothing is sent again
        self.assertFalse(matrix_runtime.ensure_admin(settings, matrix_setup.ports(ENV), self.state, request=request))
        self.assertEqual(len(requests), 2)

    def test_ensure_admin_failure(self):
        settings = self.apply()

        def request(url, data=None):
            return (200, {"nonce": "n"}) if data is None else (403, {"errcode": "M_FORBIDDEN"})

        with self.assertRaises(RuntimeError):
            matrix_runtime.ensure_admin(settings, matrix_setup.ports(ENV), self.state, request=request)
        self.assertFalse(Path(self.state, matrix_runtime.ADMIN_FLAG).exists())


if __name__ == "__main__":
    unittest.main()
