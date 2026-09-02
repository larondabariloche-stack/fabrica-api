#!/usr/bin/env python3
"""api_cultivo.py — API de cultivo + tareas/fechas de la Fábrica La Ronda.
Renacida 2/9/2026 (stdlib puro, sin dependencias).

Modo local (default):   python3 api_cultivo.py           → escucha 127.0.0.1:8082,
                        usa token.json local + fabrica.json local.
Modo nube (Render/Railway): env vars:
  GOOGLE_TOKEN_JSON     → JSON completo del token de Google (client_id, client_secret,
                          refresh_token, token, expiry, token_uri...). Si está, se usa
                          en vez de token.json local (se escribe en /tmp).
  FABRICA_DRIVE=1       → fabrica.json se lee/escribe en Google Drive (fuente de verdad
                          compartida), con respaldo local.
  API_KEY               → clave para autenticar llamadas (default "2019").
  API_REQUIRE_KEY=1     → exige header X-API-Key en POSTs (para la nube).

Endpoints:
  GET  /api/estado           → ok + puerto
  GET  /api/plantas          → lista de plantas (id, genetica, ubicacion, fase)
  GET  /api/fabrica          → contenido de fabrica.json
  POST /api/mover            → {"id","sala"} mueve planta en la planilla de cultivo
  POST /api/tarea            → marcar {"sec","titulo","estado"} o agregar {"sec","titulo","agregar":true,...}
  POST /api/fecha            → {"sec","titulo","fecha","detalle"}
"""
import json, os, sys, tempfile, urllib.request, urllib.error, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- soporte nube: token desde env var (antes de importar google_api) ---
if os.environ.get("GOOGLE_TOKEN_JSON"):
    try:
        tok = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
        tmp = os.path.join(tempfile.gettempdir(), "token.json")
        with open(tmp, "w") as f:
            json.dump(tok, f, indent=2)
        os.environ["GOOGLE_TOKEN_PATH"] = tmp
        print(f"[api_cultivo] token de Google cargado desde env → {tmp}", flush=True)
    except Exception as e:
        print(f"[api_cultivo] WARN: GOOGLE_TOKEN_JSON inválido ({e})", flush=True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from google_api import get_access

SHEET = "1X6wGVPj4WtlNNnglBwqzE5VWPEPMuMrRD4z6mxqA7nY"
WS = os.path.dirname(os.path.abspath(__file__))
FABRICA = os.path.join(WS, "fabrica.json")
SALAS = {
    "Sala de Flora": "Flora",
    "Sala Vege Catamarca": "Crecimiento",
    "Sala C - Automáticas": "Crecimiento",
}
PORT = int(os.environ.get("API_CULTIVO_PORT", "8082"))
SECCIONES_VALIDAS = {"cultivo", "aceites", "gomitas", "infraestructura", "dispensa"}

API_KEY = os.environ.get("API_KEY", "2019")
API_REQUIRE_KEY = os.environ.get("API_REQUIRE_KEY", "0") == "1"
FABRICA_DRIVE = os.environ.get("FABRICA_DRIVE", "0") == "1"
FABRICA_DRIVE_NAME = os.environ.get("FABRICA_DRIVE_NAME", "fabrica.json")
_fabrica_drive_id = None  # cache del id del archivo en Drive


# ---------- Google Drive helpers (para fabrica.json en la nube) ----------
def drive_buscar(nombre):
    q = urllib.parse.quote(f"name='{nombre}' and trashed=false")
    url = f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id,name)"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + get_access())
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
        files = data.get("files", [])
        return files[0]["id"] if files else None


def drive_descargar(fid):
    url = f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + get_access())
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()


def drive_subir(nombre, content, fid=None):
    access = get_access()
    if fid:
        # actualizar archivo existente (media upload)
        url = f"https://www.googleapis.com/upload/drive/v3/files/{fid}?uploadType=media"
        req = urllib.request.Request(url, data=content.encode(), method="PATCH")
        req.add_header("Authorization", "Bearer " + access)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    # crear archivo nuevo (multipart)
    boundary = "julia_fabrica_%d" % int(__import__("time").time() * 1000)
    meta = json.dumps({"name": nombre}).encode()
    parts = [
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta + b"\r\n",
        f"--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode() + content.encode() + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name"
    req = urllib.request.Request(url, data=b"".join(parts), method="POST")
    req.add_header("Authorization", "Bearer " + access)
    req.add_header("Content-Type", f"multipart/related; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


# ---------- fabrica.json (tareas + fechas) ----------
def leer_fabrica():
    """Lee fabrica.json: desde Drive si FABRICA_DRIVE, si no desde local."""
    global _fabrica_drive_id
    if FABRICA_DRIVE:
        try:
            fid = _fabrica_drive_id or drive_buscar(FABRICA_DRIVE_NAME)
            if fid:
                _fabrica_drive_id = fid
                content = drive_descargar(fid)
                data = json.loads(content)
                # respaldo local
                with open(FABRICA, "w") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return data
        except Exception as e:
            print(f"[api_cultivo] WARN: no pude leer fabrica.json de Drive ({e}); uso local", flush=True)
    if os.path.exists(FABRICA):
        with open(FABRICA) as f:
            return json.load(f)
    return {}


def guardar_fabrica(datos):
    """Guarda fabrica.json: local siempre; además a Drive si FABRICA_DRIVE."""
    global _fabrica_drive_id
    with open(FABRICA, "w") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    if FABRICA_DRIVE:
        try:
            fid = _fabrica_drive_id or drive_buscar(FABRICA_DRIVE_NAME)
            if fid:
                _fabrica_drive_id = fid
                drive_subir(FABRICA_DRIVE_NAME, json.dumps(datos, ensure_ascii=False, indent=2), fid=fid)
            else:
                res = drive_subir(FABRICA_DRIVE_NAME, json.dumps(datos, ensure_ascii=False, indent=2))
                _fabrica_drive_id = res.get("id")
        except Exception as e:
            print(f"[api_cultivo] WARN: no pude subir fabrica.json a Drive ({e}); queda solo local", flush=True)


def marcar_tarea(sec, titulo, estado):
    if sec not in SECCIONES_VALIDAS:
        raise ValueError(f"Sección inválida: {sec}")
    datos = leer_fabrica()
    tareas = datos.setdefault(sec, {}).setdefault("tareas", [])
    for t in tareas:
        if t.get("titulo") == titulo:
            t["estado"] = estado
            guardar_fabrica(datos)
            return {"sec": sec, "titulo": titulo, "estado": estado}
    raise ValueError(f"Tarea no encontrada: {titulo}")


def agregar_tarea(sec, titulo, detalle="", categoria="hacer", fecha="", estado="pendiente"):
    if sec not in SECCIONES_VALIDAS:
        raise ValueError(f"Sección inválida: {sec}")
    if not titulo.strip():
        raise ValueError("Falta título de tarea")
    datos = leer_fabrica()
    tareas = datos.setdefault(sec, {}).setdefault("tareas", [])
    tareas.append({
        "titulo": titulo.strip(),
        "detalle": detalle.strip(),
        "categoria": categoria if categoria in ("comprar", "arreglar", "hacer", "otro") else "hacer",
        "fecha": fecha.strip(),
        "estado": estado if estado in ("pendiente", "hecha") else "pendiente",
    })
    guardar_fabrica(datos)
    return {"sec": sec, "titulo": titulo.strip(), "agregada": True}


def agregar_fecha(sec, titulo, fecha, detalle=""):
    if sec not in SECCIONES_VALIDAS:
        raise ValueError(f"Sección inválida: {sec}")
    if not titulo.strip() or not fecha.strip():
        raise ValueError("Faltan título o fecha")
    datos = leer_fabrica()
    fechas = datos.setdefault(sec, {}).setdefault("fechas", [])
    fechas.append({"fecha": fecha.strip(), "titulo": titulo.strip(), "detalle": detalle.strip()})
    guardar_fabrica(datos)
    return {"sec": sec, "titulo": titulo.strip(), "fecha": fecha.strip(), "agregada": True}


# ---------- planilla de cultivo (mover plantas) ----------
def sheets_get(range_):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET}/values/"
           f"{urllib.parse.quote(range_)}?majorDimension=ROWS")
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + get_access())
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def sheets_put(range_, values):
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET}/values/"
           f"{urllib.parse.quote(range_)}?valueInputOption=USER_ENTERED")
    body = json.dumps({"majorDimension": "ROWS", "values": values}).encode()
    req = urllib.request.Request(url, data=body, method="PUT")
    req.add_header("Authorization", "Bearer " + get_access())
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def leer_plantas():
    r = sheets_get("🌱 Inventario de Plantas!A2:G170")
    plantas = []
    for row in r.get("values", []):
        if not row or not str(row[0]).strip():
            continue
        def cell(i):
            return str(row[i]).strip() if i < len(row) else ""
        plantas.append({"id": cell(0), "genetica": cell(1), "tipo": cell(2),
                        "fecha": cell(3), "ubicacion": cell(4), "fase": cell(5),
                        "notas": cell(6)})
    return plantas


def mover_planta(pid, sala):
    if sala not in SALAS:
        raise ValueError(f"Sala inválida: {sala}. Válidas: {list(SALAS)}")
    plantas = leer_plantas()
    fila = None
    for p in plantas:
        if p["id"] == pid:
            fila = p
            break
    if fila is None:
        raise ValueError(f"No existe planta con ID: {pid}")
    rownum = 2 + plantas.index(fila)
    nueva_fase = SALAS[sala]
    rango = f"🌱 Inventario de Plantas!E{rownum}:F{rownum}"
    res = sheets_put(rango, [[sala, nueva_fase]])
    return {"id": pid, "genetica": fila["genetica"],
            "desde": fila["ubicacion"], "hacia": sala,
            "fase": nueva_fase, "fila": rownum,
            "cells": res.get("updatedCells", 0)}


def regenerar_portada():
    """Solo local: regenera la portada tras cambios. En la nube no aplica
    (el HTML lo regenera la compu y pushea a GitHub Pages)."""
    if FABRICA_DRIVE:
        return
    script = os.path.join(WS, "generar_portada_fabrica.py")
    if os.path.exists(script):
        os.system(f"cd {WS} && python3 {script} > /dev/null 2>&1")


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode() or "{}")
        except Exception:
            return {}

    def _auth_ok(self):
        if not API_REQUIRE_KEY:
            return True
        return self.headers.get("X-API-Key") == API_KEY

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/estado":
            self._json(200, {"ok": True, "servicio": "api_cultivo", "puerto": PORT,
                             "nube": FABRICA_DRIVE})
            return
        if not self._auth_ok():
            self._json(401, {"ok": False, "error": "API key requerida (header X-API-Key)"})
            return
        if self.path == "/api/plantas":
            try:
                self._json(200, {"ok": True, "plantas": leer_plantas()})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
        elif self.path == "/api/fabrica":
            try:
                self._json(200, {"ok": True, "fabrica": leer_fabrica()})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
        else:
            self._json(404, {"ok": False, "error": "no existe: " + self.path})

    def do_POST(self):
        if not self._auth_ok():
            self._json(401, {"ok": False, "error": "API key requerida (header X-API-Key)"})
            return
        if self.path == "/api/mover":
            body = self._read_body()
            pid = str(body.get("id", "")).strip()
            sala = str(body.get("sala", "")).strip()
            if not pid or not sala:
                self._json(400, {"ok": False, "error": "Faltan id o sala"})
                return
            try:
                res = mover_planta(pid, sala)
                self._json(200, {"ok": True, "movido": res})
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
        elif self.path == "/api/tarea":
            body = self._read_body()
            sec = str(body.get("sec", "")).strip()
            titulo = str(body.get("titulo", "")).strip()
            if body.get("agregar"):
                try:
                    res = agregar_tarea(sec, titulo,
                                        detalle=str(body.get("detalle", "")),
                                        categoria=str(body.get("categoria", "hacer")),
                                        fecha=str(body.get("fecha", "")),
                                        estado=str(body.get("estado", "pendiente")))
                    regenerar_portada()
                    self._json(200, {"ok": True, **res})
                except ValueError as e:
                    self._json(400, {"ok": False, "error": str(e)})
                except Exception as e:
                    self._json(500, {"ok": False, "error": str(e)})
                return
            estado = str(body.get("estado", "")).strip()
            if not sec or not titulo or estado not in ("pendiente", "hecha"):
                self._json(400, {"ok": False, "error": "Faltan sec/titulo/estado válido"})
                return
            try:
                res = marcar_tarea(sec, titulo, estado)
                regenerar_portada()
                self._json(200, {"ok": True, "tarea": res})
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
        elif self.path == "/api/fecha":
            body = self._read_body()
            sec = str(body.get("sec", "")).strip()
            titulo = str(body.get("titulo", "")).strip()
            fecha = str(body.get("fecha", "")).strip()
            if not sec or not titulo or not fecha:
                self._json(400, {"ok": False, "error": "Faltan sec/titulo/fecha"})
                return
            try:
                res = agregar_fecha(sec, titulo, fecha, detalle=str(body.get("detalle", "")))
                regenerar_portada()
                self._json(200, {"ok": True, **res})
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
        else:
            self._json(404, {"ok": False, "error": "no existe: " + self.path})

    def log_message(self, fmt, *args):
        sys.stderr.write("[api_cultivo] %s\n" % (fmt % args))


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0" if FABRICA_DRIVE else "127.0.0.1", PORT), Handler)
    print(f"api_cultivo escuchando en :{PORT} (nube={FABRICA_DRIVE}, requiere_key={API_REQUIRE_KEY})", flush=True)
    srv.serve_forever()