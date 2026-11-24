import itertools
import json
from pathlib import Path

import httpx
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from contas.models import Assinatura, Escritorio, Membership, Usuario
from contas.papeis import Papel
from core.validators import _PESOS_CNPJ_1, _PESOS_CNPJ_2, _dv_mod11
from empresas.models import Empresa
from engines.http import ClienteMotor
from engines.models import VersaoMotor
from simulacoes.models import Simulacao

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


@pytest.fixture
def versao_motor(db):
    return VersaoMotor.objects.create(motor="regime_regular", versao_app="1.7.2", versao_db="V0073")


@pytest.fixture
def criar_simulacao(versao_motor):
    """Simulação já gravada (sem chamar motor), para testes de listagem e isolamento."""

    def _criar(empresa, **extra):
        dados = {
            "escritorio": empresa.escritorio,
            "empresa": empresa,
            "tipo": Simulacao.Tipo.REGULAR,
            "data_fato_gerador": "2026-06-10",
            "versao_motor": versao_motor,
            "situacao": Simulacao.Situacao.CALCULADO,
            "simulado": False,
            "entrada": {},
            "payload_enviado": {},
            "resposta_bruta": {},
            "total_base_calculo": "1000.00",
            "total_cbs": "9.00",
            "total_ibs": "1.00",
            "total_imposto_seletivo": "0.00",
        }
        dados.update(extra)
        return Simulacao.objects.create(**dados)

    return _criar


# --- Motores falsos -------------------------------------------------------------------------------

RESPOSTAS_GRAVADAS = Path(__file__).resolve().parent / "engines" / "tests" / "respostas"


def resposta_gravada(nome):
    """Entrada, headers e resposta reais de um motor, gravados dos motores rodando (ver respostas/)."""
    return json.loads((RESPOSTAS_GRAVADAS / nome).read_text())


class MotoresFalsos:
    """Roteador de respostas para os clientes dos motores, sem rede.

    `rotas[(host, metodo, caminho)]` é um `httpx.Response` ou uma função `request -> Response`.
    Cada requisição recebida fica em `recebidas`.
    """

    VERSOES = {
        "rr": {"versaoApp": "1.7.2", "versaoDb": "V0073", "dataVersaoDb": "2026-11-09", "ambiente": "offline"},
        "sn": {"versaoApp": "1.0.2", "versaoDb": "V0073", "dataVersaoDb": "2026-11-09", "ambiente": "offline"},
    }

    def __init__(self):
        self.recebidas = []
        self.fora_do_ar = set()
        self.rotas = {
            ("rr", "GET", "/api/calculadora/dados-abertos/versao"): httpx.Response(200, json=self.VERSOES["rr"]),
            ("sn", "GET", "/api/versao"): httpx.Response(200, json=self.VERSOES["sn"]),
            ("df", "GET", "/health/ready"): httpx.Response(200, json={"status": "ok"}),
        }

    def gravada(self, host, caminho, nome, status=200):
        """Responde com uma resposta gravada de `respostas/`."""
        dados = resposta_gravada(nome)
        self.rotas[(host, "POST", caminho)] = httpx.Response(status, json=dados["resposta"], headers=dados["headers"])

    def ultima(self, host=None, metodo="POST"):
        candidatas = [r for r in self.recebidas if (host is None or r.url.host == host) and r.method == metodo]
        return candidatas[-1] if candidatas else None

    def json_da_ultima(self, host=None):
        return json.loads(self.ultima(host).content)

    def __call__(self, request):
        self.recebidas.append(request)
        if request.url.host in self.fora_do_ar:
            raise httpx.ConnectError("recusado", request=request)
        rota = self.rotas.get((request.url.host, request.method, request.url.path))
        if rota is None:
            return httpx.Response(404, json={"detail": f"sem rota falsa para {request.method} {request.url.path}"})
        return rota(request) if callable(rota) else rota


@pytest.fixture
def motores_falsos(monkeypatch, settings):
    settings.MOTORES = {
        "regime_regular": {"url": "http://rr", "versao_path": "/api/calculadora/dados-abertos/versao"},
        "simples": {"url": "http://sn", "versao_path": "/api/versao"},
        "danfse": {"url": "http://df", "versao_path": "/health/ready", "versao_fixa": "1.0.0"},
    }
    falsos = MotoresFalsos()

    def cliente(self):
        return httpx.Client(timeout=self._timeout, transport=httpx.MockTransport(falsos), trust_env=False)

    monkeypatch.setattr(ClienteMotor, "_cliente", cliente)
    return falsos
