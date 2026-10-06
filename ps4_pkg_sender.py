#!/usr/bin/env python3
"""
PS4 PKG Sender
Envía archivos .pkg a una PS4 por FTP a través de la red local.

Requisitos: solo Python 3.8+ (tkinter y ftplib vienen incluidos).
"""

import ftplib
import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "PS4 PKG Sender"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".ps4_pkg_sender.json")
BLOCK_SIZE = 1024 * 1024  # 1 MB por bloque
DEFAULTS = {"ip": "", "port": "2121", "remote_dir": "/data/pkg"}


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def human_size(num):
    num = float(num)
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while num >= 1024 and i < len(units) - 1:
        num /= 1024
        i += 1
    return f"{num:.0f} {units[i]}" if i == 0 else f"{num:.2f} {units[i]}"


def human_time(seconds):
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError:
        pass


class Cancelled(Exception):
    """El usuario canceló el envío."""


# --------------------------------------------------------------------------
# Lógica FTP
# --------------------------------------------------------------------------
def ftp_connect(host, port):
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=30)
    # Los servidores FTP de la PS4 aceptan cualquier usuario/contraseña
    ftp.login("anonymous", "anonymous")
    ftp.set_pasv(True)
    return ftp


def ensure_remote_dir(ftp, path):
    """Entra a la carpeta remota, creándola si no existe."""
    ftp.cwd("/")
    for part in [p for p in path.split("/") if p]:
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


# --------------------------------------------------------------------------
# Interfaz gráfica
# --------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.minsize(680, 640)

        self.cfg = load_config()
        self.files = []
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False

        style = ttk.Style(self)
        for theme in ("vista", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.poll_events)

    # ---------------- construcción de la ventana ----------------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        # 1. Conexión
        conn = ttk.LabelFrame(root, text=" 1. Conexión con la PS4 ", padding=10)
        conn.pack(fill="x", **{"pady": 4})
        conn.columnconfigure(1, weight=1)

        self.var_ip = tk.StringVar(value=self.cfg["ip"])
        self.var_port = tk.StringVar(value=self.cfg["port"])
        self.var_dir = tk.StringVar(value=self.cfg["remote_dir"])

        ttk.Label(conn, text="IP de la PS4:").grid(row=0, column=0, sticky="w", pady=3)
        self.ent_ip = ttk.Entry(conn, textvariable=self.var_ip)
        self.ent_ip.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(conn, text="Puerto:").grid(row=0, column=2, sticky="w")
        self.ent_port = ttk.Entry(conn, textvariable=self.var_port, width=8)
        self.ent_port.grid(row=0, column=3, padx=(8, 0))

        ttk.Label(conn, text="Carpeta destino:").grid(row=1, column=0, sticky="w", pady=3)
        self.ent_dir = ttk.Entry(conn, textvariable=self.var_dir)
        self.ent_dir.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0))

        self.btn_test = ttk.Button(conn, text="Probar conexión", command=self.on_test)
        self.btn_test.grid(row=2, column=0, columnspan=4, sticky="e", pady=(8, 0))

        # 2. Archivos
        files = ttk.LabelFrame(root, text=" 2. Archivos .pkg a enviar ", padding=10)
        files.pack(fill="both", expand=True, pady=4)
        files.columnconfigure(0, weight=1)
        files.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(files, height=7, selectmode="extended", activestyle="none")
        self.listbox.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(files, orient="vertical", command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.config(yscrollcommand=scroll.set)

        btns = ttk.Frame(files)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.btn_add = ttk.Button(btns, text="Agregar archivos…", command=self.on_add)
        self.btn_add.pack(side="left")
        self.btn_remove = ttk.Button(btns, text="Quitar seleccionados", command=self.on_remove)
        self.btn_remove.pack(side="left", padx=6)
        self.btn_clear = ttk.Button(btns, text="Vaciar lista", command=self.on_clear)
        self.btn_clear.pack(side="left")
        self.lbl_total = ttk.Label(btns, text="0 archivos")
        self.lbl_total.pack(side="right")

        # 3. Envío
        send = ttk.LabelFrame(root, text=" 3. Enviar ", padding=10)
        send.pack(fill="x", pady=4)
        send.columnconfigure(0, weight=1)

        self.lbl_file = ttk.Label(send, text="Esperando…")
        self.lbl_file.grid(row=0, column=0, sticky="w")
        self.bar_file = ttk.Progressbar(send, maximum=100)
        self.bar_file.grid(row=1, column=0, sticky="ew", pady=(2, 8))

        self.lbl_all = ttk.Label(send, text="")
        self.lbl_all.grid(row=2, column=0, sticky="w")
        self.bar_all = ttk.Progressbar(send, maximum=100)
        self.bar_all.grid(row=3, column=0, sticky="ew", pady=(2, 8))

        actions = ttk.Frame(send)
        actions.grid(row=4, column=0, sticky="e")
        self.btn_cancel = ttk.Button(actions, text="Cancelar", command=self.on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        self.btn_send = ttk.Button(actions, text="Enviar a la PS4", command=self.on_send)
        self.btn_send.pack(side="left")

        # Registro
        logf = ttk.LabelFrame(root, text=" Registro ", padding=6)
        logf.pack(fill="both", expand=True, pady=4)
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)
        self.txt_log = tk.Text(logf, height=6, state="disabled", wrap="word")
        self.txt_log.grid(row=0, column=0, sticky="nsew")
        lscroll = ttk.Scrollbar(logf, orient="vertical", command=self.txt_log.yview)
        lscroll.grid(row=0, column=1, sticky="ns")
        self.txt_log.config(yscrollcommand=lscroll.set)

        self.lockable = [self.ent_ip, self.ent_port, self.ent_dir, self.btn_test,
                         self.btn_add, self.btn_remove, self.btn_clear, self.btn_send]

    # ---------------- helpers de UI ----------------
    def log(self, text):
        stamp = time.strftime("%H:%M:%S")
        self.txt_log.config(state="normal")
        self.txt_log.insert("end", f"[{stamp}] {text}\n")
        self.txt_log.see("end")
        self.txt_log.config(state="disabled")

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for w in self.lockable:
            w.config(state=state)
        self.btn_cancel.config(state="normal" if busy else "disabled")

    def refresh_list(self):
        self.listbox.delete(0, "end")
        total = 0
        for path in self.files:
            size = os.path.getsize(path)
            total += size
            self.listbox.insert("end", f"{os.path.basename(path)}   ({human_size(size)})")
        n = len(self.files)
        self.lbl_total.config(text=f"{n} archivo{'s' if n != 1 else ''} · {human_size(total)}")

    def get_params(self):
        host = self.var_ip.get().strip()
        if not host:
            messagebox.showwarning(APP_NAME, "Escribe la IP de tu PS4 (la ves en Ajustes > Red > Ver estado de la conexión).")
            return None
        try:
            port = int(self.var_port.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_NAME, "El puerto no es válido (en GoldHEN suele ser 2121).")
            return None
        remote_dir = "/" + self.var_dir.get().strip().strip("/")
        self.cfg.update(ip=host, port=str(port), remote_dir=remote_dir)
        save_config(self.cfg)
        return host, port, remote_dir

    # ---------------- acciones de botones ----------------
    def on_add(self):
        paths = filedialog.askopenfilenames(
            title="Elige los archivos .pkg",
            filetypes=[("Paquetes PS4", "*.pkg"), ("Todos los archivos", "*.*")],
        )
        for p in paths:
            if p not in self.files:
                self.files.append(p)
        self.refresh_list()

    def on_remove(self):
        for i in reversed(self.listbox.curselection()):
            del self.files[i]
        self.refresh_list()

    def on_clear(self):
        self.files.clear()
        self.refresh_list()

    def on_cancel(self):
        self.cancel_event.set()
        self.btn_cancel.config(state="disabled")
        self.log("Cancelando…")

    def on_test(self):
        params = self.get_params()
        if not params:
            return
        self.set_busy(True)
        self.btn_cancel.config(state="disabled")
        self.log("Probando conexión…")
        threading.Thread(target=self._test_worker, args=params, daemon=True).start()

    def on_send(self):
        if not self.files:
            messagebox.showinfo(APP_NAME, "Primero agrega al menos un archivo .pkg.")
            return
        params = self.get_params()
        if not params:
            return
        missing = [f for f in self.files if not os.path.isfile(f)]
        if missing:
            messagebox.showerror(APP_NAME, f"No se encuentra el archivo:\n{missing[0]}")
            return
        self.cancel_event.clear()
        self.bar_file["value"] = 0
        self.bar_all["value"] = 0
        self.lbl_file.config(text="Conectando…")
        self.lbl_all.config(text="")
        self.set_busy(True)
        threading.Thread(target=self._send_worker, args=(*params, list(self.files)), daemon=True).start()

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno(APP_NAME, "Hay un envío en curso. ¿Cancelar y salir?"):
                return
            self.cancel_event.set()
        self.cfg.update(ip=self.var_ip.get().strip(), port=self.var_port.get().strip(),
                        remote_dir=self.var_dir.get().strip())
        save_config(self.cfg)
        self.destroy()

    # ---------------- hilos de trabajo ----------------
    def _test_worker(self, host, port, remote_dir):
        try:
            ftp = ftp_connect(host, port)
            try:
                ftp.cwd(remote_dir)
                msg = f"Conexión correcta. La carpeta {remote_dir} existe."
            except ftplib.error_perm:
                msg = f"Conexión correcta. La carpeta {remote_dir} no existe todavía; se creará al enviar."
            try:
                ftp.quit()
            except Exception:
                ftp.close()
            self.events.put(("test", True, msg))
        except Exception as exc:
            self.events.put(("test", False, f"No se pudo conectar: {exc}\n\n"
                                            "Revisa que la PS4 esté encendida, que el servidor FTP esté activo "
                                            "y que la IP y el puerto sean correctos."))

    def _send_worker(self, host, port, remote_dir, files):
        total = sum(os.path.getsize(f) for f in files)
        done_before = 0
        start = time.time()
        ftp = None
        try:
            self.events.put(("log", f"Conectando a {host}:{port}…"))
            ftp = ftp_connect(host, port)
            ensure_remote_dir(ftp, remote_dir)
            self.events.put(("log", f"Conectado. Carpeta destino: {remote_dir}"))

            for idx, path in enumerate(files, 1):
                name = os.path.basename(path)
                size = os.path.getsize(path)
                self.events.put(("log", f"Enviando {idx}/{len(files)}: {name} ({human_size(size)})"))
                state = {"sent": 0, "last": 0.0}

                def on_block(block, idx=idx, name=name, size=size, base=done_before, state=state):
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    state["sent"] += len(block)
                    now = time.time()
                    if now - state["last"] >= 0.25 or state["sent"] >= size:
                        state["last"] = now
                        self.events.put(("progress", {
                            "idx": idx, "count": len(files), "name": name,
                            "file_sent": state["sent"], "file_size": size,
                            "total_sent": base + state["sent"], "total": total,
                            "elapsed": now - start,
                        }))

                with open(path, "rb") as fh:
                    ftp.storbinary(f"STOR {name}", fh, BLOCK_SIZE, on_block)
                done_before += size
                self.events.put(("log", f"✔ {name} enviado correctamente"))

            try:
                ftp.quit()
            except Exception:
                pass
            self.events.put(("finished", True, "¡Envío completado!"))
        except Cancelled:
            self.events.put(("finished", False, "Envío cancelado. Puede haber quedado un archivo incompleto en la PS4; bórralo antes de reintentar."))
        except Exception as exc:
            self.events.put(("finished", False, f"Error durante el envío: {exc}"))
        finally:
            if ftp is not None:
                try:
                    ftp.close()
                except Exception:
                    pass

    # ---------------- eventos desde los hilos ----------------
    def poll_events(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self.log(ev[1])
                elif kind == "progress":
                    self._on_progress(ev[1])
                elif kind == "test":
                    self.set_busy(False)
                    self.log(ev[2].splitlines()[0])
                    (messagebox.showinfo if ev[1] else messagebox.showerror)(APP_NAME, ev[2])
                elif kind == "finished":
                    self.set_busy(False)
                    self.log(ev[2])
                    if ev[1]:
                        self.bar_file["value"] = 100
                        self.bar_all["value"] = 100
                        self.lbl_file.config(text="Listo")
                        messagebox.showinfo(APP_NAME, ev[2] + "\n\nYa puedes instalar el paquete desde tu PS4.")
                    else:
                        self.lbl_file.config(text="Detenido")
                        messagebox.showwarning(APP_NAME, ev[2])
        except queue.Empty:
            pass
        self.after(100, self.poll_events)

    def _on_progress(self, p):
        file_pct = 100 if p["file_size"] == 0 else p["file_sent"] * 100 / p["file_size"]
        total_pct = 100 if p["total"] == 0 else p["total_sent"] * 100 / p["total"]
        speed = p["total_sent"] / p["elapsed"] if p["elapsed"] > 0 else 0
        eta = (p["total"] - p["total_sent"]) / speed if speed > 0 else 0
        self.bar_file["value"] = file_pct
        self.bar_all["value"] = total_pct
        self.lbl_file.config(
            text=f"Archivo {p['idx']}/{p['count']}: {p['name']} — "
                 f"{human_size(p['file_sent'])} / {human_size(p['file_size'])} ({file_pct:.0f}%)")
        self.lbl_all.config(
            text=f"Total: {human_size(p['total_sent'])} / {human_size(p['total'])}  ·  "
                 f"{human_size(speed)}/s  ·  Faltan {human_time(eta)}")


def main():
    # Pantallas de alta resolución en Windows: evita que se vea borroso
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
