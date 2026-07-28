# Minutregnskab

Et webbaseret minutregnskab udviklet til ambulancepersonale.

## Funktioner

- Registrering af ture
- Automatisk beregning af A- og B-tid
- Beregning af overtid
- Fremskudt pause
- Gem som billede
- Mobilvenligt design
- Gemmer data lokalt i browseren

## Teknologi

- Python
- Flask
- Gunicorn
- Docker
- HTML / CSS / JavaScript

## Kør lokalt

```bash
python app.py
```

## Kør med Docker Compose

```bash
docker compose up -d --build
```

Appen er derefter tilgængelig på:

```text
http://localhost:8000
```

## Automatisk Docker-image

Når kode pushes til `main`, bygger GitHub Actions automatisk et image til både almindelige computere og Raspberry Pi 5:

```text
ghcr.io/flr45/minutregnskab:latest
```

Workflowet tester builds i pull requests og publicerer kun images fra `main` eller versions-tags.

### Start det publicerede image

```bash
docker run -d \
  --name minutregnskab \
  --restart unless-stopped \
  -p 8000:8000 \
  ghcr.io/flr45/minutregnskab:latest
```

Racher-Homelab-repository'et indeholder den centrale Compose-konfiguration, som senere bruges på Raspberry Pi'en.

## Sikkerhed og drift

- Containeren kører som en bruger uden root-rettigheder.
- Docker-image har et indbygget healthcheck.
- Gunicorn kører med flere workers og threads.
- Midlertidige filer og lokale miljøfiler udelades fra Docker-imaget.

## Status

Projektet udvikles løbende og er en del af Racher HomeLab.
