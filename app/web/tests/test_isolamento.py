"""Isolamento nas telas: o escritório A não abre nem altera, pela URL, nada do escritório B.

Mesma regra da API (core/tests/test_cross_tenant.py): objeto de outro escritório responde 404.
`test_toda_tela_com_id_esta_na_matriz` falha quando alguém cria uma tela nova com id na URL e esquece
de pô-la aqui.
"""

import pytest
from django.test import Client
from django.urls import reverse

from catalogo.models import ItemCatalogo
from simulacoes.models import AliquotaEfetivaSN
from web.urls import urlpatterns

# nome da rota → (objeto do escritório B, métodos a tentar)
MATRIZ = {
    "empresa": ("empresa", ["get"]),
    "empresa-editar": ("empresa", ["get", "post"]),
    "catalogo": ("empresa", ["get"]),
    "item-novo": ("empresa", ["get", "post"]),
    "catalogo-importar": ("empresa", ["get", "post"]),
    "catalogo-aprovar": ("empresa", ["post"]),
    "item-editar": ("item", ["get", "post"]),
    "item-revalidar": ("item", ["post"]),
    "item-inativar": ("item", ["post"]),
    "aliquotas": ("simples", ["get"]),
    "aliquotas-nova": ("simples", ["get", "post"]),
    "aliquotas-editar": ("aliquota", ["get", "post"]),
    "aliquotas-remover": ("aliquota", ["post"]),
    "simular": ("empresa", ["get", "post"]),
    "simulacao": ("simulacao", ["get"]),
}


@pytest.fixture
def cenario(criar_escritorio, criar_empresa, criar_item, criar_aliquotas_sn, criar_simulacao):
    _, dono_a, _ = criar_escritorio("A")
    escritorio_b, _, _ = criar_escritorio("B")
    empresa_b = criar_empresa(escritorio_b)
    simples_b = criar_empresa(escritorio_b, regime="simples", crt=1)
    cliente = Client()
    cliente.force_login(dono_a)
    return {
        "cliente": cliente,
        "objetos": {
            "empresa": empresa_b,
            "simples": simples_b,
            "item": criar_item(empresa_b),
            "aliquota": criar_aliquotas_sn(simples_b),
            "simulacao": criar_simulacao(empresa_b),
        },
    }


def test_toda_tela_com_id_esta_na_matriz():
    com_id = {p.name for p in urlpatterns if "<uuid:pk>" in str(p.pattern)}
    assert com_id == set(MATRIZ), "Inclua a tela nova na matriz de isolamento."


@pytest.mark.parametrize("nome", sorted(MATRIZ))
def test_objeto_de_outro_escritorio_responde_404(cenario, nome):
    chave, metodos = MATRIZ[nome]
    url = reverse(f"web:{nome}", args=[cenario["objetos"][chave].pk])
    for metodo in metodos:
        resposta = getattr(cenario["cliente"], metodo)(url, {"itens": [str(cenario["objetos"]["item"].pk)]})
        assert resposta.status_code == 404, (nome, metodo, resposta.status_code)


def test_objetos_de_b_continuam_intactos(cenario):
    for nome, (chave, metodos) in MATRIZ.items():
        if "post" in metodos:
            url = reverse(f"web:{nome}", args=[cenario["objetos"][chave].pk])
            cenario["cliente"].post(url, {"itens": [str(cenario["objetos"]["item"].pk)], "razao_social": "X"})
    item = ItemCatalogo.objects.get(pk=cenario["objetos"]["item"].pk)
    assert item.ativo and item.status_classificacao == "pendente"
    assert AliquotaEfetivaSN.objects.filter(pk=cenario["objetos"]["aliquota"].pk).exists()


def test_comparar_com_simulacao_de_b_responde_404(cenario, criar_simulacao):
    pk = cenario["objetos"]["simulacao"].pk
    url = reverse("web:comparar") + f"?ids={pk}&ids={pk}"
    assert cenario["cliente"].get(url).status_code == 404


def test_historico_filtrado_pela_empresa_de_b_vem_vazio(cenario):
    url = reverse("web:historico") + f"?empresa={cenario['objetos']['empresa'].pk}"
    pagina = cenario["cliente"].get(url).content.decode()
    assert str(cenario["objetos"]["simulacao"].pk) not in pagina


def test_duplicar_simulacao_de_b_responde_404(criar_escritorio, criar_empresa, cenario):
    escritorio_a = criar_escritorio("A2")
    _, dono, _ = escritorio_a
    empresa_a = criar_empresa(escritorio_a[0])
    cliente = Client()
    cliente.force_login(dono)
    url = reverse("web:simular", args=[empresa_a.pk]) + f"?a_partir_de={cenario['objetos']['simulacao'].pk}"
    assert cliente.get(url).status_code == 404
