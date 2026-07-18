# CaseClosed NFC Reader

Android NFC reader and transaction injector for the Conan fraud investigation
backend.

## Backend setup

Add the backend base URL to the ignored project `local.properties` file:

```properties
BACKEND_BASE_URL=http://Aymans-MacBook-Air.local:8000
```

Use the LAN IP instead if `.local` hostnames do not resolve. The physical phone
and backend computer must be on the same trusted Wi-Fi network, and port 8000
must be reachable. The full endpoint remains editable in the app.

Start the backend from the Conan repository:

```bash
cd backend
cp .env.example .env
docker compose up --build
```

Scan a test EMV card, enter a SAR amount, and submit. The app displays the
accepted backend transaction ID and initial status while fraud rules and AI
analysis continue asynchronously.

## Tests

```bash
./gradlew testDebugUnitTest
```

## Data warning

This PoC sends and stores complete card/EMV data over an unauthenticated local
HTTP API and may send it to the configured LLM. Use test cards on an isolated
trusted network only. Do not use production payment-card data.
