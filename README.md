# CorSuplementos — Backend

Django REST API for the CorSuplementos store: the catalogue, stock, orders, the
Mercado Pago integration and the seller's admin. Final project of the Conquer
Blocks full-stack program.

The store sells in San Juan, Argentina. Shipping is never charged online.

The storefront is a separate Angular repository. It talks to this API only
through JSON, so either side can be replaced or redeployed on its own.

## Live environment

| Service | URL |
| --- | --- |
| API | https://web-production-d0b6b4df.up.railway.app/api/ |
| Admin | https://web-production-d0b6b4df.up.railway.app/admin/ |
| Store | https://frontend-corsuplementos.vercel.app |

Mercado Pago runs with **test credentials**: no real seller account and no real
money moves. Checkout completes with Mercado Pago's test cards.

## Trying it out

The store sells in Argentina, so Mercado Pago is the only online payment method
and it does not operate outside the region. That does not block a full review:
the checkout flow does not depend on Mercado Pago.

**Without Mercado Pago — works from anywhere**

1. Register an account.
2. Add products to the cart and open the checkout.
3. Choose **Retiro en local** (local pickup) and **Efectivo** (cash).
4. The order is created, stock is reserved, and the confirmation page and the
   order email both fire.

Cash is accepted only together with local pickup, and that rule is enforced on
the server, not just hidden in the UI.

**With Mercado Pago — Argentina only**

A test purchase needs a test buyer account, created from the Mercado Pago panel
under *Tus integraciones → Cuentas de prueba*, plus one of the published test
cards. Test credentials are issued for a test seller account, so no real money
is involved at any point.

## Architecture

```
Browser
  │
  │  HTTPS · JSON · JWT
  ▼
Angular SPA (Vercel) ──────► Django REST API (Railway · gunicorn · WhiteNoise)
                                    │
                                    ├──► PostgreSQL 18 (Railway)
                                    ├──► Cloudinary (product images)
                                    └──► Mercado Pago (checkout + webhook)
```

## Requirements

- Python 3.13+
- PostgreSQL (16 in the bundled `docker-compose.yml`, 18 on Railway)
- A Cloudinary account for product images
- Mercado Pago credentials (test credentials are enough)

## Stack

Django 6.1, Django REST Framework, SimpleJWT, PostgreSQL, Cloudinary, Mercado
Pago SDK, gunicorn and WhiteNoise.

## Business rules

The UI is the easy part. These are the rules the code actually enforces, and
they are the ones worth reading before changing anything.

1. **Shipping is never charged online.** `Order.shipping_cost` is computed
   server-side and always `0`. Uber delivery is paid to the driver, local
   pickup is free, and national shipping is quoted by the seller afterwards.
2. **Prices are computed server-side.** The client sends variants and
   quantities; it never sends a price or a total. `subtotal` and `total` are
   calculated from the database and the order lines are copied with the price
   they had at purchase time.
3. **Stock is reserved, not sold.** Creating an order decrements stock inside a
   conditional `UPDATE ... WHERE stock >= quantity`, so two buyers racing for
   the last unit serialize on the row lock and the loser updates zero rows and
   receives a readable error.
4. **Mercado Pago reservations expire.** An online order holds its units for 30
   minutes (`MERCADO_PAGO_RESERVATION_TTL`). `expire_stock_reservations`
   releases whatever is still reserved after that window, and it runs as a
   Railway cron every five minutes.
5. **Cash is pickup only.** `payment_method = cash` is rejected unless the
   shipping option is local pickup. Cash orders reserve stock without an
   expiry, because the customer is on the way to the counter.
6. **Every stock transition happens exactly once.** `reserve`, `commit` and
   `release` are conditional database transitions, not Python `if`s. A repeated
   Mercado Pago webhook, a double cancellation or two concurrent payment
   notifications are all harmless.
7. **A late approval still tries to honour the sale.** If the reservation
   already expired, the payment confirmation reserves the units again. If they
   are gone, the order stays unconfirmed with the payment id saved as evidence
   for a human, instead of confirming a sale that cannot be fulfilled.
8. **A confirmed sale is final.** A rejection notification that arrives after
   the order was confirmed or delivered does not undo it. Conversely,
   cancelling a delivered order is refused.
9. **VIP pricing is earned, not assigned.** When an order reaches `confirmed`
   with a total of `10000` or more, the customer becomes VIP. VIPs pay
   `Product.vip_price` when the product defines one, and the regular price
   otherwise.
10. **The webhook is verified.** When `MERCADOPAGO_WEBHOOK_VERIFY_SIGNATURE` is
    on, the signature is checked before any order is touched.
11. **The admin is the client panel.** Confirming payments, cancelling with
    stock release and releasing expired reservations are Django admin actions.
    There is no custom admin UI in Angular, by design.

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

## Local development

```bash
python -m venv venv
./venv/bin/pip install -r requirements.txt

docker compose up -d db          # PostgreSQL 16 on localhost:5432
cp .env.example .env             # then fill in the values

./venv/bin/python manage.py migrate
./venv/bin/python manage.py createsuperuser
./venv/bin/python manage.py runserver
```

- API: `http://localhost:8000/api/`
- Admin: `http://localhost:8000/admin/`

Every setting reads from the environment and falls back to the value above, so
a fresh clone works without touching `config/settings.py`.

Optional demo catalogue:

```bash
./venv/bin/python manage.py seed_products          # idempotent, keyed by slug
```

The frontend is a separate repository; start it on `http://localhost:4200` and
it will call this API at `http://localhost:8000/api`.

## Environment variables

`.env.example` documents every variable and where to obtain it. The ones that
matter most:

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY` | Django signing key. Must differ between local and production. |
| `DEBUG` | `True` locally, `False` in production. |
| `DB_*` | PostgreSQL connection. On Railway, reference the database service. |
| `CLOUDINARY_*` | Product image storage. |
| `MERCADOPAGO_*` | Checkout credentials and webhook secret. |
| `ALLOWED_HOSTS` | Hosts Django will answer for. |
| `CORS_ALLOWED_ORIGINS` | Origins allowed to call the API. No trailing slash. |
| `CSRF_TRUSTED_ORIGINS` | Origins allowed to POST to the admin, with scheme. |
| `FRONTEND_URL` | Where Mercado Pago sends the customer back. |
| `SECURE_SSL_REDIRECT` | `False` on Railway: its edge already terminates TLS. |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | `True` in production. |

## Settings worth knowing

| Setting | Value | Why |
| --- | --- | --- |
| `LANGUAGE_CODE` | `es-ar` | The admin is read by Spanish speakers |
| `TIME_ZONE` | `America/Argentina/San_Juan` | Order dates in local time |
| `USE_TZ` | `True` | Timestamps are still stored in UTC |
| `SECURE_SSL_REDIRECT` | `False` on Railway | Its edge already terminates TLS |
| `SESSION_COOKIE_SECURE` | `True` in production | Cookies only over HTTPS |
| `CORS_ALLOWED_ORIGINS` | no trailing slash | The origin comparison is exact |

## Management commands

```bash
./venv/bin/python manage.py seed_products              # demo catalogue, idempotent
./venv/bin/python manage.py expire_stock_reservations  # release expired holds
./venv/bin/python manage.py expire_stock_reservations --dry-run
```

`expire_stock_reservations` runs on Railway as a cron every five minutes. It is
idempotent, so running it by hand is safe.

## Tests

```bash
./venv/bin/python manage.py test
```

58 tests. They cover order amounts, stock validation under concurrency,
cancellation, payment state transitions, reservation expiry, the admin actions,
the Mercado Pago preference payload and the localization settings.

## Deployment

The web service is named `web`; PostgreSQL is a separate service named
`Postgres`; the cron runs in a third service, `backend-corsuplementos`.
`Procfile` runs, in order:

```
web: collectstatic --noinput && migrate --noinput && gunicorn config.wsgi \
  --bind 0.0.0.0:$PORT --workers 2 --timeout 60 --access-logfile -
```

WhiteNoise serves the collected static files, so the admin keeps its styles
with `DEBUG=False`. Product images live in Cloudinary, so there is no media
directory to serve.

Notes that cost time if you learn them the hard way:

- gunicorn listens on the port Railway injects (`8080` in the current deploy).
  The domain must target that port, not `8000`.
- `postgres.railway.internal` only resolves inside Railway. `railway run`
  executes locally and therefore cannot reach the database; use `railway ssh`
  and the interpreter at `/app/.venv/bin/python`, which is not on the SSH
  `PATH`.
- `CORS_ALLOWED_ORIGINS` must not end with a slash. Browsers send the origin
  without one and the comparison is exact.

### Expiry cron — Railway

A third service, built from the same repository, holds the schedule. It has to
be a separate service: the command exits when it finishes, and a web service has
to stay running.

- **Service name**: `backend-corsuplementos`
- **Cron schedule**: `*/5 * * * *`
- **Start command**: `/app/.venv/bin/python manage.py expire_stock_reservations`

The command prints how many reservations it released and exits. It is safe to
run as often as you like: each order releases its units at most once.

Two traps worth knowing:

- `railway cron list` and `railway cron run` target the **linked** service,
  which is `web`. Without `--service backend-corsuplementos` they report no
  runs, because `web` has no schedule. That reads like "the cron does not
  exist" when the cron is fine.
- Do not move `cronSchedule` into a `railway.json`. Both services deploy the
  same repository, so config-as-code would apply the schedule to `web` as well
  and turn it into a cron job.

## Repository layout

```
backend/
├── config/          settings, URLs, WSGI entry point
├── users/           custom user, JWT auth, VIP flag
├── products/        catalogue, variants, stock, seeders
├── shipping/        pickup / local delivery / national options
├── orders/          order lifecycle, stock primitives, admin actions
├── payments/        Mercado Pago preference and webhook
├── Procfile         production start command
└── manage.py
```

## Admin

The Django admin is the seller's panel: it manages products, variants, stock,
shipping options, orders and users, and it owns the actions that move an order
through its lifecycle.

## Hardening for a real deployment

The defaults above are right for a demo. A store handling real money needs
more, and these are the gaps this project leaves open on purpose:

- a `staff` user with the permissions the seller actually needs, not a
  superuser;
- login rate limiting (`django-axes`), which Django does not provide by
  default;
- a non-default admin path, to cut down on automated scanning;
- a custom domain, with `api.` and `admin.` subdomains pointing at the same
  Railway service.
