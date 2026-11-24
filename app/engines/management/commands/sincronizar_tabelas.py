from django.core.management.base import BaseCommand

from engines.sincronizacao import sincronizar_tabelas


class Command(BaseCommand):
    help = "Copia CST, cClassTrib, NCM e NBS do motor do regime regular (só se a versão da base mudou)."

    def add_arguments(self, parser):
        parser.add_argument("--forcar", action="store_true", help="Sincroniza mesmo se a versão já foi copiada.")

    def handle(self, *args, forcar=False, **options):
        execucao = sincronizar_tabelas(forcar=forcar)
        if execucao is None:
            self.stdout.write("Tabelas já estão na versão atual da base.")
        else:
            self.stdout.write(f"Concluída: {execucao.contagens}")
