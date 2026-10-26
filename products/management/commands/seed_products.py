"""Catálogo de productos de prueba.

Genera un catálogo demo para previsualizar la grilla del frontend con volumen
realista y revisarlo desde el admin de Django.

Uso:
    python manage.py seed_products              # crea el catálogo completo
    python manage.py seed_products --count 12   # solo los primeros 12
    python manage.py seed_products --reset      # borra y vuelve a crear
    python manage.py seed_products --delete     # borra el catálogo demo

Es idempotente: cada producto se identifica por su slug, así que re-ejecutarlo
nunca duplica filas. `--delete` y `--reset` tocan únicamente los slugs
declarados en CATALOG, así que no pueden borrar productos reales.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from products.models import Product, Variant

# Las imágenes NO se asignan acá. Las genera y sube `seed_product_images`, que
# necesita hablar con Cloudinary; separar eso mantiene este comando escribiendo
# sólo en la base local.

# Bullets por categoría. El primer elemento es el copete del producto; el resto
# se renderiza como lista, igual que los productos ya cargados a mano.
CATEGORY_COPY = {
    "whey": (
        "Proteína de rápida absorción para completar tu alimentación.",
        (
            "Favorece la recuperación muscular post-entreno",
            "Alta concentración proteica por porción",
            "Se disuelve fácil en agua o leche",
        ),
    ),
    "plant_protein": (
        "Proteína de origen vegetal, sin lactosa.",
        (
            "Mezcla de arveja y arroz para perfil completo de aminoácidos",
            "Apta para dietas veganas y vegetarianas",
            "Textura liviana, sin sensación pesada",
        ),
    ),
    "creatine": (
        "Suplemento clásico para entrenar fuerza.",
        (
            "Más masa muscular junto a una rutina de fuerza",
            "Mejora el rendimiento en esfuerzos cortos e intensos",
            "Sin sabor, se combina con cualquier bebida",
        ),
    ),
    "isotonics": (
        "Bebida isotónica para reponer lo que perdés al transpirar.",
        (
            "Repone electrolitos durante el esfuerzo",
            "Favorece la hidratación en entrenamientos largos",
            "Sin gas y de fácil digestión",
        ),
    ),
    "recovery": (
        "Bebida de recuperación para tomar al terminar de entrenar.",
        (
            "Combina carbohidratos y proteína en la proporción post-entreno",
            "Repone glucógeno y acelera la recuperación",
            "Listo para consumir, solo agregar agua",
        ),
    ),
    "gels": (
        "Carbohidrato listo para consumir durante el esfuerzo.",
        (
            "Mantiene la energía en entrenamientos y competencias largas",
            "Formato práctico, no necesita agua para bajarlo",
            "De rápida absorción y fácil digestión",
        ),
    ),
    "caffeine": (
        "Cafeína en cápsulas para los entrenamientos más exigentes.",
        (
            "Mejora el foco y el nivel de alerta",
            "Aumenta la resistencia percibida en esfuerzos largos",
            "Dosis por cápsula fácil de controlar",
        ),
    ),
    "magnesium": (
        "Magnesio para complementar tu recuperación diaria.",
        (
            "Contribuye a reducir calambres y contracturas",
            "Ayuda al funcionamiento muscular y nervioso normal",
            "Ideal para tomar antes de dormir",
        ),
    ),
    "omega3": (
        "Ácidos grasos esenciales para tu salud general.",
        (
            "Aporta EPA y DHA de alta pureza",
            "Contribuye al cuidado cardiovascular",
            "Libre de metales pesados, con análisis por lote",
        ),
    ),
    "multivitamins": (
        "Multivitamínico diario para cubrir los micronutrientes básicos.",
        (
            "Cubre vitaminas y minerales de la rutina diaria",
            "Refuerza el sistema inmune",
            "De toma simple, una vez por día",
        ),
    ),
    "other": (
        "Accesorio para acompañar tu rutina.",
        (
            "Material resistente y de fácil limpieza",
            "Pensado para el uso diario en el gimnasio",
            "Se combina con el resto del catálogo",
        ),
    ),
}

# Catálogo demo. `sizes` x `flavors` genera las variantes (se recortan con
# --max-variants).
CATALOG = [
    # --- Whey protein ---
    {
        "slug": "proteina-onefit-5lb",
        "brand": "ONEFIT",
        "name": "Classic Whey 5LB",
        "category": "whey",
        "price": "72000.00",
        "vip_price": "65000.00",
        "sizes": ("2.27kg",),
        "flavors": ("Chocolate", "Vainilla", "Frutilla"),
    },
    {
        "slug": "iso-whey-nutremax",
        "brand": "NUTREMAX",
        "name": "Iso Whey Zero",
        "category": "whey",
        "price": "45000.00",
        "vip_price": "40500.00",
        "sizes": ("1kg",),
        "flavors": ("Chocolate", "Cookies"),
    },
    {
        "slug": "proteina-onefit-gold-4lb",
        "brand": "ONEFIT",
        "name": "Whey Gold 4LB",
        "category": "whey",
        "price": "58000.00",
        "vip_price": "52000.00",
        "sizes": ("1.8kg",),
        "flavors": ("Chocolate", "Vainilla"),
    },
    {
        "slug": "whey-nutremax-1kg",
        "brand": "NUTREMAX",
        "name": "Whey Concentrada 1kg",
        "category": "whey",
        "price": "39800.00",
        "vip_price": None,
        "sizes": ("1kg",),
        "flavors": ("Chocolate", "Frutilla"),
    },
    # --- Plant-based protein ---
    {
        "slug": "proteina-vegetal-nutremax",
        "brand": "NUTREMAX",
        "name": "Plant Protein Vanilla",
        "category": "plant_protein",
        "price": "41000.00",
        "vip_price": None,
        "sizes": ("900g",),
        "flavors": ("Vainilla", "Chocolate"),
    },
    {
        "slug": "vegan-blend-onefit",
        "brand": "ONEFIT",
        "name": "Vegan Blend",
        "category": "plant_protein",
        "price": "43500.00",
        "vip_price": "39000.00",
        "sizes": ("900g",),
        "flavors": ("Chocolate", "Frutilla"),
    },
    # --- Creatine ---
    {
        "slug": "creatina-onefit-300",
        "brand": "ONEFIT",
        "name": "Creatina Monohidrato 300g",
        "category": "creatine",
        "price": "18800.00",
        "vip_price": "15900.00",
        "sizes": ("300g",),
        "flavors": ("Sin sabor",),
    },
    {
        "slug": "creatina-hcl-nutremax",
        "brand": "NUTREMAX",
        "name": "Creatina HCL 150g",
        "category": "creatine",
        "price": "21500.00",
        "vip_price": None,
        "sizes": ("150g",),
        "flavors": ("Sin sabor",),
    },
    # --- Isotonics ---
    {
        "slug": "hidromax-isotonic-limon",
        "brand": "NUTREMAX",
        "name": "Hydromax Isotonic Limón",
        "category": "isotonics",
        "price": "8500.00",
        "vip_price": "7600.00",
        "sizes": ("500g",),
        "flavors": ("Limón",),
    },
    {
        "slug": "hidromax-isotonic-naranja",
        "brand": "NUTREMAX",
        "name": "Hydromax Isotonic Naranja",
        "category": "isotonics",
        "price": "8500.00",
        "vip_price": "7600.00",
        "sizes": ("500g",),
        "flavors": ("Naranja",),
    },
    # --- Recovery drinks ---
    {
        "slug": "recovery-drink-nutremax",
        "brand": "NUTREMAX",
        "name": "Recovery Drink Chocolate",
        "category": "recovery",
        "price": "12000.00",
        "vip_price": None,
        "sizes": ("1kg",),
        "flavors": ("Chocolate",),
    },
    {
        "slug": "recovery-pro-onefit",
        "brand": "ONEFIT",
        "name": "Recovery Pro Post-Entreno",
        "category": "recovery",
        "price": "13400.00",
        "vip_price": "11900.00",
        "sizes": ("900g",),
        "flavors": ("Chocolate", "Vainilla"),
    },
    # --- Energy gels ---
    {
        "slug": "gel-nutremax-cafe",
        "brand": "NUTREMAX",
        "name": "Endurance Gel Café",
        "category": "gels",
        "price": "2300.00",
        "vip_price": None,
        "sizes": ("200g",),
        "flavors": ("Café",),
    },
    {
        "slug": "gel-nutremax-frutilla",
        "brand": "NUTREMAX",
        "name": "Endurance Gel Frutilla",
        "category": "gels",
        "price": "2300.00",
        "vip_price": None,
        "sizes": ("200g",),
        "flavors": ("Frutilla",),
    },
    {
        "slug": "gel-nutremax-caja12",
        "brand": "NUTREMAX",
        "name": "Endurance Gel Caja x12",
        "category": "gels",
        "price": "26400.00",
        "vip_price": "23900.00",
        "sizes": ("12 unidades",),
        "flavors": ("Surtidos",),
    },
    # --- Caffeine capsules ---
    {
        "slug": "cafeina-onefit-200mg",
        "brand": "ONEFIT",
        "name": "Cafeína 200mg",
        "category": "caffeine",
        "price": "9500.00",
        "vip_price": "8200.00",
        "sizes": ("60 caps",),
        "flavors": ("Sin sabor",),
    },
    {
        "slug": "caffeine-pro-nutremax",
        "brand": "NUTREMAX",
        "name": "Caffeine Pro 90",
        "category": "caffeine",
        "price": "12800.00",
        "vip_price": None,
        "sizes": ("90 caps",),
        "flavors": ("Sin sabor",),
    },
    # --- Magnesium ---
    {
        "slug": "magnesio-onefit-120",
        "brand": "ONEFIT",
        "name": "Magnesio Citrato 120 caps",
        "category": "magnesium",
        "price": "11200.00",
        "vip_price": "9900.00",
        "sizes": ("120 caps",),
        "flavors": ("Sin sabor",),
    },
    {
        "slug": "magnesio-nutremax-polvo",
        "brand": "NUTREMAX",
        "name": "Magnesio Citrato en Polvo",
        "category": "magnesium",
        "price": "10500.00",
        "vip_price": None,
        "sizes": ("300g",),
        "flavors": ("Limón", "Neutro"),
    },
    # --- Omega 3 ---
    {
        "slug": "omega3-onefit-1000",
        "brand": "ONEFIT",
        "name": "Omega-3 1000mg",
        "category": "omega3",
        "price": "14500.00",
        "vip_price": None,
        "sizes": ("60 caps",),
        "flavors": ("Sin sabor",),
    },
    {
        "slug": "omega3-ultra-nutremax",
        "brand": "NUTREMAX",
        "name": "Omega-3 Ultra",
        "category": "omega3",
        "price": "19900.00",
        "vip_price": "17500.00",
        "sizes": ("120 caps",),
        "flavors": ("Sin sabor",),
    },
    # --- Multivitamins ---
    {
        "slug": "multivitaminico-onefit",
        "brand": "ONEFIT",
        "name": "Multivitamínico Diario",
        "category": "multivitamins",
        "price": "13400.00",
        "vip_price": "11900.00",
        "sizes": ("60 caps",),
        "flavors": ("Sin sabor",),
    },
    # --- Otros ---
    {
        "slug": "shaker-onefit-700",
        "brand": "ONEFIT",
        "name": "Shaker 700ml",
        "category": "other",
        "price": "8900.00",
        "vip_price": None,
        "sizes": ("700ml",),
        "flavors": ("Negro", "Rojo"),
    },
    {
        "slug": "barra-proteica-nutremax-caja12",
        "brand": "NUTREMAX",
        "name": "Barra Proteica Caja x12",
        "category": "other",
        "price": "15600.00",
        "vip_price": "13900.00",
        "sizes": ("12 unidades",),
        "flavors": ("Chocolate",),
    },
]

# Stock variable pero determinista: mismo catálogo, mismos números en cada corrida.
STOCK_CYCLE = (10, 6, 8, 12, 4)


def build_description(category: str) -> str:
    """Arma el texto con el mismo formato que los productos cargados a mano."""
    blurb, bullets = CATEGORY_COPY[category]
    body = "\r\n\t•\t".join(bullets)
    return f"{blurb}\r\n\r\n\t•\t{body}"


class Command(BaseCommand):
    help = "Crea un catálogo de productos de prueba (idempotente)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=len(CATALOG),
            help=f"Cantidad de productos a crear (máximo {len(CATALOG)}).",
        )
        parser.add_argument(
            "--max-variants",
            type=int,
            default=3,
            help="Tope de variantes por producto (default 3).",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Borra el catálogo demo y lo vuelve a crear.",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Borra el catálogo demo y termina.",
        )

    def handle(self, *args, **options):
        count = options["count"]
        max_variants = options["max_variants"]

        if count < 1:
            raise CommandError("--count tiene que ser mayor a 0.")
        if max_variants < 1:
            raise CommandError("--max-variants tiene que ser mayor a 0.")

        if options["delete"] or options["reset"]:
            deleted = self._delete_demo_products()
            self.stdout.write(
                self.style.WARNING(f"Borrados {deleted} productos demo (y sus variantes).")
            )
            if options["delete"]:
                return

        selection = CATALOG[:count]

        with transaction.atomic():
            created_products, skipped_products, created_variants = self._seed(
                selection, max_variants
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Productos demo: {created_products} creados, {skipped_products} ya existían "
                f"(slug repetido). Variantes creadas: {created_variants}."
            )
        )
        self.stdout.write(
            f"Total en base: {Product.objects.count()} productos / "
            f"{Variant.objects.count()} variantes."
        )

        if created_products:
            self.stdout.write(
                self.style.WARNING(
                    "Los productos nuevos no tienen imagen. Generá y subí los mockups con: "
                    "manage.py seed_product_images"
                )
            )

    def _demo_slugs(self) -> list[str]:
        return [item["slug"] for item in CATALOG]

    def _delete_demo_products(self) -> int:
        # Sólo toca slugs declarados en CATALOG: no puede borrar productos reales.
        deleted, _ = Product.objects.filter(slug__in=self._demo_slugs()).delete()
        return deleted

    def _seed(self, selection: list[dict], max_variants: int) -> tuple[int, int, int]:
        created_products = 0
        skipped_products = 0
        created_variants = 0

        for index, item in enumerate(selection):
            product, created = Product.objects.get_or_create(
                slug=item["slug"],
                defaults={
                    "brand": item["brand"],
                    "name": item["name"],
                    "description": build_description(item["category"]),
                    "category": item["category"],
                    "price": Decimal(item["price"]),
                    "vip_price": (
                        Decimal(item["vip_price"]) if item["vip_price"] else None
                    ),
                    "is_active": True,
                },
            )

            if not created:
                skipped_products += 1
                continue

            created_products += 1
            pairs = [
                (size, flavor) for size in item["sizes"] for flavor in item["flavors"]
            ]

            for variant_index, (size, flavor) in enumerate(pairs[:max_variants], start=1):
                _, variant_created = Variant.objects.get_or_create(
                    sku=f"{item['slug'].upper()}-{variant_index}",
                    defaults={
                        "product": product,
                        "size": size,
                        "flavor": flavor,
                        "stock": STOCK_CYCLE[(index + variant_index) % len(STOCK_CYCLE)],
                        "is_active": True,
                    },
                )
                if variant_created:
                    created_variants += 1

        return created_products, skipped_products, created_variants
