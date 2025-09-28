# Python Fussball.de API

Eine leichtgewichtige, selbst gehostete Python-API zum Crawlen und Bereitstellen von Daten von fussball.de.

Entwickelt als Alternative zu https://api-fussball.de/, da dort die Locations bei Spielen und der Spielverlauf fehlen.

Dies ist primär für Vereinswebsites und selbst hosten gedacht:
* Man kann einen einzelnen Club angeben, der permanent im Cache gehalten und aktualisiert wird, um schnelle Antworten zu gewährleisten.
* Die API ist damit performant genug, um bei jedem Seitenaufruf der Vereinsseite aufgerufen zu werden.

## Lokale Entwicklung

Um die Anwendung lokal für die Entwicklung auszuführen, befolge diese Schritte:

1.  **Erstelle eine virtuelle Umgebung:**
    ```bash
    python -m venv venv
    ```

2.  **Aktiviere die virtuelle Umgebung:**
    - Unter Windows:
      ```bash
      .\venv\Scripts\activate
      ```
    - Unter macOS/Linux:
      ```bash
      source venv/bin/activate
      ```

3.  **Installiere die Abhängigkeiten:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Erstelle eine `.env`-Datei:**
    Kopiere die `.env.example` in eine neue Datei namens `.env` und passe die Werte bei Bedarf an.
    ```bash
    # Beispiel für Windows
    copy .env.example .env
    # Beispiel für macOS/Linux
    cp .env.example .env
    ```
    Wichtige Einstellungen in der `.env`-Datei:
    - **`API_KEY`**: Setze einen geheimen Wert, um deine API zu schützen.
    - **`LOG_LEVEL`**: Passe den Log-Level an. Verwende `DEBUG` für eine detaillierte Ausgabe.
    - **`CACHE_TTL_*`**: Konfiguriere die Gültigkeitsdauer für verschiedene Cache-Typen.
    - **`PREWARM_CLUB_ID`**: Setze optional eine Vereins-ID, um proaktives Caching für einen bestimmten Verein zu aktivieren.
    - **`CACHE_DIR`**: Verzeichnis für persistente Cache-Dateien (optional, z. B. bei Docker-Betrieb).

    Hinweis: Wird `CACHE_DIR` gesetzt, erwartet die Anwendung die Cache-Datei unter
    `<CACHE_DIR>/fussball_cache.json`.

5.  **Starte die Anwendung:**
    Führe den folgenden Befehl im Stammverzeichnis des Projekts aus:
    ```bash
    uvicorn fussball_api.main:app --reload
    ```
    Das `--reload`-Flag aktiviert das automatische Neuladen, sodass der Server bei Code-Änderungen automatisch neu startet. Die API ist unter `http://127.0.0.1:8000` verfügbar.

## Tests ausführen

Um die Test-Suite auszuführen, führe den folgenden Befehl im Stammverzeichnis des Projekts aus:

```bash
pytest
```

## API benutzen

Die API ist standardmäßig unter http://127.0.0.1:8000 erreichbar.

- Authentifizierung: Über den Header X-API-Key mit dem in der .env gesetzten API_KEY.
- Beispiel:
  ```bash
  curl -H "X-API-Key: <dein_api_key>" \
       "http://127.0.0.1:8000/api/search/clubs?query=Bremen"
  ```

Wichtige Endpunkte (Auswahl):
- GET /                         -> Health/Info
- GET /api/search/clubs         -> Clubs suchen (query)
- GET /api/club/{club_id}       -> Vollständige Club-Infos
- GET /api/club/{club_id}/teams -> Teams eines Clubs
- GET /api/club/{club_id}/info  -> Basis-Infos eines Clubs
- GET /api/club/{club_id}/next_games -> Nächste Spiele (Club)
- GET /api/club/{club_id}/prev_games -> Vorherige Spiele (Club)
- GET /api/team/{team_id}       -> Team-Infos
- GET /api/team/{team_id}/table -> Tabelle eines Teams
- GET /api/team/{team_id}/next_games -> Nächste Spiele (Team)
- GET /api/team/{team_id}/prev_games -> Vorherige Spiele (Team)
- GET /api/game/{game_id}       -> Spiel-Details

### Interaktive API-Dokumentation (OpenAPI)

- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc

Hinweise zur Authentifizierung:
- Trage den API-Key im Header als `X-API-Key` ein.
- In Swagger UI:
  - Klicke bei einem Endpoint auf “Try it out” und füge einen Header `X-API-Key` mit deinem Key hinzu.

## Docker-Quickstart

- Build und Start:
  ```bash
  cp docker-compose.example.yml docker-compose.yml
  # Pass die docker-compose.yml an deine Umgebung an.
  docker compose up --build
  ```

- Zugriff:
  - API unter http://127.0.0.1:8000
  - Authentifizierung via Header X-API-Key
  - Interaktive Doku (Swagger): http://127.0.0.1:8000/docs

- Logs ansehen:
  ```bash
  docker compose logs -f
  ```

## Persistenter Cache (Docker)

Standardmäßig lebt der interne Cache nur im Speicher.  
Mit dem Hintergrund-Prewarming kann es aber länger dauern, bis die Daten nach einem Container-Neustart wieder vollständig geladen sind.  

Um den Cache auch zwischen Container-Neustarts zu erhalten, kannst du ihn in eine Datei schreiben lassen und diese Datei in ein persistentes Volume mounten.

### Beispiel mit Docker Compose

```yaml
services:
  api:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    environment:
      # Setze das Verzeichnis, in dem Cache-Dateien persistiert werden
      CACHE_DIR: /data
    volumes:
      - ./fussball_api:/app/fussball_api
      - ./cache_data:/data  # persistenter Speicherort für die Cache-Datei
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8000/"]
      interval: 30s
      timeout: 5s
      retries: 3
```

Nach dem ersten Start befindet sich die Datei `fussball_cache.json` im Host-Verzeichnis `./cache_data`.
Die Anwendung lädt diese beim Start automatisch (sofern vorhanden) und speichert sie nach jedem
Prewarming-Zyklus erneut. Der Pfad wird über `CACHE_DIR` festgelegt.
