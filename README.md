# CorSuplementos — Backend

Django REST API for the CorSuplementos store. It owns the catalogue, stock,
orders, the Mercado Pago integration and the seller's admin.

Deployed on Railway at
`https://web-production-d0b6b4df.up.railway.app`. The store front end is a
separate Angular repository that consumes this API.

## Requirements

- Python 3.13+
- PostgreSQL 16 (the bundled `docker-compose.yml` provides one)
- A Cloudinary account for product images
- Mercado Pago credentials (test credentials are enough)

## Running locally

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt

docker compose up -d db
cp .env.example .env             # fill in the values
./venv/bin/python manage.py migrate
./venv/bin/python manage.py createsuperuser
./venv/bin/python manage.py runserver
```

- API: `http://localhost:8000/api/`
- Admin: `http://localhost:8000/admin/`

Every setting reads from the environment and falls back to the value above, so
a fresh clone works without touching `config/settings.py`.

## Applications

| App | Responsibility |
| --- | --- |
| `users` | Custom user, JWT endpoints, the VIP flag |
| `products` | Products, variants, stock, demo seeders |
| `shipping` | Pickup, local delivery and national shipping options |
| `orders` | Order lifecycle, stock primitives, admin actions |
| `payments` | Mercado Pago preference creation and webhook |
| `config` | Settings, URL routing, WSGI entry point |

## API

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/auth/register/` | Create a customer |
| `POST` | `/api/auth/login/` | Returns access and refresh tokens |
| `POST` | `/api/auth/refresh/` | Refresh an access token |
| `GET` | `/api/auth/me/` | Current user, including `is_vip` |
| `GET` | `/api/products/` | Active catalogue |
| `GET` | `/api/products/{slug}/` | Product detail with variants |
| `GET` | `/api/shipping/` | Active shipping options |
| `GET` | `/api/orders/` | Current user's orders |
| `POST` | `/api/orders/` | Create an order and reserve stock |
| `POST` | `/api/orders/{id}/cancel/` | Cancel and release the reservation |
| `POST` | `/api/payments/webhook/` | Mercado Pago notification |

Prices and totals are always computed server-side. The client sends variants
and quantities only.

## Stock and order lifecycle

Stock moves through `none → reserved → committed` or `none → reserved →
released`. Each transition is a conditional `UPDATE`, so it can only be won
once: repeated webhooks, double clicks and concurrent requests are harmless.

- `reserve_stock` decrements stock with `WHERE stock >= quantity`, which is
  what prevents overselling under concurrency.
- Mercado Pago orders reserve for 30 minutes (`MERCADO_PAGO_RESERVATION_TTL`).
  Cash orders reserve without an expiry and are pickup-only.
- `commit_stock` turns a reservation into a sale and never touches stock.
- `release_stock` returns the units exactly once.
- A payment approved after the reservation expired re-reserves. If the units
  are gone, the order stays unconfirmed with the payment id saved as evidence.

Read `orders/services.py` first if you are changing anything order-related. The
comments there explain why each transition is written the way it is.

## Management commands

```bash
./venv/bin/python manage.py seed_products              # demo catalogue, idempotent
./venv/bin/python manage.py seed_product_images        # mockups to Cloudinary
./venv/bin/python manage.py expire_stock_reservations  # release expired holds
./venv/bin/python manage.py expire_stock_reservations --dry-run
```

`expire_stock_reservations` runs on Railway as a cron every five minutes. It is
idempotent, so running it by hand is safe.

## Tests

```bash
./venv/bin/python manage.py test
```

56 tests. They cover order amounts, stock validation under concurrency,
cancellation, payment state transitions, reservation expiry, the admin actions
and the localization settings.

## Production

`Procfile`:

```
web: collectstatic --noinput && migrate --noinput && gunicorn config.wsgi \
  --bind 0.0.0.0:$PORT --workers 2 --timeout 60 --access-logfile -
```

WhiteNoise serves the collected static files, so the admin keeps its styles
with `DEBUG=False`. Product images live in Cloudinary, so there is no media
directory to serve.

Two deployment details that are easy to get wrong:

- gunicorn listens on the port Railway injects (`8080` today). The public
  domain must target that port.
- `postgres.railway.internal` only resolves inside Railway. Use `railway ssh`
  and `/app/.venv/bin/python`; `railway run` executes on your machine and
  cannot reach the private hostname.

## Settings worth knowing

| Setting | Value | Why |
| --- | --- | --- |
| `LANGUAGE_CODE` | `es-ar` | The admin is read by Spanish speakers |
| `TIME_ZONE` | `America/Argentina/San_Juan` | Order dates in local time |
| `USE_TZ` | `True` | Timestamps are still stored in UTC |
| `SECURE_SSL_REDIRECT` | `False` on Railway | Its edge already terminates TLS |
| `SESSION_COOKIE_SECURE` | `True` in production | Cookies only over HTTPS |
| `CORS_ALLOWED_ORIGINS` | no trailing slash | The origin comparison is exact |
