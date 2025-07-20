#!/usr/bin/env python3.12
"""
Silent Link – secure voice/chat server
• Async-io TLS server enforcing mutual authentication
• Debug mode prints live user table
• Drops clients with unknown certificates
"""

import argparse, asyncio, json, ssl, time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict
from common import (PORT, SERVER_BIND, SSL_CERT_PATH, SSL_CA_PATH, MAX_USERS,
                    ensure_data_dirs)

print(f"CERT: {SSL_CERT_PATH}, CA: {SSL_CA_PATH}")

###############################################################################
# ─── Data structures ────────────────────────────────────────────────────────
###############################################################################

@dataclass
class ClientInfo:
    """Represents a connected client."""
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    cn: str                      # CommonName (display name)
    ip: str
    connected_at: float = field(default_factory=time.time)

###############################################################################
# ─── Server core ────────────────────────────────────────────────────────────
###############################################################################

class SilentLinkServer:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.clients: Dict[str, ClientInfo] = {}  # key = CN

        # --- Configure SSL context (server side, mutual TLS) --------------
       # 🔐 Create hardened TLS server context
        self.ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

        # Load server certificate (combined .pem with cert + key)
        self.ssl_ctx.load_cert_chain(certfile=str(SSL_CERT_PATH))

        # Load internal CA cert (for verifying client certs)
        self.ssl_ctx.load_verify_locations(cafile=str(SSL_CA_PATH))

        # Require client certificates
        self.ssl_ctx.verify_mode = ssl.CERT_REQUIRED

        # Use only strong TLS 1.2 cipher suites (TLS 1.3 is used automatically)
        self.ssl_ctx.set_ciphers("ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384")

        # Disable insecure TLS versions
        self.ssl_ctx.options |= ssl.OP_NO_TLSv1
        self.ssl_ctx.options |= ssl.OP_NO_TLSv1_1

        # (Optional) Disable TLS 1.2 if you're 100% TLS 1.3 capable
        self.ssl_ctx.options |= ssl.OP_NO_TLSv1_2

        # Prefer server-side cipher order
        self.ssl_ctx.options |= ssl.OP_CIPHER_SERVER_PREFERENCE

        # Hardened TLS context (Python 3.12+, TLS 1.3 preferred)
        self.ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ssl_ctx.load_cert_chain(certfile=str(SSL_CERT_PATH))
        self.ssl_ctx.load_verify_locations(cafile=str(SSL_CA_PATH))
        self.ssl_ctx.verify_mode = ssl.CERT_REQUIRED

        # Disable legacy TLS versions and compression
        self.ssl_ctx.options |= (
            ssl.OP_NO_TLSv1 |
            ssl.OP_NO_TLSv1_1 |
            ssl.OP_NO_COMPRESSION |
            ssl.OP_CIPHER_SERVER_PREFERENCE
        )

        # Optional: restrict to TLS 1.2 ciphers if TLS 1.3 is not available
        self.ssl_ctx.set_ciphers("ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384")
        

    # ▒▒▒ connection handler ▒▒▒
    async def handle_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> None:
        try:
            peername = writer.get_extra_info("peername")[0]
            cert = writer.get_extra_info("peercert")
            cn = cert["subject"][0][0][1] if cert else "UNKNOWN"
            if len(self.clients) >= MAX_USERS:
                writer.close()
                await writer.wait_closed()
                return

            info = ClientInfo(reader=reader, writer=writer, cn=cn, ip=peername)
            self.clients[cn] = info
            self.log(f"+ {cn} @ {peername}")

            # ── Protocol: line-delimited JSON messages ────────────────────
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                try:
                    msg = json.loads(raw.decode())
                    # Echo chat/audio control to all other clients
                    await self.broadcast(msg, exclude=cn)
                except Exception as exc:
                    self.log(f"[WARN] bad msg from {cn}: {exc}")

        except ssl.SSLError as e:
            self.log(f"[TLS] {e}")
        except Exception as e:
            self.log(f"[ERR] {e}")
        finally:
            # Clean-up
            self.clients.pop(cn, None)
            writer.close()
            await writer.wait_closed()
            self.log(f"- {cn}")
            if self.debug:
                self.print_user_table()

    # ▒▒▒ broadcast helper ▒▒▒
    async def broadcast(self, msg: dict, exclude: str | None = None) -> None:
        """Send JSON line to every client except `exclude`."""
        data = (json.dumps(msg) + "\n").encode()
        for cn, c in list(self.clients.items()):
            if cn == exclude:
                continue
            try:
                c.writer.write(data)
                await c.writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                self.clients.pop(cn, None)

    # ▒▒▒ util: logging ▒▒▒
    def log(self, *a) -> None:
        """Simple timestamped print."""
        print(time.strftime("[%H:%M:%S]"), *a, flush=True)

    def print_user_table(self) -> None:
        table = ", ".join(f"{c.cn}@{c.ip}" for c in self.clients.values())
        print(f"Connected users ({len(self.clients)}/{MAX_USERS}): {table}")

    # ▒▒▒ entry-point ▒▒▒
    async def run(self) -> None:
        ensure_data_dirs()
        server = await asyncio.start_server(
            self.handle_client, SERVER_BIND, PORT, ssl=self.ssl_ctx
        )
        addr = ", ".join(str(sock.getsockname()) for sock in server.sockets)
        self.log(f"[SILENT LINK] serving on {addr}")
        
        #print(f"🔐 TLS version: {conn.version()}, cipher: {conn.cipher()}")


        async with server:
            await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true",
                        help="print live user table on connect/disconnect")
    args = parser.parse_args()
    srv = SilentLinkServer(debug=args.debug)
    try:
        asyncio.run(srv.run())
    except KeyboardInterrupt:
        print("\n[shutdown]")

if __name__ == "__main__":
    main()
