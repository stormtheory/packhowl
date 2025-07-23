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
        self.server_ip = self.settings.get("server_ip", 12345)
        self.server_port = self.settings.get("server_port", 12345)
        self._need_reconnect = False
        self._stop = False
        self._reader = None
        self._writer = None
        self._send_task = None
        self._loop = None  # Event loop reference for coroutine scheduling
        self.outbound_queue = asyncio.Queue()  # Thread-safe outbound message queue

    def run(self):
        """
        Entry point for QThread, runs the asyncio event loop.
        """
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception:
            traceback.print_exc()
        finally:
            self._loop.close()
            self._loop = None

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
        self.status.emit(f"[INFO] connecting to {ip}:{self.server_port}")
        
        def get_local_ip():
            try:
                # This doesn't send any traffic, it just selects the appropriate local IP
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(('1.1.1.1', DEFAULT_SERVER_PORT))  # Use a public DNS for reference
                    return s.getsockname()[0]
            except Exception:
                return "127.0.0.1"  # fallback
        
        # Build TLS context (client side, mutual auth)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_TLSv1_2  # TLS 1.3 only
        ctx.load_verify_locations(cafile=str(SSL_CA_PATH))
        ctx.load_cert_chain(certfile=str(CLIENT_CERT_PATH))  # your `client.pem`
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED

        # NOTE: load client cert/key here if you require client auth
        # ─── Connect Securely ───────────────────────────────────────────────
        self._reader, self._writer = await asyncio.open_connection(
            host=self.server_ip,
            port=self.server_port,
            ssl=ctx,
            local_addr=(CLIENT_IP, 0)
        )

        # Start the background sender loop and keep a handle so we can cancel it
        self._send_task = asyncio.create_task(self._send_outgoing(self._writer))



        self.status.emit("[OK] connected")

        # Send "hello" with display_name
        hello = {
                "type": "init",
                "name": self.settings["display_name"],
                "ip": get_local_ip(),
                "spk_muted": "False",    # Default
                "muted": "True"    # Default
            }

        self._writer.write((json.dumps(hello) + "\n").encode())
        await self._writer.drain()

        # main RX loop
        while not self._reader.at_eof() and not self._stop:
            try:
                line = await asyncio.wait_for(self._reader.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                continue  # check _stop and keep waiting

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
                case "status":
                    self.userlist.emit(msg["users"])
                case "chat":
                    self.chatmsg.emit(msg)
                case _:
                    logging.debug(f"[Net RX] Unknown message type: {msg_type}")


        self._send_task.cancel()
        try:
            await self._send_task
        except asyncio.CancelledError:
            pass


        self.status.emit("[WARN] server closed connection")
        try:
            self._writer.write_eof()
        except Exception:
            pass
        try:
            await self._writer.drain()
            self._writer.close()
            await self._writer.wait_closed()
        except Exception as e:
            print(f"[WARN] Close error: {e}")

    async def _send_outgoing(self, writer):
        try:
            while not self._stop:
                msg = await self.outbound_queue.get()

                if msg.get("type") == "stop":
                    print('Stopping TX/RX')
                    break
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
        Signals the thread and all async tasks to stop gracefully.
        """
        print('Stopping network')
        self._stop = True

        # Schedule cancellation of running tasks safely from outside the loop
        if self._loop and not self._loop.is_closed():
            def shutdown():
                if self._send_task:
                    self._send_task.cancel()
                if self._writer:
                    try:
                        self._writer.write_eof()
                    except Exception:
                        pass
                    try:
                        self._writer.close()
                    except Exception:
                        pass
                # Put poison pill in outbound queue to unblock send loop
                print('Net poison pill')
                asyncio.create_task(self.outbound_queue.put({"type": "stop"}))

            self._loop.call_soon_threadsafe(shutdown)
            
    def update_settings(self, settings: Settings):
        print("NET update_settings called with:", settings)
        new_ip = settings["server_ip"] if "server_ip" in settings else "127.0.0.1"
        new_port = settings["server_port"] if "server_port" in settings else DEFAULT_SERVER_PORT

        # If new settings differ, set flags
        if self.server_ip != new_ip or self.server_port != new_port:
            self.server_ip = new_ip
            self.server_port = new_port
            self.status.emit("[INFO] Server settings changed, reconnecting...")
            self._need_reconnect = True

    def reconnect(self):
        """
        Restart the connection with the updated server IP and port.
        Stops the current connection loop and triggers a reconnect.
        """
        self.status.emit("[INFO] Reconnecting to new server settings...")

        # Stop the current network operation
        self._stop = True

        # If event loop exists, stop it safely
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

        # Wait briefly to ensure clean shutdown
        import time
        time.sleep(0.1)

        # Reset stop flag and start the run loop again on a new thread
        self._stop = False

        # Since this is QThread, to restart it you typically need to create a new thread instance
        # or emit a signal to your main thread to restart the thread cleanly.
        # Here’s a common pattern:

        if self.isRunning():
            self.quit()  # Request thread to quit
            self.wait()  # Wait for it to finish

        # Restart thread (starts run())
        self.start()

