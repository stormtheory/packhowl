#!/usr/bin/env python3.12
"""
Pack Howl – secure voice/chat server
• Async-io TLS server enforcing mutual authentication
• Debug mode prints live user table
• Drops clients with unknown certificates
"""

import argparse, asyncio, json, ssl, time, base64
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict
from config import (SERVER_BIND, SSL_CERT_PATH, SSL_CA_PATH, MAX_USERS, CERTS_DIR, APP_NAME,
                    CN_WHITELIST_PATH, SERVER_IP_BLOCK_DURATION, ensure_data_dirs)
from config import SERVER_PORT as PORT
import logging

# ── Argument parser for debug mode ──────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("-d", "--debug", action='store_true', help='Run GUI in debug mode')
args = parser.parse_args()

### SET LOGGING LEVEL
logger = logging.getLogger()
if args.debug:
    logger.setLevel(logging.DEBUG)     # INFO, DEBUG
else:
    logger.setLevel(logging.INFO)     # INFO, DEBUG

logging.debug(f"CERT: {SSL_CERT_PATH}, CA: {SSL_CA_PATH}")

# ── Helper: get user list (used in UI broadcast) ────────────────────────────
def get_user_list(self) -> list[dict]:
    """Build list of connected users with display name and IP."""
    return [{"name": c.cn, "ip": c.ip} for c in self.clients.values()]


###############################################################################
# ─── ERROR CHECKING ─────────────────────────────────────────────────────────
###############################################################################
PROMPT_EXIT = False

if CERTS_DIR.is_dir():
    pass
else:
    PROMPT_EXIT = True
    print(f"\n ERROR: Directory missing: {CERTS_DIR}\n ")

if CN_WHITELIST_PATH.is_file():
    pass
else:
    PROMPT_EXIT = True
    print(f"\n \n ERROR: File missing: {CN_WHITELIST_PATH}")
    print(f"\n This file is the whitelist for the hosts by hostname. \n It contains the list of allowed hostnames. \n \n Example file:\nclient1 \nclient2 \nclient3 \n")
    
if SSL_CA_PATH.is_file():
    pass
else:
    PROMPT_EXIT = True
    print(f"\n \n ERROR: File missing: {SSL_CA_PATH}")
    print(f"\n This file: {SSL_CA_PATH} \n needs to be generated at the \n server and shared with this client in {CERTS_DIR} \n")

if SSL_CERT_PATH.is_file():
    pass
else:
    PROMPT_EXIT = True
    print(f"\n \n ERROR: File missing: {SSL_CERT_PATH}")
    print(f"\n This file: {SSL_CERT_PATH} \n needs to be generated at the \n server and shared with this client in {CERTS_DIR} \n")
    
if PROMPT_EXIT is True:
    print('Exiting...')
    exit()


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

    # ── NEW: voice / mute bookkeeping ────────────────────────────────────────
    tx: bool = False             # True while client is actively sending audio
    muted: bool = False          # True if client set mic mute
    spk_muted: bool = False
    last_audio: float = 0.0      # Timestamp of last audio frame received

###############################################################################
# ─── Server core ────────────────────────────────────────────────────────────
###############################################################################

class Server:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.clients: Dict[str, ClientInfo] = {}  # key = CN

        # ── CN whitelist: Load from file ───────────────────────────────────────────
        self.cn_whitelist = set()
        
        self.blocked_ips: Dict[str, float] = {}  # IP → timestamp when block was set
        self.block_duration = SERVER_IP_BLOCK_DURATION  # seconds (5 minutes block)

        if CN_WHITELIST_PATH.is_file():
            with CN_WHITELIST_PATH.open("r") as f:
                self.cn_whitelist = {line.strip() for line in f if line.strip()}
            if self.debug:
                self.log(f"[DEBUG] Loaded CN whitelist: {self.cn_whitelist}")
        else:
            self.log(f"[WARN] CN whitelist file missing: {CN_WHITELIST_PATH}")

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
        cn = "UNKNOWN"
        try:
            peername = writer.get_extra_info("peername")[0]

            # ── IP block check ─────────────────────────────────────────────────────────
            now = time.time()
            if peername in self.blocked_ips:
                if now - self.blocked_ips[peername] < self.block_duration:
                    self.log(f"[BLOCK] Connection denied from blocked IP {peername}")
                    writer.close()
                    await writer.wait_closed()
                    return
                else:
                    # Auto-unblock expired IP
                    del self.blocked_ips[peername]
            
            cert = writer.get_extra_info("peercert")
            cn = cert["subject"][0][0][1] if cert else "UNKNOWN"

            # ── Enforce CN whitelist ───────────────────────────────────────────────────
            if cn not in self.cn_whitelist:
                self.blocked_ips[peername] = time.time()  # Add to temporary blocklist
                self.log(f"[DENY] CN '{cn}' not in whitelist. Blocking IP {peername}")
                writer.close()
                await writer.wait_closed()
                return

            if len(self.clients) >= MAX_USERS:
                writer.close()
                await writer.wait_closed()
                return

            info = ClientInfo(reader=reader, writer=writer, cn=cn, ip=peername)
            self.clients[cn] = info
            self.log(f"+ {cn} @ {peername}")

            await self.broadcast_user_list()  # broadcast on new connection

            if self.debug:
                self.print_user_table()

            # ▒▒▒ JSON Checker ▒▒▒
            def validate_msg(msg: dict) -> bool:
                """Very basic message format validator."""
                if "type" not in msg:
                    return False

                msg_type = msg["type"]

                if msg_type == "init":
                    return isinstance(msg.get("name"), str) and isinstance(msg.get("ip"), str)
                elif msg_type == "status":
                    return isinstance(msg.get("muted", False), bool) and isinstance(msg.get("spk_muted", False), bool)
                elif msg_type == "audio":
                    return isinstance(msg.get("data"), str)  # base64 encoded frame
                elif msg_type == "muted":
                    return isinstance(msg.get("value"), bool)
                elif msg_type == "chat":
                    return isinstance(msg.get("text"), str)
                else:
                    return False  # unknown type
                
            # ── Protocol: line-delimited JSON messages ────────────────────
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                if len(raw) > 4096:  # prevent large messages from consuming memory
                    self.log(f"[ABUSE] Dropping {cn} - message too long")
                    break

                try:
                    msg = json.loads(raw.decode())
                    
                    if not validate_msg(msg):    
                        self.log(f"[ABUSE] Invalid json structure from {cn}")
                        break

                    # Handle init message specially before broadcast
                    if msg.get("type") == "init":
                        # Update client info
                        self.clients[cn].cn = msg.get("name", cn)
                        self.clients[cn].ip = msg.get("ip", peername)
                        self.clients[cn].muted = msg.get("muted", False)
                        self.clients[cn].spk_muted = msg.get("spk_muted", False)
                        await self.broadcast_user_list()
                        continue  # don't forward 'init' to others
                    
                    elif msg.get("type") == "status":
                        self.clients[cn].spk_muted = bool(msg.get("spk_muted", False))
                        self.clients[cn].muted = bool(msg.get("muted", False))
                        await self.broadcast_user_list()                # push TX status
                        #await self.broadcast(msg, exclude=cn)           # relay frame

                    # Handle Opus audio frame forwarding
                    elif msg.get("type") == "audio":
                        if "data" in msg:                                   # ── NEW: changed key 'frame' → 'data'
                            self.clients[cn].tx = True                       # mark talking
                            self.clients[cn].last_audio = time.time()
                            await self.broadcast_user_list()                # push TX status
                            await self.broadcast(msg, exclude=cn)           # relay frame
                        else:
                            self.log(f"[WARN] invalid audio msg from {cn}")

                    # ── NEW: mute/unmute message -------------------------------------------------
                    elif msg.get("type") == "muted":
                        self.clients[cn].muted = bool(msg.get("value", False))
                        await self.broadcast_user_list()
                    # ---------------------------------------------------------------------------

                    # Handle chat or control messages
                    else:
                        await self.broadcast(msg, exclude=cn)

                except Exception as exc:
                    self.log(f"[WARN] bad msg from {cn}: {exc}")

        except ssl.SSLError as e:
            self.log(f"[TLS] {e}")
        except Exception as e:
            self.log(f"[ERR] {e}")
        finally:
            # Clean-up client on disconnect
            self.clients.pop(cn, None)
            try:
                writer.write_eof()  # optional, may not be supported
            except Exception:
                pass

            try:
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                self.log(f"[WARN] Close error: {e}")

            # Broadcast updated user list after disconnect
            await self.broadcast_user_list()

            self.log(f"- {cn}")
            if self.debug:
                self.print_user_table()

    # ▒▒▒ broadcast user list ▒▒▒
    async def broadcast_user_list(self):
        """Send the updated user list to all connected clients."""
        user_list = []
        for client in self.clients.values():
            user_list.append({
                "name":  client.cn,
                "ip":    client.ip,
                "tx":    client.tx,     # ── NEW: actively transmitting flag
                "muted": client.muted,   # ── NEW: mic muted flag
                "spk_muted": client.spk_muted
            })

        message = json.dumps({
            "type": "userlist",
            "users": user_list
        }) + "\n"

        for client in self.clients.values():
            try:
                client.writer.write(message.encode())
                await client.writer.drain()
            except Exception as e:
                self.log(f"[WARN] Failed to send user list to {client.cn}: {e}")

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

    # ── periodic watcher to reset TX after silence ──────────────────────
    async def _voice_watcher(self):
        """Clears tx flag ~300 ms after last audio frame to keep indicator fresh."""
        while True:
            await asyncio.sleep(0.3)
            now = time.time()
            dirty = False
            for c in self.clients.values():
                if c.tx and now - c.last_audio > 0.3:
                    c.tx = False
                    dirty = True
            if dirty:
                await self.broadcast_user_list()
                
    # ── NEW: periodic IP‑blocklist cleaner ────────────────────────────────
    async def _ip_blocklist_cleaner(self):
        """
        Periodically purge entries from self.blocked_ips once their
        block duration has expired.  Keeps memory footprint tight and
        avoids need for manual unblock after timeout.
        """
        while True:
            await asyncio.sleep(60)          # run once a minute
            now = time.time()
            # keep only still‑valid blocks
            self.blocked_ips = {
                ip: ts for ip, ts in self.blocked_ips.items()
                if now - ts < self.block_duration
            }

    # ▒▒▒ util: logging ▒▒▒
    def log(self, *a) -> None:
        """Simple timestamped print."""
        print(time.strftime("[%H:%M:%S]"), *a, flush=True)

    def print_user_table(self) -> None:
        table = ", ".join(f"{c.cn}@{c.ip}" for c in self.clients.values())
        logging.debug(f"Connected users ({len(self.clients)}/{MAX_USERS}): {table}")

    # ▒▒▒ entry-point ▒▒▒
    async def run(self) -> None:
        ensure_data_dirs()
        server = await asyncio.start_server(
            self.handle_client, SERVER_BIND, PORT, ssl=self.ssl_ctx
        )
        addr = ", ".join(str(sock.getsockname()) for sock in server.sockets)
        self.log(f"[{APP_NAME}] serving on {addr}")

        #logging.debug(f"🔐 TLS version: {conn.version()}, cipher: {conn.cipher()}")

        # ── Kick off background maintenance tasks ──────────────────────────
        asyncio.create_task(self._voice_watcher())       # keep “TX” state fresh
        asyncio.create_task(self._ip_blocklist_cleaner())# auto‑purge IP blocks

        async with server:
            await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true",
                        help="print live user table on connect/disconnect")
    args = parser.parse_args()
    srv = Server(debug=args.debug)
    try:
        asyncio.run(srv.run())
    except KeyboardInterrupt:
        print("\n[shutdown]")

if __name__ == "__main__":
    main()
