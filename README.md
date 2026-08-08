# Bot de Telegram — Parrilla diaria de partidos

Este bot envía todos los días, por Telegram, los partidos del día de las
ligas que elegiste, junto con un link directo a la guía de canales de TV
de Colombia de ese día.

- **Partidos**: se obtienen automáticamente de [API-FOOTBALL](https://www.api-football.com/) (plan gratuito). No requiere que configures nada partido a partido.
- **Canales de TV (automático, para algunos torneos)**: para Champions
  League, Europa League, Copa Libertadores y Eliminatorias CONMEBOL, el
  bot consulta automáticamente la tabla de "derechos de TV por país" que
  mantiene Wikipedia para esos torneos y saca la fila de Colombia — sin
  que tengas que configurar ni mantener nada. Wikipedia sí permite
  consultas automatizadas a su API (a diferencia de sitios como FotMob,
  que lo prohíbe expresamente en sus términos de uso).
  Para las ligas domésticas (Liga BetPlay, Premier League, LaLiga, etc.)
  Wikipedia no mantiene esa tabla por país, así que no existe hoy una
  fuente automática y gratuita confiable para ellas — el mensaje del bot
  simplemente indica esas ligas al final con un link a la guía de TV de
  Colombia del día, para revisarlas con un toque.
- **Dónde corre**: GitHub Actions (gratis), con un cron que lo ejecuta
  automáticamente cada día. No necesitas servidor propio.
- **Escudos de los equipos**: además del mensaje de texto (con emojis), el
  bot manda un álbum de fotos — una imagen "escudo vs escudo" por partido,
  generada al vuelo con los logos que ya trae API-FOOTBALL (Pillow arma la
  imagen; no depende de ningún sitio externo ni de scraping).

## 1. Crear el bot en Telegram

1. Habla con [@BotFather](https://t.me/BotFather) en Telegram.
2. Envía `/newbot`, ponle un nombre y un usuario (debe terminar en `bot`).
3. BotFather te da un **token** (algo como `123456:ABC-...`). Guárdalo.
4. Escríbele un mensaje cualquiera a tu bot recién creado (para "activarlo").
5. Obtén tu **chat_id**: abre en el navegador, reemplazando `<TOKEN>`:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Busca el campo `"chat":{"id":...}` en la respuesta — ese número es tu chat_id.

## 2. Obtener una API key de API-FOOTBALL

1. Crea una cuenta gratis en <https://dashboard.api-football.com/register>.
2. En el dashboard obtienes una **API key** (plan gratuito: 100 requests/día,
   más que suficiente para este bot, que usa ~40-50 al día con 22 ligas).

## 3. Subir este proyecto a GitHub

1. Crea un repositorio nuevo en GitHub (puede ser privado).
2. Sube todos estos archivos tal cual están (mantén la carpeta `.github/workflows`).

## 4. Configurar los "Secrets" del repositorio

En el repo: **Settings → Secrets and variables → Actions → New repository secret**.
Crea estos tres:

| Nombre               | Valor                          |
|----------------------|---------------------------------|
| `TELEGRAM_BOT_TOKEN`  | El token que te dio BotFather   |
| `TELEGRAM_CHAT_ID`    | Tu chat_id                      |
| `API_FOOTBALL_KEY`    | Tu API key de API-FOOTBALL      |

## 5. Probarlo manualmente

En GitHub: pestaña **Actions → Enviar parrilla de partidos → Run workflow**.
Esto lo ejecuta ya mismo (sin esperar al cron) para que verifiques que te
llega el mensaje a Telegram. Revisa los "logs" del run: el script imprime
qué liga resolvió con qué ID — así puedes confirmar que encontró la liga
correcta (ver punto 7).

## 6. Horario automático

Por defecto corre todos los días a las **8:00 a.m. hora de Colombia**
(línea `cron: "0 13 * * *"` en `.github/workflows/daily.yml`, en UTC).
Para cambiar la hora, edita ese cron (recuerda que Colombia es UTC-5 todo
el año, sin horario de verano).

## 7. Ajustar ligas y canales

Abre `config/leagues.json`. Cada liga tiene:

- `display_name`: cómo se muestra en el mensaje.
- `search_name` / `country_hint`: con qué texto se busca la liga en
  API-FOOTBALL. **La primera vez, revisa los logs del workflow** para
  confirmar que cada una resolvió la liga correcta (a veces el buscador
  encuentra una liga distinta con nombre parecido).
  - Si resolvió mal una liga, corrígela buscando el nombre exacto en
    <https://dashboard.api-football.com> (sección "Documentation → Leagues")
    y, opcionalmente, escribe directamente su `id` y `season` en el JSON
    para que no tenga que buscarla cada día (más rápido y más confiable).
Puedes agregar o quitar ligas del archivo libremente, siguiendo la misma
estructura. Cada liga tiene también un campo `wiki_tv_page`: si apunta al
título exacto de una página de Wikipedia con tabla de derechos de TV por
país, el bot la usa para completar el canal automáticamente; si es `null`,
esa liga simplemente aparece en el aviso final con el link a la guía de TV.

**Importante — esto es "mejor esfuerzo"**: no pude probar el parseo de
Wikipedia contra las páginas reales en vivo al construir esto (sin acceso a
internet en el momento de escribirlo), así que está hecho con el formato
típico de esas tablas. La primera vez que corras el bot, revisa los logs:
si para alguna liga con `wiki_tv_page` configurado no aparece el canal, es
que el formato de esa tabla específica es distinto — puedo ayudarte a
ajustar el parser si me compartes el log del error.

## Notas

- Si algún día una liga no tiene partidos, simplemente no aparece en el
  mensaje de ese día.
- Si el mensaje queda muy largo, Telegram lo recibe partido en varios
  mensajes automáticamente (el script ya lo maneja).
- Todo el código es tuyo para modificar: por ejemplo, podrías agregar más
  ligas, cambiar el formato del mensaje, o mandarlo por la mañana y por la
  tarde.
