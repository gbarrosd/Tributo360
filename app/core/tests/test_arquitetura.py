"""Teste de arquitetura: views e serializers não consultam modelos de tenant sem escopo.

Procura `<Modelo>.objects`, `<Modelo>._default_manager` e `<Modelo>._base_manager` para todo modelo que
herda de `TenantModel`. O acesso certo é `<Modelo>.tenant(request)`. Regras que precisam olhar o
escritório inteiro ficam em `servicos.py`, recebendo o escritório da membership de forma explícita.
"""

import ast
from pathlib import Path

from django.apps import apps
from django.conf import settings

from core.tenancy import TenantModel

ARQUIVOS_VIGIADOS = ("views.py", "serializers.py")
GERENCIADORES_PROIBIDOS = {"objects", "_default_manager", "_base_manager"}


def _modelos_de_tenant():
    return {m.__name__ for m in apps.get_models() if issubclass(m, TenantModel)}


def _arquivos():
    for caminho in Path(settings.BASE_DIR).rglob("*.py"):
        if "tests" in caminho.parts or "migrations" in caminho.parts:
            continue
        if caminho.name in ARQUIVOS_VIGIADOS or caminho.parent.name in {"views", "serializers"}:
            yield caminho


def _violacoes(caminho, modelos):
    arvore = ast.parse(caminho.read_text(), filename=str(caminho))
    for no in ast.walk(arvore):
        if (
            isinstance(no, ast.Attribute)
            and no.attr in GERENCIADORES_PROIBIDOS
            and isinstance(no.value, ast.Name)
            and no.value.id in modelos
        ):
            yield f"{caminho.relative_to(settings.BASE_DIR)}:{no.lineno} {no.value.id}.{no.attr}"


def test_existem_modelos_de_tenant():
    assert {
        "Empresa",
        "Membership",
        "Convite",
        "Auditoria",
        "Simulacao",
        "SimulacaoItem",
        "ItemCatalogo",
        "AliquotaEfetivaSN",
    } <= _modelos_de_tenant()


def test_views_e_serializers_nao_consultam_sem_escopo():
    modelos = _modelos_de_tenant()
    encontrados = [v for caminho in _arquivos() for v in _violacoes(caminho, modelos)]
    assert not encontrados, "Use Model.tenant(request):\n" + "\n".join(encontrados)


def test_detector_pega_violacao(tmp_path, settings):
    """Garante que o teste acima não passa por estar cego."""
    settings.BASE_DIR = tmp_path
    (tmp_path / "views.py").write_text("from empresas.models import Empresa\nEmpresa.objects.all()\n")
    violacoes = [v for caminho in _arquivos() for v in _violacoes(caminho, _modelos_de_tenant())]
    assert violacoes == ["views.py:2 Empresa.objects"]


def test_todo_modelo_de_negocio_e_tenant():
    """Toda tabela dos apps do produto herda de TenantModel, salvo as globais listadas aqui."""
    # Globais: contas e as tabelas de referência dos motores (versões, alíquotas nominais, tabelas oficiais).
    globais = {
        "Usuario",
        "Escritorio",
        "Assinatura",
        "VersaoMotor",
        "AliquotaNominal",
        "SincronizacaoTabela",
        "ItemTabela",
    }
    apps_do_produto = {"contas", "empresas", "auditoria", "engines", "simulacoes", "catalogo", "web"}
    fora = [
        m.__name__
        for m in apps.get_models()
        if m._meta.app_label in apps_do_produto
        and not issubclass(m, TenantModel)
        and m.__name__ not in globais
        and not m._meta.auto_created
    ]
    assert not fora, f"Modelos sem escopo de escritório: {fora}"


def test_telas_web_estao_vigiadas():
    """As views das telas (web/views/*.py) também passam pelo detector de acesso sem escopo."""
    vigiados = {str(c.relative_to(settings.BASE_DIR)) for c in _arquivos()}
    assert {"web/views/simulacoes.py", "web/views/catalogo.py", "web/views/empresas.py"} <= vigiados
