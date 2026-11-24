"""Cria um escritório (trial) com um usuário existente como dono.

Uso típico, logo depois do `createsuperuser`:

    python manage.py criar_escritorio --email voce@exemplo.com \
        --razao-social "Meu Escritório" --documento 11222333000181

Sem --razao-social/--documento, cria "Escritório de <e-mail>" com um CNPJ de teste válido (só para
desenvolvimento). Pela tela, o mesmo caminho é /escritorios/ (aparece para quem não tem escritório).
"""

from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from contas.models import Usuario
from contas.servicos import criar_escritorio_para
from core.validators import limpar_documento, validar_cpf_ou_cnpj

CNPJ_DE_TESTE = "11222333000181"


class Command(BaseCommand):
    help = "Cria um escritório com o usuário informado como dono (ex.: para o superuser recém-criado)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--razao-social", default="")
        parser.add_argument("--documento", default="", help="CNPJ ou CPF do titular, com ou sem máscara.")

    def handle(self, *args, email, razao_social, documento, **options):
        usuario = Usuario.objects.filter(email__iexact=email.strip()).first()
        if usuario is None:
            raise CommandError(f"Não existe usuário com o e-mail {email}. Crie com `createsuperuser` antes.")
        documento = limpar_documento(documento or CNPJ_DE_TESTE)
        try:
            validar_cpf_ou_cnpj(documento)
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        requisicao = SimpleNamespace(user=usuario, META={}, membership=None)
        membership = criar_escritorio_para(
            requisicao, usuario, razao_social or f"Escritório de {usuario.email}", documento
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Escritório {membership.escritorio.razao_social} ({membership.escritorio_id}) criado; "
                f"{usuario.email} é o dono."
            )
        )
