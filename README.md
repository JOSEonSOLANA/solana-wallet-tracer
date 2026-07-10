# Solana Wallet Tracer

Analizador forense de wallets Solana drenadas. Detecta automáticamente la transacción de drenaje, encuentra el fallo de seguridad entre las transacciones anteriores y traza el flujo completo de los fondos hasta su destino final con un grafo interactivo.

## 🌐 Live Demo

[https://solana-wallet-tracer.onrender.com](https://solana-wallet-tracer.onrender.com)

## 🚀 Cómo usar

1. **Buscar wallet** — pega una dirección Solana drenada y pulsa Buscar
2. **Auto-detección** — el sistema detecta el drenaje (>80% de SOL perdido) entre las últimas transacciones
3. **Traza completa** — sigue el flujo de fondos 3 niveles de profundidad: wallets intermedias, swaps, destino final
4. **Visualización** — grafo interactivo con layout horizontal, narrativa cronológica y partículas animadas

## ✨ Características

- **Detección automática de drenaje** — identifica la transacción donde se perdió >80% del SOL
- **Detección de vulnerabilidad (FALLO)** — analiza las 5 transacciones anteriores al drenaje buscando programas sospechosos no estándar
- **Traza multi-nivel (BFS)** — escanea wallets en profundidad hasta 3 niveles para mapear todo el flujo de fondos
- **Token pairing** — detecta swaps SOL → Token y los agrupa como movimientos compuestos
- **Narrativa forense** — pasos cronológicos con badges: FALLO, DRENADO, SWAP, RECIBIDO, DESTINO, MÚLTIPLE
- **Grafo interactivo D3.js** — layout horizontal con control de zoom/arrastre, sidebar de nodos, resaltado de aristas
- **Persistencia de API key** — la key de Helius se guarda en localStorage del navegador y en el servidor
- **Rate limit handling** — detecta límite de API key y muestra advertencia para reemplazarla
- **Historial** — las búsquedas anteriores se guardan en SQLite

## 🛠️ Stack

| Componente | Tecnología |
|-----------|-----------|
| Backend | Python + Flask |
| Frontend | HTML + CSS + D3.js (v7) |
| RPC | Solana JSON-RPC + Helius |
| Base de datos | SQLite |
| Deploy | Render (free tier) |

## 📦 Instalación local

```bash
git clone https://github.com/JOSEonSOLANA/solana-wallet-tracer.git
cd solana-wallet-tracer
pip install -r requirements.txt
python server.py
```

Abrir `http://localhost:5000/`.

### Variables de entorno

| Variable | Descripción |
|---------|-----------|
| `PORT` | Puerto del servidor (default: 5000) |
| `FLASK_ENV` | `development` para modo debug |
| `HOST` | Host de escucha (default: `0.0.0.0`) |
| `HELIUS_API_KEY` | API key de Helius (opcional, hay una por defecto) |

## 🔌 API Endpoints

| Endpoint | Método | Descripción |
|----------|--------|-----------|
| `/api/transactions/<address>` | GET | Transacciones de una wallet con detección de drenaje |
| `/api/trace-from-tx` | POST | Traza el flujo de fondos desde una transacción (depth=3) |
| `/api/helius-key` | GET | Obtiene la API key de Helius guardada |
| `/api/helius-key` | POST | Guarda una nueva API key de Helius |
| `/api/history` | GET | Historial de búsquedas |
| `/api/history/<id>` | DELETE | Elimina una búsqueda del historial |
| `/api/known-wallets` | GET | Wallets conocidas (exchanges, mixers) |
| `/api/known-wallets` | POST | Añade una wallet conocida |
| `/api/known-wallets/<address>` | DELETE | Elimina una wallet conocida |
| `/api/debug-rpc` | GET | Diagnóstico de conectividad RPC |

## 🔍 ¿Cómo funciona?

1. **`/api/transactions`** obtiene las últimas 100 transacciones vía `getSignaturesForAddress`
2. Detecta el drenaje comparando `preBalances` vs `postBalances` (pérdida >80%)
3. Divide las transacciones: 5 anteriores (posible fallo) y todas las posteriores (flujo de fondos)
4. **`/api/trace-from-tx`** parsea la transacción de drenaje y hace BFS en profundidad:
   - Nivel 1: wallets involucradas en la tx de drenaje
   - Nivel 2: wallets que recibieron fondos de esas wallets
   - Nivel 3: wallets que recibieron fondos de las del nivel 2
5. Detecta la **vulnerabilidad** examinando programas no estándar en las 5 txs previas
6. Aplica **cutoff temporal**: filtra transferencias anteriores al FALLO
7. El frontend renderiza el grafo con D3.js force simulation y la narrativa forense

## 👛 Wallet de ejemplo

```
4xckmPwFBX39kGUeNkawPHj4KHrCaA9LPChAAZsSVXwM
Drenada: Jul 6 2026
FALLO: Jul 4 2026 — aprobación del programa GM1NtvvnSXUptTrMCqbogAdZJydZSNv98DoU5AZVLmGh
```

## 📄 Licencia

MIT
