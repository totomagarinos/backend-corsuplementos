"""Genera mockups 1:1 de los productos demo y los sube a Cloudinary.

Por qué existe: `Product.image` es un `CloudinaryField` y
`ProductSerializer.get_image()` llama a `build_url()`, así que la imagen tiene
que existir en el cloud. No alcanza con apuntar a una URL externa.

Uso:
    python manage.py seed_product_images            # genera y sube los faltantes
    python manage.py seed_product_images --force    # regenera todo
    python manage.py seed_product_images --delete   # borra los assets del cloud

Es determinista: mismo slug => mismo public_id, así que re-ejecutarlo
sobreescribe en lugar de acumular basura en tu Cloudinary.
"""

import io

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from products.management.commands.seed_products import CATALOG
from products.models import Product

# Carpeta dentro de Cloudinary donde viven los assets demo, para poder
# identificarlos y borrarlos sin tocar las imágenes reales.
DEFAULT_FOLDER = "corsuplementos/products"

# Silueta y color por categoría. La variedad es intencional: una grilla con 24
# frascos idénticos se ve plana y no sirve para juzgar el layout.
#   parts: (x0, y0, x1, y1, radio, tono) en fracciones del lado del lienzo.
#          tono 'accent' = color pleno, 'body' = color mezclado con el fondo.
CATEGORY_VISUALS = {
    "whey": {
        "shape": "tub",
        "accent": (230, 57, 70),
        "parts": [
            (0.26, 0.22, 0.74, 0.31, 0.030, "accent"),
            (0.28, 0.29, 0.72, 0.82, 0.050, "body"),
        ],
        "label": (0.31, 0.36, 0.69, 0.76),
    },
    "plant_protein": {
        "shape": "pouch",
        "accent": (61, 220, 132),
        "parts": [
            (0.29, 0.18, 0.71, 0.24, 0.020, "accent"),
            (0.30, 0.22, 0.70, 0.83, 0.050, "body"),
        ],
        "label": (0.33, 0.31, 0.67, 0.78),
    },
    "creatine": {
        "shape": "tub",
        "accent": (139, 92, 246),
        "parts": [
            (0.27, 0.24, 0.73, 0.32, 0.030, "accent"),
            (0.29, 0.30, 0.71, 0.80, 0.050, "body"),
        ],
        "label": (0.32, 0.37, 0.68, 0.74),
    },
    "isotonics": {
        "shape": "jar",
        "accent": (34, 211, 238),
        "parts": [
            (0.35, 0.20, 0.65, 0.33, 0.040, "accent"),
            (0.30, 0.32, 0.70, 0.79, 0.060, "body"),
        ],
        "label": (0.33, 0.38, 0.67, 0.74),
    },
    "recovery": {
        "shape": "pouch",
        "accent": (244, 163, 64),
        "parts": [
            (0.29, 0.19, 0.71, 0.25, 0.020, "accent"),
            (0.30, 0.23, 0.70, 0.82, 0.050, "body"),
        ],
        "label": (0.33, 0.32, 0.67, 0.77),
    },
    "gels": {
        "shape": "sachet",
        "accent": (163, 230, 53),
        "parts": [
            (0.38, 0.16, 0.62, 0.21, 0.020, "accent"),
            (0.37, 0.20, 0.63, 0.84, 0.030, "body"),
        ],
        "label": (0.39, 0.30, 0.61, 0.78),
    },
    "caffeine": {
        "shape": "jar",
        "accent": (245, 158, 11),
        "parts": [
            (0.36, 0.22, 0.64, 0.34, 0.040, "accent"),
            (0.31, 0.33, 0.69, 0.78, 0.060, "body"),
        ],
        "label": (0.34, 0.39, 0.66, 0.73),
    },
    "magnesium": {
        "shape": "jar",
        "accent": (99, 102, 241),
        "parts": [
            (0.36, 0.21, 0.64, 0.33, 0.040, "accent"),
            (0.31, 0.32, 0.69, 0.79, 0.060, "body"),
        ],
        "label": (0.34, 0.38, 0.66, 0.74),
    },
    "omega3": {
        "shape": "jar",
        "accent": (20, 184, 166),
        "parts": [
            (0.35, 0.23, 0.65, 0.34, 0.040, "accent"),
            (0.30, 0.33, 0.70, 0.77, 0.060, "body"),
        ],
        "label": (0.33, 0.39, 0.67, 0.72),
    },
    "multivitamins": {
        "shape": "jar",
        "accent": (236, 72, 153),
        "parts": [
            (0.36, 0.22, 0.64, 0.33, 0.040, "accent"),
            (0.31, 0.32, 0.69, 0.78, 0.060, "body"),
        ],
        "label": (0.34, 0.38, 0.66, 0.73),
    },
    "other": {
        "shape": "box",
        "accent": (148, 163, 184),
        "parts": [
            (0.25, 0.22, 0.75, 0.29, 0.010, "accent"),
            (0.26, 0.27, 0.74, 0.81, 0.010, "body"),
        ],
        "label": (0.30, 0.35, 0.70, 0.76),
    },
}

FONT_BOLD = "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"
FONT_MEDIUM = "/usr/share/fonts/truetype/ubuntu/Ubuntu-M.ttf"


def _mix(accent, factor=0.34, base=(20, 20, 20)):
    """Mezcla el color de acento con el fondo para el cuerpo del envase."""
    return tuple(round(c * factor + b * (1 - factor)) for c, b in zip(accent, base))


def _background(size):
    """Degradado vertical, sutil, en la misma línea del tema oscuro."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    top, bottom = (28, 28, 28), (14, 14, 14)

    for y in range(size):
        ratio = y / (size - 1)
        draw.line(
            [(0, y), (size, y)],
            fill=tuple(round(t + (b - t) * ratio) for t, b in zip(top, bottom)),
        )

    return img


def _add_glow(img, accent, size):
    """Resplandor difuso detrás del producto (y una sombra de apoyo abajo)."""
    from PIL import Image, ImageDraw, ImageFilter

    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    draw.ellipse(
        [0.20 * size, 0.24 * size, 0.80 * size, 0.84 * size],
        fill=(*accent, 46),
    )
    draw.ellipse([0.28 * size, 0.80 * size, 0.72 * size, 0.87 * size], fill=(0, 0, 0, 110))

    layer = layer.filter(ImageFilter.GaussianBlur(size * 0.05))
    return Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")


def _draw_tracked(draw, xy, text, font, fill, tracking):
    """Texto con espaciado extra entre letras (para la marca)."""
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font) + tracking


def _wrap(draw, text, font, max_width):
    lines, current = [], ""

    for word in text.split():
        trial = f"{current} {word}".strip()

        if not current or draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines


def render_mockup(product, variant, size):
    """Devuelve un PNG 1:1 en memoria con la silueta, la marca y el nombre."""
    from PIL import Image, ImageDraw, ImageFont

    visual = CATEGORY_VISUALS[product.category]
    accent = visual["accent"]
    body = _mix(accent)

    img = _background(size)
    img = _add_glow(img, accent, size)

    draw = ImageDraw.Draw(img, "RGBA")

    for x0, y0, x1, y1, radius, tone in visual["parts"]:
        draw.rounded_rectangle(
            [x0 * size, y0 * size, x1 * size, y1 * size],
            radius=radius * size,
            fill=accent if tone == "accent" else body,
        )

    # Contorno sutil: separa el envase del fondo sin recortarlo con un borde duro.
    for x0, y0, x1, y1, radius, _ in visual["parts"]:
        draw.rounded_rectangle(
            [x0 * size, y0 * size, x1 * size, y1 * size],
            radius=radius * size,
            outline=(255, 255, 255, 26),
            width=max(2, round(size * 0.002)),
        )

    lx0, ly0, lx1, ly1 = visual["label"]
    label_w = (lx1 - lx0) * size
    center_x = (lx0 + lx1) / 2 * size

    brand_font = ImageFont.truetype(FONT_MEDIUM, round(size * 0.028))
    detail_font = ImageFont.truetype(FONT_MEDIUM, round(size * 0.034))

    brand = product.brand.upper()
    tracker = size * 0.006
    brand_w = sum(draw.textlength(c, font=brand_font) + tracker for c in brand) - tracker

    draw.text(
        (center_x - brand_w / 2, ly0 * size),
        "",
        font=brand_font,
    )
    _draw_tracked(
        draw,
        (center_x - brand_w / 2, ly0 * size),
        brand,
        brand_font,
        fill=(255, 255, 255, 200),
        tracking=tracker,
    )

    detail = variant.size if not variant.flavor else f"{variant.size} · {variant.flavor}"
    detail_h = size * 0.034 * 1.35
    detail_y = ly1 * size - detail_h

    detail_w = draw.textlength(detail, font=detail_font)
    draw.text(
        (center_x - detail_w / 2, detail_y),
        detail,
        font=detail_font,
        fill=(255, 255, 255, 150),
    )

    # El nombre se achica hasta entrar en el espacio libre entre marca y detalle.
    name_top = ly0 * size + size * 0.05
    name_bottom = detail_y - size * 0.02
    available_h = name_bottom - name_top

    name_size = round(size * 0.062)
    while name_size > round(size * 0.030):
        name_font = ImageFont.truetype(FONT_BOLD, name_size)
        lines = _wrap(draw, product.name, name_font, label_w)
        line_h = (name_font.getbbox("Ag")[3] - name_font.getbbox("Ag")[1]) * 1.18

        if len(lines) <= 3 and len(lines) * line_h <= available_h:
            break

        name_size -= round(size * 0.003)

    name_font = ImageFont.truetype(FONT_BOLD, name_size)
    lines = _wrap(draw, product.name, name_font, label_w)
    line_h = (name_font.getbbox("Ag")[3] - name_font.getbbox("Ag")[1]) * 1.18
    y = name_top + max(0, (available_h - len(lines) * line_h) / 2)

    for line in lines:
        line_w = draw.textlength(line, font=name_font)
        draw.text(
            (center_x - line_w / 2, y),
            line,
            font=name_font,
            fill=(255, 255, 255, 245),
        )
        y += line_h

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)

    return buffer


class Command(BaseCommand):
    help = "Genera y sube a Cloudinary los mockups 1:1 de los productos demo."

    def add_arguments(self, parser):
        parser.add_argument(
            "--folder",
            default=DEFAULT_FOLDER,
            help=f"Carpeta destino en Cloudinary (default {DEFAULT_FOLDER}).",
        )
        parser.add_argument(
            "--size",
            type=int,
            default=1200,
            help="Lado del cuadrado en px (default 1200).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Regenera y re-sube aunque la imagen ya esté asignada.",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Borra de Cloudinary los assets demo y limpia el campo image.",
        )

    def handle(self, *args, **options):
        try:
            import PIL  # noqa: F401
        except ImportError as exc:
            raise CommandError(
                "Falta Pillow. Instalalo con: ./venv/bin/pip install Pillow"
            ) from exc

        import cloudinary.uploader

        folder = options["folder"].strip("/")
        size = options["size"]
        force = options["force"]

        if size < 200:
            raise CommandError("--size tiene que ser de al menos 200px.")

        slugs = [item["slug"] for item in CATALOG]
        products = (
            Product.objects.filter(slug__in=slugs).prefetch_related("variants").order_by("id")
        )
        total_expected = len(slugs)

        if not products:
            raise CommandError(
                "No hay productos demo. Corré primero: manage.py seed_products"
            )

        if options["delete"]:
            self._delete_assets(cloudinary, folder, products)
            return

        uploaded, skipped, failed = 0, 0, []

        for product in products:
            public_id = f"{folder}/demo-{product.slug}"

            if not force and str(product.image) == public_id:
                skipped += 1
                continue

            variant = product.variants.first()

            if variant is None:
                failed.append(f"{product.slug} (sin variantes)")
                continue

            try:
                result = cloudinary.uploader.upload(
                    render_mockup(product, variant, size),
                    public_id=public_id,
                    folder=None,
                    overwrite=True,
                    invalidate=True,
                    resource_type="image",
                )
            except Exception as exc:  # noqa: BLE001 — se reporta y se sigue
                failed.append(f"{product.slug} ({type(exc).__name__}: {exc})")
                continue

            with transaction.atomic():
                product.image = result["public_id"]
                product.save(update_fields=["image", "updated_at"])

            uploaded += 1

        if uploaded:
            self.stdout.write(
                self.style.SUCCESS(f"Imágenes subidas: {uploaded} ({size}x{size}, 1:1).")
            )

        if skipped:
            self.stdout.write(f"Ya tenían imagen: {skipped}.")

        if failed:
            self.stdout.write(self.style.ERROR(f"Fallaron {len(failed)}: {failed}"))

        pending = total_expected - products.count()
        if pending:
            self.stdout.write(
                self.style.WARNING(f"Faltan crear {pending} productos demo (slug inexistente).")
            )

    def _delete_assets(self, cloudinary, folder, products):
        removed, cleared = 0, 0

        for product in products:
            public_id = f"{folder}/demo-{product.slug}"

            try:
                result = cloudinary.uploader.destroy(public_id, invalidate=True)
                if result.get("result") == "ok":
                    removed += 1
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.ERROR(f"{product.slug}: {exc}"))

            if str(product.image) == public_id:
                product.image = ""
                product.save(update_fields=["image", "updated_at"])
                cleared += 1

        self.stdout.write(
            self.style.WARNING(
                f"Assets borrados en Cloudinary: {removed}. Campos image limpiados: {cleared}."
            )
        )
