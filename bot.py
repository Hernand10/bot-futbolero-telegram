"""
Bot de Telegram: envía diariamente la parrilla de partidos de las ligas
configuradas en config/leagues.json, con emojis y, además del mensaje de
texto, un álbum de fotos con el escudo de cada equipo (home vs away) por
partido, generado con los logos que ya trae API-FOOTBALL.

Fuente de partidos: API-FOOTBALL (https://www.api-football.com/)

Fuente de canales de TV (automática, para algunos torneos):
    Wikipedia mantiene, para ciertos torneos, una tabla de "qué canal lo
    transmite en cada país" (ej. Champions League, Europa League, Copa
    Libertadores, Eliminatorias CONMEBOL). El bot consulta esa tabla vía
    la API de Wikipedia (que sí permite acceso automatizado, a diferencia
    de sitios como FotMob) y saca la fila de Colombia. Para los torneos
    que NO tienen esa tabla en Wikipedia (ligas domésticas como Premier
    League, LaLiga, Liga BetPlay, etc.) no hay una fuente automática y
    gratuita confiable, así que el mensaje incluye al final un link a la
    guía de TV de Colombia del día para revisarlos manualmente con un toque.
"""

import os
import re
import json
import datetime
import time
import requests
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

API_BASE = "https://v3.football.api-sports.io"
WIKI_API = "https://es.wikipedia.org/w/api.php"

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"].strip()
API_FOOTBALL_KEY = os.environ["API_FOOTBALL_KEY"].strip()

HEADERS = {"x-apisports-key": API_FOOTBALL_KEY}

# Diagnóstico temprano: si el secret llegó vacío, mejor un error claro que
# 22 avisos crípticos de "missing application key".
if not API_FOOTBALL_KEY:
    raise SystemExit(
        "El secret API_FOOTBALL_KEY llegó vacío. Revisa en GitHub: "
        "Settings > Secrets and variables > Actions > API_FOOTBALL_KEY."
    )

# Wikipedia pide un User-Agent identificable para uso automatizado de su API.
# Puedes cambiar el contacto por el tuyo (no es obligatorio pero es buena práctica).
WIKI_HEADERS = {
    "User-Agent": "BotPartidosColombiaTelegram/1.0 (uso personal, no comercial)"
}

# Colombia = UTC-5 todo el año (no tiene horario de verano)
UTC_OFFSET_HORAS = -5

_cache_canales_wiki = {}


def cargar_ligas(ruta="config/leagues.json"):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def _revisar_errores_api(cuerpo_json, contexto):
    """
    api-football suele responder HTTP 200 incluso cuando algo falla (key
    inválida, plan sin acceso, límite diario superado, parámetro mal
    formado), y mete el motivo en el campo 'errors' del JSON en vez de
    devolver un código de error HTTP. requests.raise_for_status() no
    detecta esto, así que lo revisamos a mano para no quedarnos ciegos.
    """
    errores = cuerpo_json.get("errors")
    if errores:
        print(f"  [ERROR API-FOOTBALL] en {contexto}: {errores}")
    return errores


def buscar_liga(search_name, country_hint=None):
    """Resuelve el league_id y la temporada actual buscando por nombre."""
    try:
        resp = requests.get(
            f"{API_BASE}/leagues",
            headers=HEADERS,
            params={"search": search_name},
            timeout=20,
        )
        resp.raise_for_status()
        cuerpo = resp.json()
        _revisar_errores_api(cuerpo, f"búsqueda de liga '{search_name}'")
        restante = resp.headers.get("x-ratelimit-requests-remaining")
        if restante is not None:
            print(f"  (requests restantes hoy en API-FOOTBALL: {restante})")
        data = cuerpo.get("response", [])
    except requests.RequestException as e:
        print(f"  [ERROR] buscando liga '{search_name}': {e}")
        return None, None

    if not data:
        print(f"  [AVISO] no se encontró ninguna liga para '{search_name}'")
        return None, None

    elegido = data[0]
    if country_hint:
        for item in data:
            pais = (item.get("country") or {}).get("name") or ""
            if country_hint.lower() in pais.lower():
                elegido = item
                break

    league_id = elegido["league"]["id"]
    nombre_real = elegido["league"]["name"]
    pais_real = (elegido.get("country") or {}).get("name")

    temporada_actual = None
    for s in elegido.get("seasons", []):
        if s.get("current"):
            temporada_actual = s["year"]
            break
    if temporada_actual is None and elegido.get("seasons"):
        temporada_actual = elegido["seasons"][-1]["year"]

    print(f"  -> resuelto: '{search_name}' = {nombre_real} ({pais_real}), id={league_id}, temporada={temporada_actual}")
    return league_id, temporada_actual


def fecha_hoy_colombia():
    """
    'Hoy' según la hora de Colombia (UTC-5), no la del servidor donde corre
    el workflow (GitHub Actions usa UTC). Sin esto, correr el bot de noche
    en Colombia cae ya en el 'día siguiente' en UTC y busca la fecha
    equivocada.
    """
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=UTC_OFFSET_HORAS)).date()


def partidos_de_hoy(league_id, season):
    hoy = fecha_hoy_colombia().isoformat()
    try:
        resp = requests.get(
            f"{API_BASE}/fixtures",
            headers=HEADERS,
            params={"league": league_id, "season": season, "date": hoy},
            timeout=20,
        )
        resp.raise_for_status()
        cuerpo = resp.json()
        _revisar_errores_api(cuerpo, f"partidos league_id={league_id} season={season} date={hoy}")
        fixtures = cuerpo.get("response", [])
        print(f"  -> league_id={league_id} season={season} date={hoy}: {len(fixtures)} partido(s)")
        return fixtures
    except requests.RequestException as e:
        print(f"  [ERROR] obteniendo partidos para league_id={league_id}: {e}")
        return []


def formatear_hora_local(timestamp_unix):
    dt_utc = datetime.datetime.utcfromtimestamp(timestamp_unix)
    dt_local = dt_utc + datetime.timedelta(hours=UTC_OFFSET_HORAS)
    return dt_local.strftime("%I:%M %p").lstrip("0")


def formatear_partido(fixture):
    home = fixture["teams"]["home"]["name"]
    away = fixture["teams"]["away"]["name"]
    hora = formatear_hora_local(fixture["fixture"]["timestamp"])
    return f"⏰ {hora} — {home} 🆚 {away}"


def _limpiar_wikitexto(texto):
    """Quita marcado wiki (referencias, enlaces, plantillas simples) de una celda."""
    texto = re.sub(r"<ref[^>]*>.*?</ref>", "", texto, flags=re.DOTALL)
    texto = re.sub(r"<ref[^>]*/>", "", texto)
    texto = re.sub(r"\{\{[^{}]*\}\}", "", texto)  # plantillas simples (banderas, etc.)
    texto = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", texto)  # [[a|b]] -> b
    texto = re.sub(r"'''?", "", texto)  # negritas/cursivas
    texto = re.sub(r"<[^>]+>", "", texto)  # tags html sueltos
    texto = texto.replace("|", " ").strip(" \n\t-")
    return re.sub(r"\s{2,}", " ", texto).strip()


def obtener_canal_wikipedia(pagina_wiki, pais="Colombia"):
    """
    Busca en una página de Wikipedia (tabla de derechos de TV por país) la
    fila correspondiente a `pais` y devuelve el texto del canal.
    Devuelve None si no se encuentra o si algo falla (mejor esfuerzo: no
    rompe el resto del bot si Wikipedia cambió el formato de la tabla).
    """
    if pagina_wiki in _cache_canales_wiki:
        return _cache_canales_wiki[pagina_wiki]

    resultado = None
    try:
        resp = requests.get(
            WIKI_API,
            headers=WIKI_HEADERS,
            params={
                "action": "parse",
                "page": pagina_wiki,
                "prop": "wikitext",
                "format": "json",
                "formatversion": 2,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        wikitext = data.get("parse", {}).get("wikitext", "")

        # Recorremos fila por fila (las tablas wiki separan filas con "|-")
        filas = wikitext.split("|-")
        for fila in filas:
            if pais.lower() not in fila.lower():
                continue
            # separar celdas: cada celda empieza con "||" o al inicio de línea con "|"
            celdas = re.split(r"\n\s*\|\|?|\|\|", fila)
            celdas = [c for c in celdas if c.strip()]
            for i, celda in enumerate(celdas):
                if pais.lower() in celda.lower() and i + 1 < len(celdas):
                    canal = _limpiar_wikitexto(celdas[i + 1])
                    if canal:
                        resultado = canal
                    break
            if resultado:
                break
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"  [AVISO] no se pudo consultar Wikipedia para '{pagina_wiki}': {e}")

    _cache_canales_wiki[pagina_wiki] = resultado
    return resultado


# Guía de TV de Colombia del día (fuente: FotMob). No se hace scraping de esta
# página automáticamente porque sus términos de uso lo prohíben expresamente;
# solo se genera el link para que la abras tú con un toque y veas el canal
# exacto de cada partido, ya filtrado por Colombia y por hoy. Se usa como
# respaldo para los torneos que no tienen tabla de canales en Wikipedia.
GUIA_TV_COLOMBIA_URL = "https://www.fotmob.com/tv-guide/co"

# --- Imágenes de escudos (home vs away) por partido ---

RUTAS_FUENTE = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # incluida en ubuntu-latest
]


def _cargar_fuente(tamano):
    for ruta in RUTAS_FUENTE:
        try:
            return ImageFont.truetype(ruta, tamano)
        except OSError:
            continue
    return ImageFont.load_default()


def _descargar_escudo(url, lado=180):
    """Descarga el logo de un equipo y lo deja cuadrado con fondo blanco."""
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGBA")
    except Exception as e:
        print(f"  [AVISO] no se pudo descargar escudo ({url}): {e}")
        return None

    img.thumbnail((lado, lado), Image.LANCZOS)
    lienzo = Image.new("RGBA", (lado, lado), (255, 255, 255, 0))
    x = (lado - img.width) // 2
    y = (lado - img.height) // 2
    lienzo.paste(img, (x, y), img)
    return lienzo


def crear_imagen_partido(fixture):
    """
    Arma una imagen con el escudo del equipo local, un 'VS' y el escudo
    del visitante. Devuelve un BytesIO listo para subir a Telegram, o
    None si no se pudieron descargar los escudos.
    """
    home = fixture["teams"]["home"]
    away = fixture["teams"]["away"]

    escudo_local = _descargar_escudo(home.get("logo", ""))
    escudo_visita = _descargar_escudo(away.get("logo", ""))
    if not escudo_local or not escudo_visita:
        return None

    tam_escudo = escudo_local.width
    ancho, alto = 640, 260
    lienzo = Image.new("RGB", (ancho, alto), (255, 255, 255))

    y_escudo = 30
    lienzo.paste(escudo_local, (60, y_escudo), escudo_local)
    lienzo.paste(escudo_visita, (ancho - tam_escudo - 60, y_escudo), escudo_visita)

    draw = ImageDraw.Draw(lienzo)
    fuente_vs = _cargar_fuente(46)
    texto_vs = "VS"
    bbox = draw.textbbox((0, 0), texto_vs, font=fuente_vs)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((ancho - w) / 2, y_escudo + tam_escudo / 2 - h / 2 - bbox[1]),
        texto_vs,
        fill=(60, 60, 60),
        font=fuente_vs,
    )

    fuente_nombres = _cargar_fuente(22)
    for nombre, cx in ((home["name"], 60 + tam_escudo / 2), (away["name"], ancho - tam_escudo / 2 - 60)):
        bbox = draw.textbbox((0, 0), nombre, font=fuente_nombres)
        w = bbox[2] - bbox[0]
        draw.text((cx - w / 2, y_escudo + tam_escudo + 12), nombre, fill=(20, 20, 20), font=fuente_nombres)

    buffer = BytesIO()
    lienzo.save(buffer, format="JPEG", quality=88)
    buffer.seek(0)
    return buffer


def enviar_album_telegram(fotos_con_pie):
    """
    fotos_con_pie: lista de tuplas (BytesIO, texto_del_pie).
    Envía en grupos de hasta 10 (límite de Telegram para sendMediaGroup).
    """
    if not fotos_con_pie:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMediaGroup"
    for inicio in range(0, len(fotos_con_pie), 10):
        lote = fotos_con_pie[inicio:inicio + 10]
        media = []
        files = {}
        for i, (foto, pie) in enumerate(lote):
            campo = f"foto{inicio}_{i}"
            files[campo] = (f"{campo}.jpg", foto, "image/jpeg")
            item = {"type": "photo", "media": f"attach://{campo}"}
            if pie:
                item["caption"] = pie
            media.append(item)

        try:
            resp = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "media": json.dumps(media)},
                files=files,
                timeout=60,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  [AVISO] no se pudo enviar un lote de escudos: {e}")
        time.sleep(1)  # ser amable con el rate limit de Telegram


def construir_mensaje(ligas_config):
    hoy_txt = fecha_hoy_colombia().strftime("%d/%m/%Y")
    lineas = [f"📅 Parrilla de partidos — {hoy_txt} ⚽🇨🇴"]
    hubo_partidos = False
    huerfanos_sin_canal = []
    fixtures_con_liga = []  # para las imágenes de escudos

    for liga in ligas_config:
        nombre_mostrar = liga["display_name"]
        league_id = liga.get("id")
        season = liga.get("season")

        if not league_id:
            league_id, season = buscar_liga(
                liga["search_name"], liga.get("country_hint")
            )
            time.sleep(0.3)  # ser amable con el rate limit

        if not league_id:
            continue

        fixtures = partidos_de_hoy(league_id, season)
        time.sleep(0.3)

        if not fixtures:
            continue

        hubo_partidos = True
        lineas.append(f"\n🏆 {nombre_mostrar}")
        for fx in fixtures:
            lineas.append(f"  {formatear_partido(fx)}")
            fixtures_con_liga.append((nombre_mostrar, fx))

        pagina_wiki = liga.get("wiki_tv_page")
        if pagina_wiki:
            canal = obtener_canal_wikipedia(pagina_wiki)
            time.sleep(0.3)
            if canal:
                lineas.append(f"  📺 {canal}")
            else:
                huerfanos_sin_canal.append(nombre_mostrar)
        else:
            huerfanos_sin_canal.append(nombre_mostrar)

    if not hubo_partidos:
        lineas.append("\n😴 Hoy no hay partidos programados en tus ligas seleccionadas.")
    elif huerfanos_sin_canal:
        lineas.append(
            f"\n📺 Canal no disponible automáticamente para: {', '.join(huerfanos_sin_canal)}."
            f"\n   👉 Revísalo aquí: {GUIA_TV_COLOMBIA_URL}"
        )

    return "\n".join(lineas), fixtures_con_liga


def enviar_telegram(texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram limita cada mensaje a 4096 caracteres
    for i in range(0, len(texto), 4000):
        trozo = texto[i:i + 4000]
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": trozo},
            timeout=20,
        )
        resp.raise_for_status()


def verificar_estado_api():
    """
    Consulta /status: dice el plan de la cuenta, cuántas requests llevas
    usadas hoy y si la suscripción está activa. Se imprime al principio
    del log para diagnosticar de un vistazo si el problema es la API key,
    el plan, o el límite diario — sin tener que leer los 22 intentos de
    liga uno por uno.
    """
    print("===== ESTADO DE LA CUENTA API-FOOTBALL =====")
    print(f"Longitud de API_FOOTBALL_KEY recibida: {len(API_FOOTBALL_KEY)} caracteres")
    try:
        resp = requests.get(f"{API_BASE}/status", headers=HEADERS, timeout=20)
        print(f"HTTP status: {resp.status_code}")
        print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
    except requests.RequestException as e:
        print(f"[ERROR] no se pudo consultar /status: {e}")
    print("==============================================")


def main():
    verificar_estado_api()

    ligas_config = cargar_ligas()
    mensaje, fixtures_con_liga = construir_mensaje(ligas_config)

    print("----- MENSAJE A ENVIAR -----")
    print(mensaje)
    print("-----------------------------")
    enviar_telegram(mensaje)
    print("Mensaje de texto enviado correctamente a Telegram.")

    fotos_con_pie = []
    for nombre_liga, fx in fixtures_con_liga:
        imagen = crear_imagen_partido(fx)
        if imagen:
            pie = f"🏆 {nombre_liga}\n{formatear_partido(fx)}"
            fotos_con_pie.append((imagen, pie))

    if fotos_con_pie:
        enviar_album_telegram(fotos_con_pie)
        print(f"{len(fotos_con_pie)} imagen(es) de escudos enviada(s) correctamente.")


if __name__ == "__main__":
    main()
