# client/network.py

import ssl
import socket
import asyncio
import json
import traceback
import logging

from PySide6 import QtCore

from client.settings import Settings

from config import (APP_NAME, APP_ICON_PATH, CLIENT_IP, SSL_CA_PATH, CERTS_DIR,
                    DATA_DIR, ensure_data_dirs)
from config import SERVER_PORT as DEFAULT_SERVER_PORT
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')


###############################################################################
# ─── ERROR CHECK ────────────────────────────────────────────────────────────
###############################################################################

CLIENT_CERT_PATH = CERTS_DIR / f"{socket.gethostname()}.pem"

if not CLIENT_CERT_PATH.exists():
    raise FileNotFoundError(f"Client cert not found: {CLIENT_CERT_PATH}")


###############################################################################
# ─── Networking Thread ─────────────────────────────────────────────────────
###############################################################################

"""
Network thread handling asyncio TLS client connection.
• Runs in a QThread to not block the Qt event loop
• Handles auto-reconnect with back-off
• Sends/receives JSON messages over TLS socket
• Emits PySide6 signals for status updates, user list, chat messages
"""

### Load config
settings = Settings()

class NetworkThread(QtCore.QThread):
    """
    Runs asyncio TLS client loop without blocking the Qt event loop.
    Emits:
        - status (str): connection status messages
        - userlist (list): list of users received from server
        - chatmsg (dict): chat messages from server
    """

    status = QtCore.Signal(str)
    userlist = QtCore.Signal(list)
    chatmsg = QtCore.Signal(dict)

    def __init__(self, settings, audio_engine=None):
        super().__init__()
        self.settings = settings
        self.audio_engine = audio_engine
        global SERVER_PORT
        SERVER_PORT = self.settings.get("server_port", 12345)
        self._stop = False
        self._loop = None  # Event loop reference for coroutine scheduling
        self.outbound_queue = asyncio.Queue()  # Thread-safe outbound message queue

    def run(self):
        """
        Entry point for QThread, runs the asyncio event loop.
        """
        asyncio.run(self._main())

    async def _main(self):
        """
        Main coroutine loop handling connect, reconnect, and messaging.
        """
        self._loop = asyncio.get_running_loop()
        while not self._stop:
            try:
                await self._connect_and_loop()
            except Exception as e:
                self.status.emit(f"[ERR] Network error: {e}")
                traceback.print_exc()

            # Auto-reconnect back-off countdown
            for i in range(5, 0, -1):
                if self._stop:
                    return
                self.status.emit(f"[INFO] Reconnecting in {i}s...")
                await asyncio.sleep(1)

    async def _connect_and_loop(self):
        ip = self.settings["server_ip"]
        self.status.emit(f"[INFO] connecting to {ip}:{SERVER_PORT}")

        # Build TLS context (client side, mutual auth)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_2  # TLS 1.3 only
        ctx.load_verify_locations(cafile=str(SSL_CA_PATH))
        ctx.load_cert_chain(certfile=str(CLIENT_CERT_PATH))  # your `client.pem`
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED

        # NOTE: load client cert/key here if you require client auth
        reader, writer = await asyncio.open_connection(
            host=ip, port=SERVER_PORT, ssl=ctx, local_addr=(CLIENT_IP, 0)
        )
        self.status.emit("[OK] connected")

        # Send "hello" with display_name
        hello = {
                "type": "init",
                "name": self.settings["display_name"],
                "ip": socket.gethostbyname(socket.gethostname())  # add IP if needed
            }

        writer.write((json.dumps(hello) + "\n").encode())
        await writer.drain()

        send_task = asyncio.create_task(self._send_outgoing(writer))  # background sender loop

        # main RX loop
        while not reader.at_eof() and not self._stop:
            line = await reader.readline()
            if not line or self._stop:
                break
            msg = json.loads(line.decode())
            msg_type = msg.get("type")

            # Handle incoming audio packets
            if msg_type == "audio" and hasattr(self, "audio_engine"):
                logging.debug(f"[Net RX] Received audio packet: {len(msg['data']) // 2} bytes")
                self.audio_engine.enqueue_audio_threadsafe(msg["data"])
                continue  # skip further processing for this packet

            # Handle other known message types
            match msg_type:
                case "userlist":
                    self.userlist.emit(msg["users"])
                case "chat":
                    self.chatmsg.emit(msg)
                case _:
                    logging.debug(f"[Net RX] Unknown message type: {msg_type}")


        send_task.cancel()
        try:
            await send_task
        except asyncio.CancelledError:
            pass


        self.status.emit("[WARN] server closed connection")
        try:
            writer.write_eof()
        except Exception:
            pass
        try:
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        except Exception as e:
            print(f"[WARN] Close error: {e}")

    async def _send_outgoing(self, writer):
        try:
            while not self._stop:
                msg = await self.outbound_queue.get()

                if msg.get("type") == "audio":
                    logging.debug(f"[Net TX] Sending audio packet ({len(msg['data']) // 2} bytes)")

                writer.write((json.dumps(msg) + "\n").encode())
                await writer.drain()
                self.outbound_queue.task_done()
        except asyncio.CancelledError:
            pass


    def queue_message(self, msg: dict):
        """
        Thread-safe method to queue messages for sending.
        Can be called from GUI thread.
        """
        if self._loop and not self._stop:
            asyncio.run_coroutine_threadsafe(
                self.outbound_queue.put(msg), self._loop
            )

    def stop(self):
        """
        Signals the thread to stop gracefully.
        """
        self._stop = True
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
