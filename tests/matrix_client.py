#!/usr/bin/env python3

#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Minimal Matrix client for the module tests, run on the NS8 node.

It talks to Synapse through Traefik (https://127.0.0.1 with the Host header
of the homeserver) and prints JSON results. Exit status 1 means the check
failed; the reason is on stderr.
"""

import argparse
import hashlib
import hmac
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CONTEXT = ssl.create_default_context()
CONTEXT.check_hostname = False
CONTEXT.verify_mode = ssl.CERT_NONE


class Client:
    def __init__(self, host, token=None):
        self.host = host
        self.token = token

    def request(self, method, path, body=None, expect=(200,)):
        headers = {"Host": self.host, "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request("https://127.0.0.1" + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, context=CONTEXT, timeout=30) as response:
                status, raw, ctype = response.status, response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            status, raw, ctype = error.code, error.read(), error.headers.get("Content-Type", "")
        if status not in expect:
            raise SystemExit(f"{method} {path}: HTTP {status} {raw[:300]!r}")
        try:
            return json.loads(raw), ctype
        except ValueError:
            return raw.decode(errors="replace"), ctype

    def call(self, method, path, body=None, expect=(200,)):
        return self.request(method, "/_matrix/client/v3" + path, body, expect)[0]


def login(client, user, password):
    answer = client.call("POST", "/login", {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": user},
        "password": password,
    }, expect=(200, 403))
    if "access_token" not in answer:
        raise SystemExit(f"login of {user} failed: {answer}")
    return answer


def wait(check, timeout, what):
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() > deadline:
            raise SystemExit(f"timed out waiting for {what}")
        time.sleep(2)


def messages(client, room):
    path = f"/rooms/{urllib.parse.quote(room)}/messages?dir=b&limit=50"
    return client.call("GET", path)["chunk"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--token")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("versions")
    get = sub.add_parser("get")
    get.add_argument("path")
    p = sub.add_parser("login")
    p.add_argument("user")
    p.add_argument("password")
    p = sub.add_parser("bad-login")
    p.add_argument("user")
    p.add_argument("password")
    p = sub.add_parser("create-room")
    p.add_argument("invite")
    p.add_argument("body")
    p = sub.add_parser("read")
    p.add_argument("room")
    p.add_argument("body")
    p = sub.add_parser("bot-reply")
    p.add_argument("bot")
    sub.add_parser("whoami")
    p = sub.add_parser("stun")
    p.add_argument("port", type=int)
    sub.add_parser("sfu-token")
    p = sub.add_parser("ssa-login")
    p.add_argument("user_id")
    p.add_argument("secret")
    p = sub.add_parser("room-command")
    p.add_argument("room")
    p.add_argument("bot")
    p.add_argument("body")
    p = sub.add_parser("server-admin")
    p.add_argument("user_id")
    args = parser.parse_args()
    client = Client(args.host, args.token)

    if args.command == "versions":
        result = client.request("GET", "/_matrix/client/versions")[0]
    elif args.command == "get":
        body, ctype = client.request("GET", args.path)
        result = {"body": body, "content_type": ctype}
    elif args.command == "login":
        result = login(client, args.user, args.password)
    elif args.command == "bad-login":
        answer = client.call("POST", "/login", {
            "type": "m.login.password",
            "identifier": {"type": "m.id.user", "user": args.user},
            "password": args.password,
        }, expect=(403,))
        result = {"errcode": answer.get("errcode")}
    elif args.command == "create-room":
        room = client.call("POST", "/createRoom", {"invite": [args.invite], "preset": "private_chat"})["room_id"]
        client.call("PUT", f"/rooms/{urllib.parse.quote(room)}/send/m.room.message/t{time.time_ns()}",
                    {"msgtype": "m.text", "body": args.body})
        result = {"room_id": room}
    elif args.command == "read":
        room = urllib.parse.quote(args.room)
        # Join if invited; joining twice is fine
        client.call("POST", f"/rooms/{room}/join", {})
        bodies = [e["content"].get("body") for e in messages(client, args.room) if e["type"] == "m.room.message"]
        if args.body not in bodies:
            raise SystemExit(f"message {args.body!r} not found in {bodies}")
        result = {"found": True}
    elif args.command == "bot-reply":
        room = client.call("POST", "/createRoom", {"invite": [args.bot], "is_direct": True, "preset": "trusted_private_chat"})["room_id"]
        quoted = urllib.parse.quote(room)
        wait(lambda: args.bot in client.call("GET", f"/rooms/{quoted}/joined_members")["joined"], 120, f"{args.bot} to join")
        client.call("PUT", f"/rooms/{quoted}/send/m.room.message/t{time.time_ns()}", {"msgtype": "m.text", "body": "help"})
        reply = wait(
            lambda: next((e["content"].get("body") for e in messages(client, room)
                          if e["sender"] == args.bot and e["type"] == "m.room.message"), None),
            120, f"a message from {args.bot}",
        )
        result = {"room_id": room, "reply": reply[:200]}
    elif args.command == "stun":
        # RFC 5389 binding request to coturn on this node
        transaction = os.urandom(12)
        request = b"\x00\x01\x00\x00" + b"\x21\x12\xa4\x42" + transaction
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(5)
            udp.sendto(request, ("127.0.0.1", args.port))
            answer = udp.recv(2048)
        if answer[:2] != b"\x01\x01" or answer[8:20] != transaction:
            raise SystemExit(f"no STUN binding success response: {answer[:20].hex()}")
        result = {"stun": "ok"}
    elif args.command == "sfu-token":
        # What Element Call does: OpenID token from Synapse, then a LiveKit
        # token from the JWT service (legacy /sfu/get API)
        whoami = client.call("GET", "/account/whoami")
        quoted = urllib.parse.quote(whoami["user_id"])
        openid = client.call("POST", f"/user/{quoted}/openid/request_token", {})
        room = client.call("POST", "/createRoom", {"preset": "private_chat"})["room_id"]
        anonymous = Client(args.host)
        answer, _ = anonymous.request("POST", "/livekit/jwt/sfu/get", {
            "room": room,
            "openid_token": openid,
            "device_id": whoami.get("device_id") or "ROBOT",
        })
        if not isinstance(answer, dict) or not answer.get("jwt"):
            raise SystemExit(f"no LiveKit token: {answer}")
        result = {"url": answer.get("url"), "jwt_parts": len(answer["jwt"].split("."))}
    elif args.command == "ssa-login":
        # matrix-synapse-shared-secret-auth: token = HMAC-SHA512(secret, user ID)
        token = hmac.new(args.secret.encode(), args.user_id.encode(), hashlib.sha512).hexdigest()
        answer = client.call("POST", "/login", {
            "type": "com.devture.shared_secret_auth",
            "identifier": {"type": "m.id.user", "user": args.user_id},
            "token": token,
        }, expect=(200, 403))
        if "access_token" not in answer:
            raise SystemExit(f"shared-secret login failed: {answer}")
        result = {"user_id": answer["user_id"]}
    elif args.command == "room-command":
        quoted = urllib.parse.quote(args.room)
        client.call("POST", f"/rooms/{quoted}/join", {})
        client.call("PUT", f"/rooms/{quoted}/send/m.room.message/t{time.time_ns()}",
                    {"msgtype": "m.text", "body": args.body})
        reply = wait(
            lambda: next((e["content"].get("body") for e in messages(client, args.room)
                          if e["sender"] == args.bot and e["type"] == "m.room.message"), None),
            120, f"a message from {args.bot}",
        )
        result = {"reply": reply[:300]}
    elif args.command == "whoami":
        result = client.call("GET", "/account/whoami")
    elif args.command == "server-admin":
        quoted = urllib.parse.quote(args.user_id)
        result = client.request("GET", f"/_synapse/admin/v2/users/{quoted}")[0]
        result = {"admin": result.get("admin"), "name": result.get("name")}
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
