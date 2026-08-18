#!/usr/bin/env python3

import asyncio
import json
import os
import pty
import pwd
import signal
import struct
import termios
import fcntl

import websockets

HOST = "127.0.0.1"
PORT = 8090
TERMINAL_USER = os.environ.get("PYWEBCNC_USER", "dietpi")


def create_shell():
    user = pwd.getpwnam(TERMINAL_USER)
    pid, fd = pty.fork()

    if pid == 0:
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
        os.environ["HOME"] = user.pw_dir
        os.environ["USER"] = TERMINAL_USER
        os.environ["LOGNAME"] = TERMINAL_USER
        os.environ["SHELL"] = "/bin/bash"
        os.environ["TERM"] = "xterm-256color"
        os.chdir(user.pw_dir)
        os.execv("/bin/bash", ["/bin/bash", "--login"])

    return pid, fd


def set_terminal_size(fd, rows, cols):
    size = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


async def read_pty(fd, websocket):
    loop = asyncio.get_running_loop()

    while True:
        try:
            data = await loop.run_in_executor(None, os.read, fd, 4096)
            if not data:
                break
            await websocket.send(data.decode("utf-8", errors="replace"))
        except (OSError, websockets.exceptions.ConnectionClosed):
            break


async def terminal_handler(websocket):
    pid, fd = create_shell()
    reader = asyncio.create_task(read_pty(fd, websocket))

    try:
        async for message in websocket:
            try:
                data = json.loads(message)

                if data.get("type") == "input":
                    text = data.get("data", "")
                    if text:
                        os.write(fd, text.encode())

                elif data.get("type") == "resize":
                    rows = int(data.get("rows", 24))
                    cols = int(data.get("cols", 80))
                    set_terminal_size(fd, rows, cols)

            except Exception as exc:
                print(f"terminal message error: {exc}", flush=True)

    except websockets.exceptions.ConnectionClosed:
        pass

    finally:
        reader.cancel()
        try:
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


async def main():
    print(f"pywebcnc terminal listening on {HOST}:{PORT}", flush=True)
    print(f"shell user: {TERMINAL_USER}", flush=True)

    async with websockets.serve(terminal_handler, HOST, PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
