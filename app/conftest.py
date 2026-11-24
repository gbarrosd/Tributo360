import itertools

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from contas.models import Assinatura, Escritorio, Membership, Usuario
from contas.papeis import Papel
from core.validators import _PESOS_CNPJ_1, _PESOS_CNPJ_2, _dv_mod11
from empresas.models import Empresa

SENHA = "SenhaForte123"


def gerar_cnpj(base):
    """CNPJ válido a partir de 12 caracteres (0-9, A-Z)."""
    valores = [ord(c) - 48 for c in base]
    dv1 = _dv_mod11(valores, _PESOS_CNPJ_1)
    dv2 = _dv_mod11(valores + [dv1], _PESOS_CNPJ_2)
    return f"{base}{dv1}{dv2}"


# CNPJs válidos e distintos para os testes, alternando numérico e alfanumérico.
CNPJS = (gerar_cnpj(f"{'AB' if n % 2 else '00'}{n:06d}0001") for n in itertools.count(1))


@pytest.fixture(autouse=True)
def _limpa_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def criar_usuario(db):
    contador = itertools.count(1)

    def _criar(email=None, nome="Pessoa", senha=SENHA):
        email = email or f"pessoa{next(contador)}@exemplo.com.br"
        return Usuario.objects.create_user(email=email, password=senha, nome=nome)

    return _criar


@pytest.fixture
def criar_escritorio(db, criar_usuario):
    """Cria escritório com assinatura e um dono. Devolve (escritorio, dono, membership_do_dono)."""

    def _criar(razao_social="Escritório", dono=None, plano="trial"):
        escritorio = Escritorio.objects.create(razao_social=razao_social, documento="11222333000181")
        Assinatura.criar_para(escritorio, plano)
        dono = dono or criar_usuario()
        membership = Membership.objects.create(escritorio=escritorio, usuario=dono, papel=Papel.DONO)
        return escritorio, dono, membership

    return _criar


@pytest.fixture
def adicionar_membro(criar_usuario):
    def _adicionar(escritorio, papel=Papel.CONTADOR, usuario=None, acesso_todas_empresas=True):
        usuario = usuario or criar_usuario()
        return Membership.objects.create(
            escritorio=escritorio, usuario=usuario, papel=papel, acesso_todas_empresas=acesso_todas_empresas
        )

    return _adicionar


@pytest.fixture
def criar_empresa(db):
    def _criar(escritorio, **extra):
        dados = {
            "cnpj": next(CNPJS),
            "razao_social": "Empresa Teste Ltda",
            "regime": Empresa.Regime.REGULAR,
            "crt": Empresa.CRT.NORMAL,
            "cnae_principal": "6920601",
            "uf": "SP",
            "municipio_ibge": "3550308",
        }
        dados.update(extra)
        return Empresa.objects.create(escritorio=escritorio, **dados)

    return _criar


@pytest.fixture
def cliente_de():
    """APIClient logado como o usuário da membership, com o escritório dela no cabeçalho."""

    def _cliente(membership):
        cliente = APIClient()
        cliente.force_login(membership.usuario)
        cliente.credentials(HTTP_X_ESCRITORIO=str(membership.escritorio_id))
        return cliente

    return _cliente
