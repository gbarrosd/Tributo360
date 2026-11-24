import time

from django.core.management.base import BaseCommand, CommandError

from engines.registro import registrar_versoes_atuais


class Command(BaseCommand):
    help = "Consulta a versão de cada motor e grava em VersaoMotor."

    def add_arguments(self, parser):
        parser.add_argument("--aguardar", type=int, default=0, help="Segundos para esperar os motores subirem.")

    def handle(self, *args, aguardar=0, **options):
        limite = time.monotonic() + aguardar
        while True:
            versoes = registrar_versoes_atuais()
            if all(versoes.values()) or time.monotonic() >= limite:
                break
            time.sleep(5)
        for motor, versao in versoes.items():
            self.stdout.write(f"{motor}: {versao or 'indisponível'}")
        if not all(versoes.values()):
            raise CommandError("Algum motor não respondeu.")
