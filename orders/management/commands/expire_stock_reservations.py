from django.core.management.base import BaseCommand

from orders.services import release_expired_reservations


class Command(BaseCommand):
    help = "Release stock held by payment reservations that have expired."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report how many expired reservations exist.",
        )

    def handle(self, *args, **options):
        if options["dry_run"]:
            pending = release_expired_reservations(dry_run=True)
            self.stdout.write(f"{pending} reserva(s) vencida(s) sin liberar.")
            return

        released = release_expired_reservations()
        self.stdout.write(self.style.SUCCESS(f"{released} reserva(s) liberada(s)."))
